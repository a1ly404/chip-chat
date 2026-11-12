"""Planned maintenance windows: tell a planned restart apart from an unplanned one.

Deploy, rollback and recreate scripts bounce the engine's containers on purpose. A watcher that
only sees "the relay restarted and the ticks went quiet" cannot tell that from a crash, so every
planned deploy used to page as a PROBLEM and then post RECOVERED. This module is the contract
between the two sides:

* the script that is about to restart things appends a ``start`` row (receipt id, reason, TTL)
  and an ``end`` row when it is done (``python -m chip.maintenance start|end ...``);
* a watcher asks :func:`covering` whether a restart timestamp falls inside a marked window and,
  if so, logs it as planned instead of raising a problem. Restarts outside every window still
  alert exactly as before.

Rows live in ``<data dir>/chip_state/maintenance.jsonl`` (``CHIP_MAINTENANCE_PATH`` overrides).
The file is append-only; a window is the fold of its rows by receipt id.

Fail-safe rules, so a marker can never hide a real outage for long:

* a window that never gets an ``end`` row closes on its own at ``start + ttl`` (TTL is clamped
  to :data:`MAX_TTL_S`);
* only the restart-shaped findings a watcher chooses to check are affected; everything else
  still pages;
* writing a marker is refused inside a background job (``CHIP_BGJOB_ID`` / ``CHIP_JOB_ACTOR``),
  so job code cannot open a window to mute its own alerts.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

PATH_ENV = "CHIP_MAINTENANCE_PATH"
FILE_NAME = "maintenance.jsonl"
DEFAULT_TTL_S = 2 * 3600
MIN_TTL_S = 60
MAX_TTL_S = 12 * 3600
DEFAULT_GRACE_S = 300.0
MAX_ROWS = 2000
_RECEIPT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,159}$")
_IN_JOB_MARKERS = ("CHIP_BGJOB_ID", "CHIP_JOB_ACTOR")


class MaintenanceError(ValueError):
    """Bad marker input, or a marker write refused (for example inside a job)."""


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def marker_path(environ: Mapping[str, str] | None = None) -> Path:
    env = _env(environ)
    raw = str(env.get(PATH_ENV, "") or "").strip()
    if raw:
        return Path(raw)
    root = str(env.get("CHIP_CHAT_DATA_DIR", "") or "").strip()
    if root:
        return Path(root).expanduser() / "chip_state" / FILE_NAME
    from chip.config import data_dir

    return data_dir() / "chip_state" / FILE_NAME


def _in_job(environ: Mapping[str, str] | None) -> str | None:
    sources = [os.environ] if environ is None else [environ, os.environ]
    for src in sources:
        for marker in _IN_JOB_MARKERS:
            if str(src.get(marker, "") or "").strip():
                return marker
    return None


def _check_receipt(receipt: str) -> str:
    rid = str(receipt or "").strip()
    if not _RECEIPT.match(rid):
        raise MaintenanceError(f"bad receipt id {rid[:40]!r}: use 1-160 of [A-Za-z0-9._:@/-]")
    return rid


def _append(row: Mapping[str, Any], environ: Mapping[str, str] | None) -> dict[str, Any]:
    marker = _in_job(environ)
    if marker:
        raise MaintenanceError(f"in-job refusal: maintenance markers cannot be written inside a job ({marker} set)")
    path = marker_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(row)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(out, sort_keys=True) + "\n")
    return out


def start(
    receipt: str,
    *,
    reason: str = "",
    ttl_s: float = DEFAULT_TTL_S,
    now: float | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Open (or re-open) the window ``receipt``. ``now`` may be in the past to backdate the start."""
    rid = _check_receipt(receipt)
    ttl = min(max(float(ttl_s), MIN_TTL_S), MAX_TTL_S)
    ts = time.time() if now is None else float(now)
    return _append(
        {"event": "start", "receipt": rid, "ts": round(ts, 3), "ttl_s": ttl, "reason": str(reason or "")[:200]},
        environ,
    )


