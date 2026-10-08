from __future__ import annotations
"""OpenRouter GLM reasoning payloads (offline)."""


import json
from pathlib import Path

import pytest

from chip import config, openrouter, runtime, spend

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_extract_empty_on_reasoning_length_starvation() -> None:
    payload = _load("openrouter_glm_reasoning_length.json")
    assert openrouter.reasoning_token_starvation(payload)
    assert openrouter.extract_assistant_text(payload) == ""


def test_extract_content_only_even_when_reasoning_on_stop() -> None:
    payload = _load("openrouter_glm_reasoning_stop.json")
    assert not openrouter.reasoning_token_starvation(payload)
    assert openrouter.extract_assistant_text(payload) == ""


def test_retry_budget_from_length_starvation() -> None:
    payload = _load("openrouter_glm_reasoning_length.json")
    assert openrouter.retry_max_tokens_if_reasoning_starved(payload, 512) == 1024
    assert openrouter.retry_max_tokens_if_reasoning_starved(payload, 1024) == 2048
    assert openrouter.retry_max_tokens_if_reasoning_starved(payload, 4096) is None


def test_runtime_retries_once_when_reasoning_starves(monkeypatch: pytest.MonkeyPatch) -> None:
    starved = _load("openrouter_glm_reasoning_length.json")
    ok = {
        "model": "z-ai/glm-5.3-flash",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Acknowledged."},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 12},
    }
    budgets: list[int] = []

    def fake_chat(_key: str, *, model: str, messages: list, max_tokens: int) -> dict:
        del model, messages
        budgets.append(max_tokens)
        return starved if len(budgets) == 1 else ok

    monkeypatch.setattr(openrouter, "chat_completion", fake_chat)
    monkeypatch.setattr(openrouter, "require_api_key", lambda: "sk-or-v1-unit-test-key")
    monkeypatch.setattr(spend, "guard_before_live_completion", lambda *a, **k: None)
    monkeypatch.setattr(spend, "record_after_live_completion", lambda *a, **k: spend.default_ledger())

    room_budget = config.ROOM_COMPLETION_MAX_TOKENS
    text, payload, routed, _ledger = runtime.complete(
        [{"role": "user", "content": "ping"}],
        max_tokens=room_budget,
        use_mock=False,
        command="room",
    )
    assert budgets == [room_budget, room_budget * 2]
    assert text == "Acknowledged."
    assert routed == "z-ai/glm-5.3-flash"
    assert payload["choices"][0]["message"]["content"] == "Acknowledged."


def test_runtime_no_retry_when_content_present(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = {
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": "pong"}},
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    calls = 0

    def fake_chat(*_a, **_k) -> dict:
        nonlocal calls
        calls += 1
        return ok

    monkeypatch.setattr(openrouter, "chat_completion", fake_chat)
    monkeypatch.setattr(openrouter, "require_api_key", lambda: "sk-or-v1-unit-test-key")
    monkeypatch.setattr(spend, "guard_before_live_completion", lambda *a, **k: None)
    monkeypatch.setattr(spend, "record_after_live_completion", lambda *a, **k: spend.default_ledger())

    text, _, _, _ = runtime.complete(
        [{"role": "user", "content": "ping"}],
        max_tokens=64,
        use_mock=False,
    )
    assert calls == 1
    assert text == "pong"
