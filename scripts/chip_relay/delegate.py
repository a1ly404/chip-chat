"""Explicit ``delegate:<persona> <task>`` handoffs (relay-controlled, not AUTO)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from chip import config, store

MAX_DELEGATE_HOPS = 3
# Nested ``delegate:`` hops within one handoff chain (one human/inject turn → webhook follow-ups).
_chain_depth: dict[str, int] = {}
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


def reset_delegate_chain(room_id: str) -> None:
    """Start a new handoff chain (call at the beginning of each human/inject turn)."""
    _chain_depth[room_id] = 0


def next_delegate_chain_hop(room_id: str) -> int:
    """1-based hop index for the next delegate in the current chain."""
    return _chain_depth.get(room_id, 0) + 1


def record_delegate_chain_hop(room_id: str, hop: int) -> None:
    """Mark that ``hop`` was consumed (logged whether or not chip ran)."""
    _chain_depth[room_id] = hop


def max_logged_delegate_hop(room_id: str) -> int:
    """Highest ``relay_handoff.hop`` recorded in the room transcript (lifetime peak)."""
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
