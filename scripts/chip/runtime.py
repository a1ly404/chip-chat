"""Shared completion helper for chat, agents, and rooms."""

from __future__ import annotations

from typing import Any

from chip import budget_mode, config, mock, openrouter, spend

# Payload key carrying the cost of a discarded reasoning-starved first attempt (see complete()).
RETRY_KEY = "_chip_retry"


def retry_cost_usd(payload: Any) -> float:
    """USD spent on a discarded reasoning-starved attempt recorded by :func:`complete`, else 0."""
    info = payload.get(RETRY_KEY) if isinstance(payload, dict) else None
    try:
        return max(0.0, float((info or {}).get("cost_usd") or 0.0))
    except (TypeError, ValueError):
        return 0.0


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
    monthly = spend.guard_before_live_completion(command, key)
    thrift = isinstance(monthly, (int, float)) and budget_mode.mode_for(monthly) == budget_mode.THRIFT
    if thrift:
        # Thrift mode: cheapest model, short answers (rest of the month).
        chosen = budget_mode.thrift_model()
        max_tokens = min(max_tokens, budget_mode.thrift_max_tokens())
    payload = openrouter.chat_completion(key, model=chosen, messages=messages, max_tokens=max_tokens)
    retry_max = openrouter.retry_max_tokens_if_reasoning_starved(payload, max_tokens)
    if thrift and retry_max is not None:
        retry_max = min(retry_max, 2 * budget_mode.thrift_max_tokens())
    retry_info: dict[str, Any] | None = None
    if retry_max is not None:
        routed_retry = (payload.get("model") or chosen) if isinstance(payload.get("model"), str) else chosen
        spend.record_after_live_completion(command, routed_retry, payload, key=key)
        r_cost, r_source, _p, _c = spend.cost_from_completion_payload(payload, routed_retry)
        retry_info = {"model": routed_retry, "cost_usd": float(r_cost), "cost_source": r_source}
        payload = openrouter.chat_completion(
            key, model=chosen, messages=messages, max_tokens=retry_max
        )
    if retry_info is not None and isinstance(payload, dict):
        # Cost of the discarded reasoning-starved attempt (already in the main ledger), so callers
        # that keep their own per-task spend (background jobs) can count it too.
        payload = {**payload, RETRY_KEY: retry_info}
    routed = (payload.get("model") or chosen) if isinstance(payload.get("model"), str) else chosen
    ledger = spend.record_after_live_completion(command, routed, payload, key=key)
    return openrouter.extract_assistant_text(payload), payload, routed, ledger
