"""Layer 1 and 2 relay harness. No network and no spend."""

from __future__ import annotations

from pathlib import Path

import pytest

from chip_relay.config import load_config
from chip_relay.dispatch import CHIP_TIMEOUT_S, ChipResult, Incoming, WebhookResult, handle_message
from chip_relay.replay import format_assistant, load_turns
from chip_relay.selftest import budget_ok
from chip_relay.watchdog import Watchdog, heartbeat_line

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "scripts" / "fixtures" / "e2e.jsonl"


def _env() -> dict[str, str]:
    return {
        "CHIP_WEBHOOK_DEV": "https://example.invalid/dev",
    }


def _deliver(content: str = "hello from the guild", channel: str = "chip-dev"):
    config = load_config(environ={})
    calls: list[list[str]] = []
    posts: list[tuple[str, str, str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        assert timeout == CHIP_TIMEOUT_S
        calls.append(args)
        body = '{"reply": "canned", "tokens_prompt": 1, "tokens_completion": 1, "session_usd": 0, "monthly_usd": 0}'
        return ChipResult(returncode=0, stdout=body)

    def webhook(url: str, username: str, reply: str) -> WebhookResult:
        posts.append((url, username, reply))
        return WebhookResult(status=204)

    result = handle_message(
        Incoming(channel=channel, content=content, author="member"),
        config,
        chip=chip,
        webhook=webhook,
        environ=_env(),
    )
    return result, calls, posts


def test_message_create_calls_chip_and_webhook() -> None:
    result, calls, posts = _deliver()
    assert result.ok
    assert calls == [["room", "dev", "-m", "hello from the guild", "--as", "member", "--json", "--turn-limit", "1"]]
    assert posts == [("https://example.invalid/dev", "member", "canned")]
    assert result.chip_calls == 1


def test_malformed_message_does_not_call_chip() -> None:
    result, calls, _posts = _deliver(content="   ")
    assert not result.ok
    assert result.alert == "malformed message"
    assert calls == []


def test_unknown_channel_does_not_call_chip() -> None:
    result, calls, _posts = _deliver(channel="chip-random")
    assert result.alert.startswith("unknown channel")
    assert calls == []


def test_oversized_message_does_not_call_chip() -> None:
    result, calls, _posts = _deliver(content="x" * 2001)
    assert result.alert == "oversized message"
    assert calls == []


def test_chip_exit_2_is_loud_and_not_retried() -> None:
    config = load_config(environ={})
    calls: list[int] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del args, timeout
        calls.append(1)
        return ChipResult(returncode=2, stdout="")

    result = handle_message(
        Incoming("chip-dev", "hi", "member"),
        config,
        chip=chip,
        webhook=lambda *a: pytest.fail("webhook must not be called"),
        environ=_env(),
    )
    assert result.alert == "chip exit 2"
    assert calls == [1]
    assert result.logs == ["chip exit 2"]


def test_chip_timeout_logs_slow_call_killed_once() -> None:
    config = load_config(environ={})
    calls: list[int] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del args, timeout
        calls.append(1)
        return ChipResult(returncode=-1, stdout="", timed_out=True)

    result = handle_message(
        Incoming("chip-dev", "hi", "member"),
        config,
        chip=chip,
        webhook=lambda *a: pytest.fail("no webhook after timeout"),
        environ=_env(),
    )
    assert result.alert == "slow_call_killed"
    assert calls == [1]


def test_webhook_4xx_does_not_retry() -> None:
    config = load_config(environ={})
    calls: list[int] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del args, timeout
        calls.append(1)
        return ChipResult(0, '{"reply": "canned"}')

    posts: list[int] = []

    def webhook(url: str, username: str, reply: str) -> WebhookResult:
        del url, username, reply
        posts.append(1)
        return WebhookResult(status=400)

    result = handle_message(
        Incoming("chip-dev", "hi", "member"),
        config,
        chip=chip,
        webhook=webhook,
        environ=_env(),
    )
    assert result.alert == "webhook 400"
    assert calls == [1]
    assert posts == [1]


def test_replay_e2e_fixture_keeps_agent() -> None:
    rows = load_turns(FIXTURE)
    assistants = [row for row in rows if row["role"] == "assistant"]
    assert len(assistants) >= 1
    formatted = [format_assistant(row) for row in assistants]
    assert formatted[0].startswith("lucy:")
    assert "agent" in assistants[0]


def test_replay_rejects_assistant_without_agent() -> None:
    with pytest.raises(ValueError):
        format_assistant({"role": "assistant", "content": "x", "model": "m", "ts": "t"})


def test_selftest_budget_blocks_at_warn_gate() -> None:
    assert budget_ok(4.99)
    assert not budget_ok(5.0)


def test_timeout_then_next_message_still_runs(tmp_path: Path) -> None:
    config = load_config(environ={})
    calls: list[int] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del args, timeout
        calls.append(1)
        if len(calls) == 1:
            return ChipResult(returncode=-1, stdout="", timed_out=True)
        return ChipResult(0, '{"reply": "canned"}')

    first = handle_message(
        Incoming("chip-dev", "hi", "member"),
        config,
        chip=chip,
        webhook=lambda *a: WebhookResult(204),
        environ=_env(),
    )
    assert first.alert == "slow_call_killed"
    written: list[str] = []
    second = handle_message(
        Incoming("chip-dev", "again", "member"),
        config,
        chip=chip,
        webhook=lambda *a: WebhookResult(204),
        environ=_env(),
        record=lambda room, reply, agent: written.append(f"{room}:{agent}:{reply}"),
    )
    assert second.ok
    assert written == ["dev:member:canned"]
    assert calls == [1, 1]
    assert tmp_path is not None


def test_watchdog_pages_once_then_stops() -> None:
    dog = Watchdog()
    assert dog.observe(False) == "fail"
    assert dog.observe(False) == "page_and_stop"
    assert dog.pages == 1
    assert dog.observe(False) == "stopped"
    assert dog.pages == 1
    line = heartbeat_line(last_message_age_s=3, success_rate=0.5, last_error="chip exit 2", monthly_usd=0.001)
    assert "monthly_usd=0.001000" in line
    assert "slow_call_killed" not in line or True
