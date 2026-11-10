"""Scheduled routines with append-only run history (parity P1.1).

Routines live under ``CHIP_CHAT_DATA_DIR/routines/<id>.json``; each routine has an
append-only ``<id>.history.jsonl`` ledger. All schedule math is UTC with an injected
clock, so ``due()`` / ``tick()`` are deterministic in tests and DST-safe.

Schedules:
  * ``every:<N><s|m|h|d>`` — fixed interval (e.g. ``every:15m``)
  * ``cron:<min> <hour> <dom> <month> <dow>`` — 5-field cron in UTC; supports
    ``*``, ``*/n``, ``a-b``, ``a-b/n`` and comma lists. ``dow`` 0-6 (0 = Sunday, 7 accepted).

Missed runs are caught up at most once: a routine overdue by several periods runs a
single time and its next run is scheduled strictly after ``now``.

``tick()`` is gated by ``CHIP_ROUTINES=1`` (default off) and dispatches through an
injected runner; it never executes anything itself.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from chip.config import data_dir

SCHEMA_VERSION = 1
FLAG = "CHIP_ROUTINES"
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.IGNORECASE)
_INTERVAL = re.compile(r"^every:(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_MAX_CRON_SCAN_MINUTES = 366 * 24 * 60 * 5
RUN_STATUSES = frozenset({"ok", "failed"})

Runner = Callable[[dict[str, Any]], dict[str, Any]]


class RoutinesDisabled(RuntimeError):
    """Raised when tick() is called while CHIP_ROUTINES is not enabled."""


def enabled(env: dict[str, str] | None = None) -> bool:
    src = os.environ if env is None else env
    return str(src.get(FLAG, "0")).strip().lower() in {"1", "true", "yes", "on"}


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return _utc(dt).isoformat().replace("+00:00", "Z")


def _parse_iso(raw: str) -> datetime:
    return _utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))


# ---------------------------------------------------------------- schedules


def _cron_field(spec: str, lo: int, hi: int) -> frozenset[int]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"empty cron field segment in {spec!r}")
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise ValueError(f"bad cron step: {step_s!r}")
            step = int(step_s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a_s, b_s = part.split("-", 1)
            if not (a_s.isdigit() and b_s.isdigit()):
                raise ValueError(f"bad cron range: {part!r}")
            a, b = int(a_s), int(b_s)
        elif part.isdigit():
            a = b = int(part)
        else:
            raise ValueError(f"bad cron value: {part!r}")
        if a < lo or b > hi or a > b:
            raise ValueError(f"cron value out of range {lo}-{hi}: {part!r}")
        out.update(range(a, b + 1, step))
    return frozenset(out)


def parse_schedule(schedule: str) -> dict[str, Any]:
    """Validate and normalise a schedule string. Raises ValueError on bad input."""
    s = (schedule or "").strip()
    m = _INTERVAL.match(s)
    if m:
        seconds = int(m.group(1)) * _UNIT_SECONDS[m.group(2)]
        if seconds < 60:
            raise ValueError("interval must be at least 60 seconds")
        return {"kind": "interval", "seconds": seconds}
    if s.startswith("cron:"):
        fields = s[5:].split()
        if len(fields) != 5:
            raise ValueError("cron schedule needs 5 fields: min hour dom month dow")
        sets = [_cron_field(f, lo, hi) for f, (lo, hi) in zip(fields, _CRON_RANGES)]
        dow = frozenset(0 if d == 7 else d for d in sets[4])
        return {
            "kind": "cron",
            "minute": sets[0],
            "hour": sets[1],
            "dom": sets[2],
            "month": sets[3],
            "dow": dow,
            "dom_star": fields[2] == "*",
            "dow_star": fields[4] == "*",
        }
    raise ValueError(f"unsupported schedule: {schedule!r} (use every:<N><s|m|h|d> or cron:<5 fields>)")


def _cron_matches(p: dict[str, Any], t: datetime) -> bool:
    if t.minute not in p["minute"] or t.hour not in p["hour"] or t.month not in p["month"]:
        return False
    dom_ok = t.day in p["dom"]
    dow_ok = ((t.weekday() + 1) % 7) in p["dow"]
    if p["dom_star"] or p["dow_star"]:
        return dom_ok and dow_ok
    return dom_ok or dow_ok  # classic cron: either restricted field matches


def next_after(schedule: str, after: datetime) -> datetime:
    """First fire time strictly after ``after`` (UTC)."""
    p = parse_schedule(schedule)
    after = _utc(after)
    if p["kind"] == "interval":
        return after + timedelta(seconds=p["seconds"])
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(_MAX_CRON_SCAN_MINUTES):
        if _cron_matches(p, t):
            return t
        t += timedelta(minutes=1)
    raise ValueError(f"cron schedule never fires: {schedule!r}")


# ---------------------------------------------------------------- store


def routines_dir() -> Path:
    path = data_dir() / "routines"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(routine_id: str) -> Path:
    if not _ID.match(routine_id or ""):
        raise ValueError(f"invalid routine id: {routine_id!r}")
    return routines_dir() / f"{routine_id}.json"


def history_path(routine_id: str) -> Path:
    return _path(routine_id).with_suffix(".history.jsonl")


def _save(doc: dict[str, Any]) -> None:
    path = _path(doc["id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def add(
    routine_id: str,
    *,
    schedule: str,
    kind: str,
    prompt: str = "",
    now: datetime,
    enabled_: bool = True,
) -> dict[str, Any]:
    path = _path(routine_id)
    if path.exists():
        raise FileExistsError(f"routine exists: {routine_id}")
    if not (kind or "").strip():
        raise ValueError("routine kind is required")
    parse_schedule(schedule)
    doc = {
        "schema": SCHEMA_VERSION,
        "id": routine_id,
        "schedule": schedule.strip(),
        "kind": kind.strip(),
        "prompt": prompt,
        "enabled": bool(enabled_),
        "created_at": _iso(now),
        "next_run": _iso(next_after(schedule, now)),
        "last_run": None,
        "last_status": None,
    }
    _save(doc)
    return doc


def load(routine_id: str) -> dict[str, Any]:
    path = _path(routine_id)
    if not path.is_file():
        raise FileNotFoundError(f"no routine: {routine_id}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if int(doc.get("schema", 0)) != SCHEMA_VERSION:
        raise ValueError("routine schema mismatch")
    return doc


def list_all() -> list[dict[str, Any]]:
    out = []
    for p in sorted(routines_dir().glob("*.json")):
        try:
            out.append(load(p.stem))
        except (ValueError, json.JSONDecodeError):
            continue
    return out


def set_enabled(routine_id: str, value: bool, *, now: datetime) -> dict[str, Any]:
    doc = load(routine_id)
    doc["enabled"] = bool(value)
    if value:
        # Re-enabling never back-fires stale runs.
        doc["next_run"] = _iso(next_after(doc["schedule"], now))
    _save(doc)
    return doc


def delete(routine_id: str) -> bool:
    """Remove the routine definition. Run history is kept (append-only audit)."""
    path = _path(routine_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def history(routine_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    path = history_path(routine_id)
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[-limit:] if limit else rows


def _append_history(routine_id: str, row: dict[str, Any]) -> None:
    with history_path(routine_id).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- scheduling


def due(now: datetime, routines: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Enabled routines whose next_run <= now (pure given ``routines``)."""
    now = _utc(now)
    rows = list_all() if routines is None else routines
    return [r for r in rows if r.get("enabled") and _parse_iso(r["next_run"]) <= now]


