"""Structured ops events -> cases (Phase 2 step 1).

Monitors POST ``/alert`` with an ``event`` object::

    {"alert_id": "...", "key": "event:<kind>", "detail": "...",
     "event": {"kind", "service", "state", "reason", "since", "source", ["synthetic"]}}

This path is deliberately inert: it only creates or updates a case row in
``<data_dir>/case_intake/event_cases.json``. It never runs alert handlers,
never posts to chat, never restarts anything and never pages.

* One case per fingerprint ``evt-<sha12(kind|service)>``.
* A non-``up`` state opens the case (or reopens a resolved one); repeats of an
  open case only bump ``count`` / ``last_seen`` (dedupe).
* ``up`` marks an open case ``resolved`` (rows are never deleted).
* The active system pack may declare an ``event_cases`` JSON surface with
  ``service_aliases`` (raw name -> canonical service) and ``ignore_services``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

SURFACE_KEY = "event_cases"
STORE_NAME = "event_cases.json"
STATES = frozenset({"down", "up", "grace", "degraded", "warn", "crit"})
HISTORY_MAX = 20
_TEXT_MAX = 160
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:()/-]{0,63}$")
_URLISH = re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_TOKENISH = re.compile(r"[A-Za-z0-9_\-]{24,}")
_LOCK = threading.Lock()


def is_event(body: object) -> bool:
    return isinstance(body, dict) and isinstance(body.get("event"), dict)


def _clean(text: object) -> str:
    s = _URLISH.sub("<url>", str(text or ""))
    s = _TOKENISH.sub("<redacted>", s)
    return " ".join(s.split())[:_TEXT_MAX]


def fingerprint(kind: str, service: str) -> str:
    digest = hashlib.sha256(f"{kind}|{service}".encode()).hexdigest()[:12]
    return f"evt-{digest}"


def load_mapping(environ: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        from chip_relay.plugins import load_pack_json_surface

        data = load_pack_json_surface(SURFACE_KEY, environ) or {}
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def canonical_service(raw: str, mapping: dict[str, Any]) -> str:
    aliases = mapping.get("service_aliases") or {}
    name = raw.strip()
    if isinstance(aliases, dict):
        name = str(aliases.get(name, aliases.get(name.lower(), name)))
    return name.lower()


def store_path(data_dir: Path | None = None) -> Path:
    if data_dir is None:
        from chip import config

        data_dir = Path(config.data_dir())
    return Path(data_dir) / "case_intake" / STORE_NAME


def load_store(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".event_cases.", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _validate(event: dict[str, Any]) -> str | None:
    for field in ("kind", "service", "state", "source"):
        value = event.get(field)
        if not isinstance(value, str) or not _NAME.match(value):
            return f"bad event field: {field}"
    if event["state"] not in STATES:
        return "bad event field: state"
    return None


def handle_event(
    body: dict[str, Any],
    environ: dict[str, str] | None = None,
    *,
    data_dir: Path | None = None,
    now: float | None = None,
    mapping: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply one event to the case store. Never posts, never runs handlers."""
    event = body.get("event") if isinstance(body, dict) else None
    if not isinstance(event, dict):
        return {"status": 400, "ok": False, "error": "missing event"}
    problem = _validate(event)
    if problem:
        return {"status": 400, "ok": False, "error": problem}
    mapping = load_mapping(environ) if mapping is None else mapping
    kind = event["kind"].lower()
    service = canonical_service(event["service"], mapping)
    state = event["state"]
    ignored = {str(s).lower() for s in mapping.get("ignore_services") or []}
    if service in ignored:
        return {"status": 202, "ok": True, "action": "ignored", "reason": "ignored service"}
    ts = time.time() if now is None else now
    fp = fingerprint(kind, service)
    path = store_path(data_dir)
    stamp = {
        "at": ts,
        "state": state,
        "reason": _clean(event.get("reason")),
        "source": event["source"],
    }
    with _LOCK:
        cases = load_store(path)
        row = cases.get(fp)
        if state == "up":
            if not row or row.get("status") != "open":
                return {"status": 202, "ok": True, "action": "noop", "case_id": fp}
            row["status"] = "resolved"
            row["resolved_at"] = ts
            action = "resolved"
        elif row and row.get("status") == "open":
            row["count"] = int(row.get("count") or 0) + 1
            action = "bumped" if row.get("state") == state else "updated"
        else:
            reopened = bool(row)
            row = {
                "case_id": fp,
                "kind": kind,
                "service": service,
                "status": "open",
                "count": 1,
                "opened_at": ts,
                "first_seen": (row or {}).get("first_seen", ts),
                "since": _clean(event.get("since")),
                "opens": int((row or {}).get("opens") or 0) + 1,
                "history": list((row or {}).get("history") or []),
            }
            action = "reopened" if reopened else "created"
        if event.get("synthetic") is True:
            row["synthetic"] = True
        row["state"] = state
        row["last_seen"] = ts
        row["last_reason"] = stamp["reason"]
        row["source"] = event["source"]
        if action != "bumped":
            row["history"] = (list(row.get("history") or []) + [stamp])[-HISTORY_MAX:]
        cases[fp] = row
        _save_store(path, cases)
    return {
        "status": 202,
        "ok": True,
        "action": action,
        "case_id": fp,
        "count": row["count"],
        "deduped": action == "bumped",
    }
