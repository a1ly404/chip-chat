"""OpenRouter HTTP client — OPENROUTER_CC_API_KEY from environment only."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from chip import config, pack_values

UPSTREAM = "https://openrouter.ai/api/v1"
ENV_KEY = "OPENROUTER_CC_API_KEY"
KEY_PREFIX = "sk-or-"

# GLM 5.3 family spends completion budget on reasoning; small max_tokens starves content.
MAX_COMPLETION_TOKENS_CAP = 4096
_REASONING_EFFORT_MODEL_PREFIX = "z-ai/glm-"


class ChipConfigError(Exception):
    """Missing or invalid Chip Chat OpenRouter configuration."""


def require_api_key() -> str:
    config.load_dotenv()
    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        raise ChipConfigError(
            f"{ENV_KEY} is not set. Copy .env.example to .env or export a Chip Chat–dedicated "
            f"OpenRouter key (see docs/CHIP_CHAT.md)."
        )
    if not key.startswith(KEY_PREFIX):
        raise ChipConfigError(
            f"{ENV_KEY} must look like an OpenRouter key (prefix {KEY_PREFIX!r}); got length {len(key)}."
        )
    if key in ("sk-or-v1-REPLACE_ME", "sk-or-v1-your_openrouter_key_here"):
        raise ChipConfigError(f"{ENV_KEY} is still a placeholder; set a real key in your environment.")
    return key


def _headers(key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": pack_values.openrouter_http_referer(),
        "X-Title": "Chip-Chat-CLI",
    }


def key_health(key: str) -> dict[str, Any]:
    """GET /api/v1/key. Relay startup uses this; it does not chat."""
    req = urllib.request.Request(f"{UPSTREAM}/key", headers=_headers(key))
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise ChipConfigError(f"OpenRouter key health failed HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ChipConfigError(f"OpenRouter key health unreachable: {exc}") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {}


def auth_key_info(key: str) -> dict[str, Any]:
    req = urllib.request.Request(f"{UPSTREAM}/auth/key", headers=_headers(key))
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise ChipConfigError(f"OpenRouter auth/key failed HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ChipConfigError(f"OpenRouter auth/key unreachable: {exc}") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {}


def _openrouter_reasoning_options(model: str) -> dict[str, Any] | None:
    """Lower reasoning effort on cheap GLM defaults (reasoning cannot be disabled)."""
    if model.startswith(_REASONING_EFFORT_MODEL_PREFIX):
        return {"effort": "low"}
    return None


def chat_completion(
    key: str,
    *,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = 256,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    reasoning = _openrouter_reasoning_options(model)
    if reasoning is not None:
        body["reasoning"] = reasoning
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{UPSTREAM}/chat/completions",
        data=data,
        headers=_headers(key),
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise ChipConfigError(f"OpenRouter chat failed HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ChipConfigError(f"OpenRouter chat unreachable: {exc}") from exc
    latency_ms = int((time.perf_counter() - start) * 1000)
    if isinstance(payload, dict):
        payload["_chip_latency_ms"] = latency_ms
    return payload if isinstance(payload, dict) else {"raw": payload}


def _choice(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return {}
    return choices[0]


def finish_reason(payload: dict[str, Any]) -> str:
    reason = _choice(payload).get("finish_reason")
    return (reason or "").strip() if isinstance(reason, str) else ""


def _message_field_text(message: dict[str, Any], field: str) -> str:
    raw = message.get(field)
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, dict) and item.get("type") in (None, "text"):
                parts.append(str(item.get("text") or ""))
        return "".join(parts).strip()
    return str(raw).strip()


def _assistant_message(payload: dict[str, Any]) -> dict[str, Any]:
    message = _choice(payload).get("message")
    return message if isinstance(message, dict) else {}


def reasoning_token_starvation(payload: dict[str, Any]) -> bool:
    """Completion budget exhausted on reasoning with no assistant content."""
    message = _assistant_message(payload)
    if _message_field_text(message, "content"):
        return False
    if not _message_field_text(message, "reasoning"):
        return False
    return finish_reason(payload) == "length"


def retry_max_tokens_if_reasoning_starved(payload: dict[str, Any], max_tokens: int) -> int | None:
    """One-shot higher budget when GLM-style reasoning consumed the completion cap."""
    if not reasoning_token_starvation(payload):
        return None
    bumped = min(max(max_tokens * 2, 1024), MAX_COMPLETION_TOKENS_CAP)
    if bumped <= max_tokens:
        return None
    return bumped


def extract_assistant_text(payload: dict[str, Any]) -> str:
    """Assistant reply for Discord/transcript — message.content only (never reasoning)."""
    message = _assistant_message(payload)
    return _message_field_text(message, "content")
