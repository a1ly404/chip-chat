"""JSONL persistence for threads, agents, and rooms."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip.config import data_dir


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def thread_path(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return data_dir() / "threads" / f"{safe}.jsonl"


def agent_registry_path() -> Path:
    return data_dir() / "agents.json"


def load_agents() -> dict[str, dict[str, Any]]:
    path = agent_registry_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def save_agents(agents: dict[str, dict[str, Any]]) -> None:
    path = agent_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(agents, indent=2) + "\n", encoding="utf-8")


def load_room_history(room: str, *, limit: int = 10) -> list[dict[str, str]]:
    """Last ``limit`` user/assistant records from the room transcript, in order."""
    if limit <= 0:
        return []
    path = thread_path(room)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        role = rec.get("role")
        if role not in ("user", "assistant"):
            continue
        content = rec.get("content")
        if not isinstance(content, str) or not content:
            continue
        rows.append(rec)
    out: list[dict[str, str]] = []
    for rec in rows[-limit:]:
        role = str(rec["role"])
        content = rec["content"]
        agent = rec.get("agent")
        if isinstance(agent, str) and agent.strip() and agent != "user":
            marker = f"[{agent}]"
            if not content.startswith(f"{marker} "):
                content = f"{marker} {content}"
        out.append({"role": role, "content": content})
    return out


SESSION_OPEN = "session_open"
SESSION_CLOSE = "session_close"


def _read_thread_records(room: str) -> list[dict[str, Any]]:
    path = thread_path(room)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def session_segment_rows(room: str) -> list[dict[str, Any]]:
    """Transcript rows since the last session_close marker (excludes markers)."""
    rows = _read_thread_records(room)
    start = 0
    for idx, rec in enumerate(rows):
        if rec.get("role") == "room" and rec.get("event") == SESSION_CLOSE:
            start = idx + 1
    segment: list[dict[str, Any]] = []
    for rec in rows[start:]:
        if rec.get("role") == "room" and rec.get("event") in (SESSION_OPEN, SESSION_CLOSE):
            continue
        segment.append(rec)
    return segment


def mark_session_closed(room: str) -> None:
    append_jsonl(
        thread_path(room),
        {"ts": _ts(), "role": "room", "event": SESSION_CLOSE},
    )


def log_message(
    thread: str,
    role: str,
    content: str,
    *,
    agent: str | None = None,
    model: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    record: dict[str, Any] = {
        "ts": _ts(),
        "role": role,
        "content": content,
    }
    if agent:
        record["agent"] = agent
    if model:
        record["model"] = model
    if extra:
        record.update(extra)
    append_jsonl(thread_path(thread), record)
