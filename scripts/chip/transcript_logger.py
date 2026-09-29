"""Timestamped JSONL transcript writer for the RAZ-335 ops A/B session.

Rows land in ~/appdata/chip-chat/threads/ops_raza335/<arm>.jsonl with a
raising-append (fail-closed) write; the transcripts are the grading corpus,
so any dropped row must fail the session loudly, not silently shrink.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.home() / "appdata" / "chip-chat" / "threads" / "ops_raza335"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class TranscriptLogger:
    def __init__(self, arm: str, root: Path = ROOT) -> None:
        self.arm = arm
        self.root = Path(root)
        self.path = self.root / f"{arm}.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)

    def event(self, kind: str, actor: str, content: str, **fields) -> dict:
        row = {
            "ts": _utcnow(),
            "arm": self.arm,
            "kind": kind,
            "actor": actor,
            "content": content,
        }
        row.update({k: v for k, v in fields.items() if v is not None})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return row
