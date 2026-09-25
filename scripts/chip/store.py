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
