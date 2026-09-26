"""Shared completion helper for chat, agents, and rooms."""

from __future__ import annotations

from typing import Any

from chip import config, mock, openrouter, spend


def complete(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    max_tokens: int = 256,
    use_mock: bool | None = None,
    command: str = "completion",
    escalate: bool = False,
) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
    try:
        chosen = config.resolve_model(model, escalate=escalate)
    except ValueError as exc:
        raise openrouter.ChipConfigError(str(exc)) from exc
    if use_mock is None:
        use_mock = config.mock_mode()
    if use_mock:
        payload = mock.mock_completion(chosen, messages)
        return openrouter.extract_assistant_text(payload), payload, chosen, spend.load_ledger()

    key = openrouter.require_api_key()
    spend.guard_before_live_completion(command, key)
    payload = openrouter.chat_completion(key, model=chosen, messages=messages, max_tokens=max_tokens)
    retry_max = openrouter.retry_max_tokens_if_reasoning_starved(payload, max_tokens)
    if retry_max is not None:
        routed_retry = (payload.get("model") or chosen) if isinstance(payload.get("model"), str) else chosen
        spend.record_after_live_completion(command, routed_retry, payload, key=key)
        payload = openrouter.chat_completion(
            key, model=chosen, messages=messages, max_tokens=retry_max
        )
    routed = (payload.get("model") or chosen) if isinstance(payload.get("model"), str) else chosen
    ledger = spend.record_after_live_completion(command, routed, payload, key=key)
    return openrouter.extract_assistant_text(payload), payload, routed, ledger
