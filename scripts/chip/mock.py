"""Offline mock responses when CHIP_CHAT_MOCK=1 or --mock."""

from __future__ import annotations

import re
from typing import Any

from chip import config, pack_values
from chip_relay.delegate import parse_delegate

_SPECIALISTS = pack_values.mock_specialists()
_PERSONA_DETECT_ORDER = pack_values.mock_persona_detect_order()
_HOP_OK = re.compile(r"hop-ok[-\w]*", re.IGNORECASE)
_ORCHESTRATOR_TICK = "orchestrator-tick:"


def mock_auth_key() -> dict[str, Any]:
    return {
        "label": "chip-chat-mock",
        "usage": 0.001,
        "usage_monthly": 0.42,
        "is_free_tier": False,
    }


def _detect_persona(system: str) -> str | None:
    lower = system.lower()
    personas = config.load_personas()
    for pid in _PERSONA_DETECT_ORDER:
        entry = personas.get(pid)
        if not entry:
            continue
        display = (entry.get("display_name") or pid).strip()
        if not display:
            continue
        if f"you are {display.lower()}" in lower:
            return pid
    if "planner" in lower and "you are planner" in lower:
        return "planner"
    if "builder" in lower and "you are builder" in lower:
        return "builder"
    return None


def _persona_label(persona: str) -> str:
    entry = config.load_personas().get(persona) or {}
    return (entry.get("display_name") or persona).strip() or persona


def _delegate_line_from_user(text: str) -> str | None:
    request = parse_delegate(text)
    if request is None:
        return None
    return f"delegate:{request.persona} {request.task}"


def _specialist_reply(persona: str, task: str) -> str:
    label = _persona_label(persona)
    hop = _HOP_OK.search(task)
    if hop:
        body = hop.group(0)
    elif "exactly:" in task.lower():
        body = task.split("exactly:", 1)[-1].strip().rstrip(".")
    elif task.strip():
        body = task.strip()[:80]
    else:
        body = "ok"
    return f"[mock {label}] {body}"


def mock_completion(model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    last = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last = (msg.get("content") or "").strip()
            break
    system = ""
    for msg in messages:
        if msg.get("role") == "system":
            system = (msg.get("content") or "").lower()
            break

    if "planner" in system and "you are planner" in system:
        text = f"[mock Planner] Steps for «{last[:80]}»: 1) clarify 2) slice 3) ship."
    elif "builder" in system and "you are builder" in system:
        text = f"[mock Builder] On «{last[:80]}»: implement step 1 with a tiny CLI proof."
    else:
        persona = _detect_persona(system)
        if persona == "pm" and _ORCHESTRATOR_TICK in last:
            text = (
                "claim: orchestrator closed specialist slice\n"
                "status: done\n"
                "evidence: WI-16\n"
                "next: none"
            )
        else:
            delegate_line = _delegate_line_from_user(last)
            if delegate_line is not None:
                label = _persona_label(persona or "pm")
                text = f"[mock {label}] on it.\n{delegate_line}"
            elif persona in _SPECIALISTS:
                text = _specialist_reply(persona, last)
            elif persona:
                text = f"[mock {_persona_label(persona)}] got it."
            else:
                text = "[mock Chip] got it."

    return {
        "id": "chip-mock-completion",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 20, "total_tokens": 32},
        "_chip_latency_ms": 1,
    }
