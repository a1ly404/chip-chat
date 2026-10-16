"""Monthly budget modes: normal / thrift / stopped + month rollover (Refs RAZ-16). Mocks only."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chip import budget_mode, spend

_BUDGET_ENV = (
    "CHIP_SPEND_CAP_USD",
    "CHIP_SPEND_THRIFT_PCT",
    "CHIP_THRIFT_MODEL",
    "CHIP_THRIFT_MAX_TOKENS",
    "CHIP_THRIFT_REPLY_CHARS",
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    for key in _BUDGET_ENV:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def _write_ledger(monthly: float, *, month: str | None = None) -> None:
    ledger = spend.default_ledger()
    ledger["monthly_usd"] = monthly
    ledger["monthly_source"] = "api"
    ledger["month"] = month or budget_mode.month_key()
    spend.save_ledger(ledger)


# --- thresholds ------------------------------------------------------------


def test_defaults_cap_5_thrift_4() -> None:
    assert budget_mode.cap_usd() == pytest.approx(5.0)
    assert budget_mode.thrift_usd() == pytest.approx(4.0)
    assert budget_mode.thrift_model() == "qwen/qwen3.7-flash"
    assert budget_mode.thrift_max_tokens() == 256


@pytest.mark.parametrize("monthly", [0.0, 0.18, 3.99])
def test_under_80_percent_is_normal(monthly: float) -> None:
    assert budget_mode.mode_for(monthly) == budget_mode.NORMAL


@pytest.mark.parametrize("monthly", [4.0, 4.5, 4.99])
def test_80_to_under_100_percent_is_thrift(monthly: float) -> None:
    assert budget_mode.mode_for(monthly) == budget_mode.THRIFT


@pytest.mark.parametrize("monthly", [5.0, 5.01, 12.0])
def test_100_percent_and_over_is_stopped(monthly: float) -> None:
    assert budget_mode.mode_for(monthly) == budget_mode.STOPPED


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_SPEND_CAP_USD", "10")
    monkeypatch.setenv("CHIP_SPEND_THRIFT_PCT", "0.5")
    assert budget_mode.thrift_usd() == pytest.approx(5.0)
    assert budget_mode.mode_for(4.99) == budget_mode.NORMAL
    assert budget_mode.mode_for(5.0) == budget_mode.THRIFT
    assert budget_mode.mode_for(10.0) == budget_mode.STOPPED
    monkeypatch.setenv("CHIP_SPEND_CAP_USD", "junk")
    assert budget_mode.cap_usd() == pytest.approx(5.0)


# --- live-call routing (mocked OpenRouter) ---------------------------------


def _mock_live(monkeypatch: pytest.MonkeyPatch, monthly: float) -> list[dict]:
    from chip import openrouter, runtime

    calls: list[dict] = []
    monkeypatch.setenv("OPENROUTER_CC_API_KEY", "sk-or-v1-unit-test-key")
    monkeypatch.setattr(runtime.config, "mock_mode", lambda: False)
    monkeypatch.setattr(spend, "guard_before_live_completion", lambda _c, _k: monthly)
    monkeypatch.setattr(spend, "record_after_live_completion", lambda *a, **k: spend.load_ledger())

    def fake_completion(_key, *, model, messages, max_tokens):
        calls.append({"model": model, "max_tokens": max_tokens})
        return {"model": model, "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}

    monkeypatch.setattr(openrouter, "chat_completion", fake_completion)
    return calls


def test_normal_mode_keeps_requested_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import runtime

    calls = _mock_live(monkeypatch, 1.0)
    runtime.complete([{"role": "user", "content": "hi"}], model="z-ai/glm-5.3-flash", max_tokens=1024)
    assert calls == [{"model": "z-ai/glm-5.3-flash", "max_tokens": 1024}]


def test_thrift_mode_routes_cheap_model_short_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import runtime

    calls = _mock_live(monkeypatch, 4.2)
    runtime.complete([{"role": "user", "content": "status?"}], model="z-ai/glm-5.3-flash", max_tokens=1024)
    assert calls == [{"model": "qwen/qwen3.7-flash", "max_tokens": 256}]


def test_stopped_refuses_live_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import openrouter, runtime

    monkeypatch.setenv("OPENROUTER_CC_API_KEY", "sk-or-v1-unit-test-key")
    monkeypatch.setattr(runtime.config, "mock_mode", lambda: False)

    def fake_refresh(_key: str):
        led = spend.default_ledger()
        led["monthly_usd"] = 5.0
        return led, 5.0

    monkeypatch.setattr(spend, "refresh_from_auth", fake_refresh)
    monkeypatch.setattr(openrouter, "chat_completion", lambda *a, **k: pytest.fail("live call at hard stop"))
    with pytest.raises(spend.SpendCutoffError):
        runtime.complete([{"role": "user", "content": "hi"}])


def test_stopped_mock_mode_still_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import runtime

    _write_ledger(9.0)
    text, _payload, _model, _ledger = runtime.complete([{"role": "user", "content": "ping"}], use_mock=True)
    assert text


# --- read-only in thrift ----------------------------------------------------


def test_act_paths_open_in_normal() -> None:
    _write_ledger(1.0)
    assert budget_mode.act_block_reason("solver restart") is None


@pytest.mark.parametrize("monthly,mode", [(4.2, "thrift"), (6.0, "stopped")])
def test_act_paths_closed_in_thrift_and_stopped(monthly: float, mode: str) -> None:
    _write_ledger(monthly)
    reason = budget_mode.act_block_reason("solver restart")
    assert reason is not None and reason.startswith(f"budget-{mode}")


def test_orchestrator_hop_off_in_thrift_even_if_unlocked() -> None:
    from chip_relay import orchestrator

    _write_ledger(4.2)
    assert orchestrator.orchestrator_tick_enabled({"CHIP_RELAY_PM_ORCHESTRATOR": "1"}) is False


def test_thrift_clips_reply_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_THRIFT_REPLY_CHARS", "20")
    long = "x" * 100
    _write_ledger(1.0)
    assert budget_mode.clip_reply(long) == long
    _write_ledger(4.2)
    assert len(budget_mode.clip_reply(long)) == 20


# --- month rollover -----------------------------------------------------------


def test_rollover_resets_monthly_figures() -> None:
    ledger = spend.default_ledger()
    ledger.update(month="2026-10", monthly_usd=4.9, estimated_monthly_usd=4.9, session_usd=7.0,
                  budget_notices={"thrift": "2026-10-20T00:00:00+00:00"})
    rolled = budget_mode.roll_month(ledger, datetime(2026, 11, 1, 0, 5, tzinfo=timezone.utc))
    assert rolled is True
    assert ledger["month"] == "2026-11"
    assert ledger["monthly_usd"] == 0.0
    assert ledger["budget_notices"] == {}
    assert spend.ledger_monthly_usd(ledger) == pytest.approx(0.0)
    assert budget_mode.mode_for(spend.ledger_monthly_usd(ledger)) == budget_mode.NORMAL
    assert ledger["previous_month"] == {"month": "2026-10", "monthly_usd": 4.9}


def test_same_month_no_reset_and_legacy_ledger_stamped() -> None:
    ledger = spend.default_ledger()
    ledger.update(month="2026-10", monthly_usd=4.5)
    assert budget_mode.roll_month(ledger, datetime(2026, 10, 31, tzinfo=timezone.utc)) is False
    assert ledger["monthly_usd"] == 4.5
    legacy = {"verified_as_of": "2026-10-15T13:13:53+00:00", "monthly_usd": 0.18}
    assert budget_mode.roll_month(legacy, datetime(2026, 10, 15, tzinfo=timezone.utc)) is False
    assert legacy["month"] == "2026-10" and legacy["monthly_usd"] == 0.18


def test_load_ledger_rolls_over_on_disk_month(tmp_path: Path) -> None:
    _write_ledger(4.9, month="2000-01")
    loaded = spend.load_ledger()
    assert loaded["month"] == budget_mode.month_key()
    assert spend.ledger_monthly_usd(loaded) == pytest.approx(0.0)
    assert budget_mode.status(loaded)["mode"] == budget_mode.NORMAL


def test_usage_monthly_zero_does_not_fall_back_to_lifetime_usage() -> None:
    assert spend.monthly_usd_from_auth({"usage_monthly": 0, "usage": 42.0}) is None
    assert spend.monthly_usd_from_auth({"usage_monthly": 1.25, "usage": 42.0}) == pytest.approx(1.25)
    assert spend.monthly_usd_from_auth({"usage": 0.5}) == pytest.approx(0.5)


# --- one-shot notices + status ------------------------------------------------


def test_notices_post_once_per_mode() -> None:
    posted: list[str] = []

    def post(line: str) -> bool:
        posted.append(line)
        return True

    _write_ledger(1.0)
    assert budget_mode.maybe_post_notice(post) is None
    _write_ledger(4.2)
    assert budget_mode.maybe_post_notice(post) == "thrift"
    assert budget_mode.maybe_post_notice(post) is None
    led = spend.load_ledger()
    led["monthly_usd"] = 5.1
    spend.save_ledger(led)
    assert budget_mode.maybe_post_notice(post) == "stopped"
    assert budget_mode.maybe_post_notice(post) is None
    assert len(posted) == 2
    assert posted[0].startswith("[chip-budget] THRIFT MODE")
    assert posted[1].startswith("[chip-budget] HARD STOP")
    assert all("@" not in line for line in posted)


def test_failed_post_retries_later() -> None:
    _write_ledger(4.2)
    assert budget_mode.maybe_post_notice(lambda _l: False) is None
    assert budget_mode.maybe_post_notice(lambda _l: True) == "thrift"


def test_status_endpoint_exposes_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from chip_relay.health_server import start_health_server

    _write_ledger(4.2)
    monkeypatch.setenv("CHIP_RELAY_HEALTH_PORT", "0")
    server = start_health_server()
    try:
        url = f"http://127.0.0.1:{server.server_port}/status"
        with urllib.request.urlopen(url, timeout=2) as resp:
            payload = json.loads(resp.read())
    finally:
        server.shutdown()
    assert payload["budget"]["mode"] == "thrift"
    assert payload["budget"]["cap_usd"] == pytest.approx(5.0)
    assert payload["budget"]["thrift_model"] == "qwen/qwen3.7-flash"
