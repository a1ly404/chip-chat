"""Disk free-space check for the watch / alert pipeline (default off).

Reports free space on a few configured paths (default: the data dir) and turns it
into the same status-doc shape every external watch check uses::

    {check, checked_at, status, problems, warns, notes, stale_after_s, paths}

written to ``<data>/watch/checks/disk.json``. A watcher that already folds
``watch/checks/*.json`` into its verdict (problems page, warns are logged) picks
it up with no new notifier. Optionally (``CHIP_DISK_ALERTS_PATH``) a flat alert
row ``{kind, id, title, severity, detail, source}`` is appended to a JSONL file
when a path's level gets worse, so the PM tick can read it through
``CHIP_PM_ALERTS_EXTRA_PATHS``.

Levels per path (free = space available to unprivileged users):

* ``problem`` when free% < ``CHIP_DISK_PROBLEM_PCT`` (5) **or** free < ``CHIP_DISK_PROBLEM_GIB`` (2);
* ``warn`` when free% < ``CHIP_DISK_WARN_PCT`` (15) **or** free < ``CHIP_DISK_WARN_GIB`` (10);
* ``ok`` otherwise. A threshold of ``0`` disables that leg.

Finding strings are stable (no byte counts) so a known low disk dedupes like any
other finding; the numbers live in ``notes`` and ``paths``. A path that cannot be
stat'ed is a warn, never an exception: :func:`maybe_write` and :func:`run` do not
raise. Read-only apart from the status file (and the optional alert JSONL).

Flags:

* ``CHIP_DISK_CHECK=1`` - :func:`maybe_write` measures and writes the status file
  (off: it writes nothing and removes a ``disk.json`` it wrote earlier, so a
  turned-off check does not linger as "status stale");
* ``CHIP_DISK_CHECK_PATHS`` - comma / ``os.pathsep`` separated (default: data dir).

CLI: ``python -m chip.disk_health [--json] [--write] [--path P ...]``;
exit ``0`` ok, ``1`` warn, ``2`` problem.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

CHECK_NAME = "disk"
ENABLED_ENV = "CHIP_DISK_CHECK"
PATHS_ENV = "CHIP_DISK_CHECK_PATHS"
WARN_PCT_ENV = "CHIP_DISK_WARN_PCT"
WARN_GIB_ENV = "CHIP_DISK_WARN_GIB"
PROBLEM_PCT_ENV = "CHIP_DISK_PROBLEM_PCT"
PROBLEM_GIB_ENV = "CHIP_DISK_PROBLEM_GIB"
ALERTS_ENV = "CHIP_DISK_ALERTS_PATH"
STALE_ENV = "CHIP_DISK_STALE_AFTER_S"

DEFAULT_DATA_DIR = "/data/chip-chat"
DEFAULTS = {WARN_PCT_ENV: 15.0, WARN_GIB_ENV: 10.0, PROBLEM_PCT_ENV: 5.0, PROBLEM_GIB_ENV: 2.0}
DEFAULT_STALE_AFTER_S = 3 * 3600
GIB = 1024 ** 3
OK, WARN, PROBLEM = "ok", "warn", "problem"
_RANK = {OK: 0, WARN: 1, PROBLEM: 2}
_SEVERITY = {WARN: "warning", PROBLEM: "critical"}
_TRUE = frozenset({"1", "true", "yes", "on"})

Usage = Callable[[str], Any]  # shutil.disk_usage-like: .total / .free


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    return str(_env(environ).get(ENABLED_ENV, "")).strip().lower() in _TRUE


def data_dir(environ: Mapping[str, str] | None = None) -> Path:
    return Path(str(_env(environ).get("CHIP_CHAT_DATA_DIR", "")).strip() or DEFAULT_DATA_DIR)


def status_path(environ: Mapping[str, str] | None = None) -> Path:
    return data_dir(environ) / "watch" / "checks" / f"{CHECK_NAME}.json"


def paths(environ: Mapping[str, str] | None = None) -> list[str]:
    raw = str(_env(environ).get(PATHS_ENV, "")).strip()
    if not raw:
        return [str(data_dir(environ))]
    out: list[str] = []
    for part in raw.replace(os.pathsep, ",").split(","):
        p = part.strip()
        if p and p not in out:
            out.append(p)
    return out or [str(data_dir(environ))]


def _num(environ: Mapping[str, str], key: str, default: float) -> float:
    raw = str(environ.get(key, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value != value or value < 0 or value == float("inf"):
        return default
    return value


def thresholds(environ: Mapping[str, str] | None = None) -> dict[str, float]:
    env = _env(environ)
    return {key: _num(env, key, default) for key, default in DEFAULTS.items()}


def _fmt(value: float) -> str:
    return f"{value:g}"


def level(free_bytes: int, total_bytes: int, limits: Mapping[str, float]) -> str:
    """ok / warn / problem for one path (a 0 threshold disables that leg)."""
    pct = (100.0 * free_bytes / total_bytes) if total_bytes > 0 else 0.0
    gib = free_bytes / GIB

    def below(pct_key: str, gib_key: str) -> bool:
        lp, lg = limits[pct_key], limits[gib_key]
        return (lp > 0 and pct < lp) or (lg > 0 and gib < lg)

    if below(PROBLEM_PCT_ENV, PROBLEM_GIB_ENV):
        return PROBLEM
    if below(WARN_PCT_ENV, WARN_GIB_ENV):
        return WARN
    return OK


def measure(
    path_list: Iterable[str],
    environ: Mapping[str, str] | None = None,
    *,
    usage: Usage = shutil.disk_usage,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Status doc for ``path_list``. Never raises on a bad path."""
    env = _env(environ)
    limits = thresholds(env)
    now = now or datetime.now(timezone.utc)
    problems: list[str] = []
    warns: list[str] = []
    notes: list[str] = []
    rows: list[dict[str, Any]] = []
    warn_rule = f"<{_fmt(limits[WARN_PCT_ENV])}% or <{_fmt(limits[WARN_GIB_ENV])} GiB"
    problem_rule = f"<{_fmt(limits[PROBLEM_PCT_ENV])}% or <{_fmt(limits[PROBLEM_GIB_ENV])} GiB"
    for p in path_list:
        try:
            du = usage(p)
            total, free = int(du.total), int(du.free)
        except Exception as exc:  # noqa: BLE001 - a failed stat is a warn, not a crash
            warns.append(f"disk {p}: stat failed ({exc.__class__.__name__})")
            rows.append({"path": p, "level": "error", "error": exc.__class__.__name__})
            continue
        lvl = level(free, total, limits)
        pct = round(100.0 * free / total, 1) if total > 0 else 0.0
        gib = round(free / GIB, 2)
        rows.append({"path": p, "level": lvl, "total_bytes": total, "free_bytes": free,
                     "free_pct": pct, "free_gib": gib})
        notes.append(f"disk {p}: {gib:g} GiB free ({pct:g}%)")
        if lvl == PROBLEM:
            problems.append(f"disk {p}: free space below problem threshold ({problem_rule})")
        elif lvl == WARN:
            warns.append(f"disk {p}: free space below warn threshold ({warn_rule})")
    status = PROBLEM if problems else (WARN if warns else OK)
    stale = int(_num(env, STALE_ENV, float(DEFAULT_STALE_AFTER_S)))
    return {
        "check": CHECK_NAME,
        "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "problems": problems,
        "warns": warns,
        "notes": notes,
        "stale_after_s": stale,
        "thresholds": limits,
        "paths": rows,
    }


