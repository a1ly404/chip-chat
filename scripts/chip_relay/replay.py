"""Format stored JSONL turns. No Discord and no OpenRouter."""

from __future__ import annotations

import json
from pathlib import Path


def load_turns(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def format_assistant(row: dict) -> str:
    if row.get("role") != "assistant":
        raise ValueError("not an assistant turn")
    agent = row.get("agent")
    if not agent:
        raise ValueError("assistant turn missing agent")
    if "model" not in row or "ts" not in row:
        raise ValueError("assistant turn missing model or ts")
    return f"{agent}: {row.get('content') or ''}"
