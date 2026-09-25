"""Explicit ``delegate:<persona> <task>`` handoffs (relay-controlled, not AUTO)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from chip import config, store

MAX_DELEGATE_HOPS = 3
_DELEGATE_LINE = re.compile(
    r"^delegate:(?P<persona>[A-Za-z0-9][A-Za-z0-9_.-]{0,31})\s+(?P<task>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DelegateRequest:
    persona: str
    task: str


def known_persona_ids() -> frozenset[str]:
    return frozenset(config.load_personas().keys())


def parse_delegate_line(line: str) -> DelegateRequest | None:
    """Parse one line like ``delegate:dev fix the flake``."""
    wanted = line.strip()
    if not wanted:
        return None
    match = _DELEGATE_LINE.match(wanted)
    if not match:
        return None
    persona = match.group("persona").strip().lower()
    task = match.group("task").strip()
    if not task:
        return None
    if persona not in known_persona_ids():
        return None
    return DelegateRequest(persona=persona, task=task)


def parse_delegate(text: str) -> DelegateRequest | None:
    """Return the first valid ``delegate:`` line in a multi-line reply."""
    if not isinstance(text, str):
        return None
    for line in text.splitlines():
        found = parse_delegate_line(line)
        if found is not None:
            return found
    return None


def max_logged_delegate_hop(room_id: str) -> int:
    """Highest ``relay_handoff.hop`` recorded in the room transcript."""
    path = store.thread_path(room_id)
    if not path.is_file():
        return 0
    peak = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        handoff = rec.get("relay_handoff")
        if not isinstance(handoff, dict):
            continue
        hop = handoff.get("hop")
        if isinstance(hop, int) and hop > peak:
            peak = hop
    return peak