def end(
    receipt: str,
    *,
    status: str = "ok",
    now: float | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Close the window ``receipt`` (an ``end`` without a ``start`` is recorded but opens nothing)."""
    rid = _check_receipt(receipt)
    ts = time.time() if now is None else float(now)
    return _append({"event": "end", "receipt": rid, "ts": round(ts, 3), "status": str(status or "ok")[:40]}, environ)


def _rows(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-MAX_ROWS:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def windows(*, now: float | None = None, environ: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Every known window, oldest first: ``{receipt, start, end, state, reason, status}``.

    ``state`` is ``open`` (no end row, TTL not reached), ``closed`` (end row) or ``expired``
    (no end row, TTL passed; ``end`` is then ``start + ttl``).
    """
    now = time.time() if now is None else float(now)
    by_id: dict[str, dict[str, Any]] = {}
    for row in _rows(marker_path(environ)):
        rid = str(row.get("receipt") or "")
        try:
            ts = float(row.get("ts"))
        except (TypeError, ValueError):
            continue
        if not rid or not math.isfinite(ts):
            continue
        if row.get("event") == "start":
            try:
                ttl = min(max(float(row.get("ttl_s", DEFAULT_TTL_S)), MIN_TTL_S), MAX_TTL_S)
            except (TypeError, ValueError):
                ttl = float(DEFAULT_TTL_S)
            prev = by_id.get(rid)
            first = min(ts, prev["start"]) if prev and prev.get("end_ts") is None else ts
            by_id[rid] = {"receipt": rid, "start": first, "ttl_s": ttl, "end_ts": None,
                          "reason": str(row.get("reason") or ""), "status": ""}
        elif row.get("event") == "end":
            w = by_id.get(rid)
            if w is not None and w.get("end_ts") is None and ts >= w["start"]:
                w["end_ts"] = ts
                w["status"] = str(row.get("status") or "ok")
    out: list[dict[str, Any]] = []
    for w in sorted(by_id.values(), key=lambda x: x["start"]):
        hard_end = w["start"] + w["ttl_s"]
        if w["end_ts"] is not None:
            stop, state = min(w["end_ts"], hard_end), "closed"
        elif now < hard_end:
            stop, state = hard_end, "open"
        else:
            stop, state = hard_end, "expired"
        out.append({"receipt": w["receipt"], "start": w["start"], "end": stop, "state": state,
                    "reason": w["reason"], "status": w["status"]})
    return out


def covering(
    ts: float,
    *,
    grace_s: float = DEFAULT_GRACE_S,
    now: float | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """The newest window with ``start - grace <= ts <= end + grace``, else None."""
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return None
    if t <= 0 or not math.isfinite(t):
        return None
    g = max(0.0, float(grace_s))
    hit = None
    for w in windows(now=now, environ=environ):
        if w["start"] - g <= t <= w["end"] + g:
            hit = w
    return hit


def active(*, now: float | None = None, environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """The newest window that is open at ``now`` (end row not written yet, TTL not reached)."""
    now = time.time() if now is None else float(now)
    hit = None
    for w in windows(now=now, environ=environ):
        if w["state"] == "open" and w["start"] <= now:
            hit = w
    return hit


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m chip.maintenance", description="Planned maintenance window markers")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start", help="open a window before a planned restart")
    s.add_argument("--receipt", required=True)
    s.add_argument("--reason", default="")
    s.add_argument("--ttl", type=float, default=DEFAULT_TTL_S, help=f"seconds (clamped {MIN_TTL_S}..{MAX_TTL_S})")
    s.add_argument("--at", type=float, default=None, help="unix start time (default now; may backdate)")
    e = sub.add_parser("end", help="close a window after the restart is done")
    e.add_argument("--receipt", required=True)
    e.add_argument("--status", default="ok")
    sub.add_parser("status", help="print known windows as JSON")
    args = ap.parse_args(list(argv) if argv is not None else None)
    try:
        if args.cmd == "start":
            row = start(args.receipt, reason=args.reason, ttl_s=args.ttl, now=args.at)
        elif args.cmd == "end":
            row = end(args.receipt, status=args.status)
        else:
            print(json.dumps({"path": str(marker_path()), "windows": windows()[-20:]}, indent=1))
            return 0
    except MaintenanceError as exc:
        print(f"maintenance: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(row, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