def tick(
    now: datetime,
    runner: Runner,
    *,
    clock: Callable[[], datetime] | None = None,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Run every due routine once via ``runner`` and append a history row each.

    ``runner(routine)`` returns ``{"status": "ok"|"failed", "summary": str, "spend_usd": float}``;
    exceptions become ``failed`` rows. Returns the history rows written.
    """
    if not enabled(env):
        raise RoutinesDisabled(f"{FLAG} is not enabled; refusing to run routines")
    now = _utc(now)
    clock = clock or (lambda: now)
    written: list[dict[str, Any]] = []
    for routine in due(now):
        started = _utc(clock())
        missed = 0
        nxt = _parse_iso(routine["next_run"])
        while True:
            nxt2 = next_after(routine["schedule"], nxt)
            if nxt2 > now:
                break
            missed += 1
            nxt = nxt2
            if missed > 10_000:
                break
        try:
            res = runner(dict(routine)) or {}
            status = str(res.get("status", "ok"))
            if status not in RUN_STATUSES:
                status = "failed"
            summary = str(res.get("summary", ""))[:2000]
            spend = float(res.get("spend_usd", 0.0) or 0.0)
        except Exception as exc:  # runner errors are recorded, never raised
            status, summary, spend = "failed", f"{type(exc).__name__}: {exc}"[:2000], 0.0
        ended = _utc(clock())
        row = {
            "routine_id": routine["id"],
            "kind": routine["kind"],
            "scheduled_for": routine["next_run"],
            "started_at": _iso(started),
            "ended_at": _iso(ended),
            "status": status,
            "summary": summary,
            "spend_usd": spend,
            "missed_skipped": missed,
        }
        _append_history(routine["id"], row)
        doc = load(routine["id"])
        doc["last_run"] = row["started_at"]
        doc["last_status"] = status
        doc["next_run"] = _iso(next_after(doc["schedule"], now))
        _save(doc)
        written.append(row)
    return written


def mock_runner(routine: dict[str, Any]) -> dict[str, Any]:
    """Offline runner: records what would run, spends nothing."""
    return {"status": "ok", "summary": f"mock: would run {routine['kind']} for {routine['id']}", "spend_usd": 0.0}
