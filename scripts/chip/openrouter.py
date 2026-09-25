"""OpenRouter HTTP client — OPENROUTER_CC_API_KEY from environment only."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from chip import config

UPSTREAM = "https://openrouter.ai/api/v1"
ENV_KEY = "OPENROUTER_CC_API_KEY"
KEY_PREFIX = "sk-or-"


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
        "HTTP-Referer": "https://github.com/a1ly404/chip-chat",
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


def chat_completion(
    key: str,
    *,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = 256,
) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
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


def extract_assistant_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    return (content or "").strip()
