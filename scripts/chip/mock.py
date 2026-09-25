"""Offline mock responses when CHIP_CHAT_MOCK=1 or --mock."""

from __future__ import annotations

from typing import Any


def mock_auth_key() -> dict[str, Any]:
    return {
        "label": "chip-chat-mock",
        "usage": 0.001,
        "usage_monthly": 0.42,
        "is_free_tier": False,
    }


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

    if "planner" in system:
        text = f"[mock Planner] Steps for «{last[:80]}»: 1) clarify 2) slice 3) ship."
    elif "builder" in system:
        text = f"[mock Builder] On «{last[:80]}»: implement step 1 with a tiny CLI proof."
    else:
        text = f"[mock Chip] Echo OK — received {len(last)} chars."

    return {
        "id": "chip-mock-completion",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 20, "total_tokens": 32},
        "_chip_latency_ms": 1,
    }
