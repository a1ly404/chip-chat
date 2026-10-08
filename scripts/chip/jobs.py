"""PM orchestrator job board — durable slices under jobs/<room>/<id>.json."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip.config import data_dir
from chip import store

SCHEMA_VERSION = 1
_JOB_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.IGNORECASE)
_STATUSES = frozenset({"open", "done", "blocked", "escalated"})


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_dir(room: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in room)
    return data_dir() / "jobs" / safe


def job_path(room: str, job_id: str) -> Path:
    if not _JOB_ID.match(job_id):
        raise ValueError(f"invalid job id: {job_id!r}")
    return job_dir(room) / f"{job_id}.json"


def _validate(doc: dict[str, Any]) -> dict[str, Any]:
    if int(doc.get("schema", 0)) != SCHEMA_VERSION:
        raise ValueError("job schema mismatch")
    for key in ("id", "owner_persona", "goal", "acceptance", "stop"):
        if not isinstance(doc.get(key), str) or not str(doc[key]).strip():
            raise ValueError(f"job missing or empty field: {key}")
    status = str(doc.get("status") or "open").lower()
    if status not in _STATUSES:
        raise ValueError(f"invalid job status: {status}")
    artifacts = doc.get("artifacts")
    if artifacts is None:
        doc["artifacts"] = []
    elif not isinstance(artifacts, list):
        raise ValueError("job artifacts must be a list")
    doc["status"] = status
    return doc


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def create_job(
    room: str,
    *,
    owner_persona: str,
    goal: str,
    acceptance: str,
    stop: str,
    job_id: str | None = None,
    artifacts: list[str] | None = None,
) -> dict[str, Any]:
    jid = (job_id or new_job_id()).strip().lower()
    doc: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "id": jid,
        "room": room,
        "owner_persona": owner_persona.strip().lower(),
        "goal": goal.strip(),
        "acceptance": acceptance.strip(),
        "stop": stop.strip(),
        "artifacts": list(artifacts or []),
        "status": "open",
        "created_at": _ts(),
        "updated_at": _ts(),
    }
    _validate(doc)
    path = job_path(room, jid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    mirror_job_line(room, jid, "created", owner=owner_persona)
    return doc


def load_job(room: str, job_id: str) -> dict[str, Any]:
    path = job_path(room, job_id)
    if not path.is_file():
        raise FileNotFoundError(f"job not found: {room}/{job_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("job file must be an object")
    return _validate(raw)


def save_job(doc: dict[str, Any]) -> dict[str, Any]:
    doc = _validate(dict(doc))
    doc["updated_at"] = _ts()
    room = str(doc["room"])
    jid = str(doc["id"])
    job_path(room, jid).write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return doc


def list_jobs(room: str, *, status: str | None = None) -> list[dict[str, Any]]:
    root = job_dir(room)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            doc = _validate(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError, OSError):
            continue
        if status is not None and doc.get("status") != status.lower():
            continue
        out.append(doc)
    return out


def active_job(room: str, owner_persona: str = "pm") -> dict[str, Any] | None:
    """Most recently updated open job for the room (default owner pm)."""
    open_jobs = [
        j
        for j in list_jobs(room, status="open")
        if str(j.get("owner_persona", "")).lower() == owner_persona.lower()
    ]
    if not open_jobs:
        return None
    open_jobs.sort(key=lambda j: str(j.get("updated_at") or j.get("created_at") or ""), reverse=True)
    return open_jobs[0]


def set_job_status(room: str, job_id: str, status: str, *, note: str = "") -> dict[str, Any]:
    doc = load_job(room, job_id)
    st = status.strip().lower()
    if st not in _STATUSES:
        raise ValueError(f"invalid status: {status}")
    doc["status"] = st
    if note.strip():
        doc.setdefault("notes", []).append({"ts": _ts(), "text": note.strip()})
    save_job(doc)
    mirror_job_line(room, job_id, f"status:{st}", owner=str(doc.get("owner_persona") or "pm"))
    return doc


def mirror_job_line(room: str, job_id: str, event: str, *, owner: str = "pm") -> None:
    """One-line mirror in the room transcript for operator visibility."""
    content = f"job:{job_id} {event}"
    store.log_message(
        room,
        "relay",
        content,
        agent=owner,
        extra={"pm_job": {"id": job_id, "event": event}},
    )


def parse_job_done_line(text: str) -> str | None:
    """``job:done <id>`` in a pm reply closes the board row."""
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("job:done "):
            return line.split(None, 1)[1].strip().lower()
    return None
