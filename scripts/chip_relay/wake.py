"""Wake a persona via @mention or ``persona:`` prefix (Grok-style room routing)."""

from __future__ import annotations

import re

from chip_relay.delegate import DelegateRequest, known_persona_ids, parse_delegate_line

_WAKE_AT = re.compile(
    r"^@(?P<persona>[A-Za-z0-9][A-Za-z0-9_.-]{0,31})\b\s*:?\s+(?P<task>.+)$",
    re.IGNORECASE,
)
_WAKE_COLON = re.compile(
    r"^(?P<persona>[A-Za-z0-9][A-Za-z0-9_.-]{0,31}):(?P<task>.+)$",
    re.IGNORECASE,
)


def _valid_persona(name: str) -> str | None:
    persona = name.strip().lower()
    if persona in known_persona_ids():
        return persona
    return None


def parse_wake_line(line: str) -> DelegateRequest | None:
    """Parse one line like ``@dev fix it`` or ``pm: status`` (not ``delegate:``)."""
    wanted = line.strip()
    if not wanted:
        return None
    if wanted.lower().startswith("delegate:"):
        return None
    match = _WAKE_AT.match(wanted)
    if match is None:
        match = _WAKE_COLON.match(wanted)
    if match is None:
        return None
    persona = _valid_persona(match.group("persona"))
    if persona is None:
        return None
    task = match.group("task").strip()
    if not task:
        return None
    return DelegateRequest(persona=persona, task=task)


def parse_inbound_handoff(text: str) -> DelegateRequest | None:
    """First ``delegate:``, ``@persona``, or ``persona:`` handoff in the message."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    delegate = parse_delegate_line(stripped)
    if delegate is not None:
        return delegate
    first_line = stripped.splitlines()[0].strip()
    return parse_wake_line(first_line)


def apply_wake_to_turn(text: str, default_persona: str) -> tuple[str, str]:
    """If the message wakes a persona, return ``(target_persona, task_text)``."""
    request = parse_wake_line(text.strip().splitlines()[0])
    if request is None:
        return default_persona, text.strip()
    return request.persona, request.task