def _read_doc(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write_atomic(path: Path, doc: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(dict(doc), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def alert_rows(doc: Mapping[str, Any], previous: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Flat PM alert rows for paths whose level got worse than in ``previous``."""
    before = {str(r.get("path")): str(r.get("level")) for r in (previous or {}).get("paths") or []
              if isinstance(r, Mapping)}
    out: list[dict[str, Any]] = []
    for row in doc.get("paths") or []:
        lvl = str(row.get("level"))
        if lvl not in _SEVERITY or _RANK[lvl] <= _RANK.get(before.get(str(row.get("path")), OK), 0):
            continue
        path = str(row.get("path"))
        out.append({
            "kind": "alert",
            "id": f"disk-low:{path}",
            "title": f"Disk low on {path} ({lvl})",
            "severity": _SEVERITY[lvl],
            "detail": f"{row.get('free_gib')} GiB free ({row.get('free_pct')}%) on {path}",
            "source": "disk-health",
            "ts": doc.get("checked_at"),
        })
    return out


def run(
    environ: Mapping[str, str] | None = None,
    *,
    usage: Usage = shutil.disk_usage,
    now: datetime | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Measure, write the status file and (optionally) alert rows. Never raises."""
    env = _env(environ)
    try:
        doc = measure(paths(env), env, usage=usage, now=now)
    except Exception as exc:  # noqa: BLE001 - belt and braces: the tick must not die here
        return {"check": CHECK_NAME, "status": "error", "error": exc.__class__.__name__}
    if not write:
        return doc
    target = status_path(env)
    previous = _read_doc(target)
    try:
        _write_atomic(target, doc)
    except OSError as exc:
        doc["write_error"] = exc.__class__.__name__
        return doc
    alerts_path = str(env.get(ALERTS_ENV, "")).strip()
    if alerts_path:
        rows = alert_rows(doc, previous)
        if rows:
            try:
                ap = Path(alerts_path)
                ap.parent.mkdir(parents=True, exist_ok=True)
                with ap.open("a", encoding="utf-8") as fh:
                    for row in rows:
                        fh.write(json.dumps(row, sort_keys=True) + "\n")
                doc["alerts_written"] = len(rows)
            except OSError as exc:
                doc["alerts_error"] = exc.__class__.__name__
    return doc


def maybe_write(
    environ: Mapping[str, str] | None = None,
    *,
    usage: Usage = shutil.disk_usage,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Watch-tick hook: run when ``CHIP_DISK_CHECK`` is on, else clear our old status file."""
    env = _env(environ)
    try:
        if enabled(env):
            return run(env, usage=usage, now=now)
        target = status_path(env)
        if target.is_file() and _read_doc(target).get("check") == CHECK_NAME:
            target.unlink()
    except Exception:  # noqa: BLE001 - never break the caller's tick
        return None
    return None


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m chip.disk_health", description=__doc__.splitlines()[0])
    ap.add_argument("--path", action="append", default=None, help="path to check (repeatable)")
    ap.add_argument("--json", action="store_true", help="print the status doc as JSON")
    ap.add_argument("--write", action="store_true", help="also write <data>/watch/checks/disk.json")
    args = ap.parse_args(argv)
    env = dict(_env(environ))
    if args.path:
        env[PATHS_ENV] = ",".join(args.path)
    doc = run(env, write=args.write)
    if args.json:
        print(json.dumps(doc, indent=1, sort_keys=True))
    else:
        print(f"disk: {doc.get('status')}")
        for line in (doc.get("problems") or []) + (doc.get("warns") or []) + (doc.get("notes") or []):
            print(f"  {line}")
    return {OK: 0, WARN: 1, PROBLEM: 2}.get(str(doc.get("status")), 1)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
