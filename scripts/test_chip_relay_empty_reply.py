"""Empty chip reply must not hit Discord webhooks; gateway surfaces ERROR."""

from __future__ import annotations

import pytest

from chip_relay.config import RelayConfig, RoomBinding
from chip_relay.dispatch import ChipResult, Incoming, WebhookResult, handle_inbound_delegate, handle_message
from chip_relay.gateway import (
    dispatch_error_visible_in_channel,
    format_channel_dispatch_error,
)


def _dev_config() -> RelayConfig:
    room = RoomBinding(
        id="dev",
        as_agent="dev",
        webhook_env="CHIP_WEBHOOK_DEV",
        discord_channel="chip-dev",
        speakers_by_id={"900000000000000002": "dev"},
        speakers_declared=True,
    )
    return RelayConfig(channel="chip-relay", turn_limit=1, auto=False, rooms=(room,))


def _env() -> dict[str, str]:
    return {"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"}


@pytest.mark.parametrize("stdout", ['{"reply": ""}', '{"reply": "   \\n"}'])
def test_empty_chip_reply_skips_webhook(stdout: str) -> None:
    config = _dev_config()
    posts: list[tuple[str, str, str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del args, timeout
        return ChipResult(0, stdout)

    result = handle_message(
        Incoming("chip-dev", "hi", "member", author_id="900000000000000002"),
        config,
        chip=chip,
        webhook=lambda *a: pytest.fail("webhook must not be called for empty reply"),
        environ=_env(),
    )
    assert not result.ok
    assert result.alert == "empty chip reply"
    assert result.chip_calls == 1
    assert result.webhook_calls == 0
    assert posts == []


def test_inbound_delegate_empty_reply_skips_webhook() -> None:
    room = _dev_config().room("dev")
    posts: list[tuple[str, str, str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del args, timeout
        return ChipResult(0, '{"reply": ""}')

    result = handle_inbound_delegate(
        room,
        from_persona="pm",
        content="delegate:dev Reply with exactly: hop-ok",
        chip=chip,
        webhook=lambda *a: pytest.fail("webhook must not be called"),
        environ=_env(),
    )
    assert not result.ok
    assert result.alert == "empty chip reply"
    assert result.webhook_calls == 0


def test_delegate_follow_empty_reply_skips_second_webhook(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    config = _dev_config()
    chip_calls: list[int] = []
    webhook_calls: list[int] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del args, timeout
        chip_calls.append(1)
        if len(chip_calls) == 1:
            return ChipResult(0, '{"reply": "handing off\\ndelegate:tester run checks"}')
        return ChipResult(0, '{"reply": ""}')

    def webhook(url: str, username: str, reply: str) -> WebhookResult:
        del url, username, reply
        webhook_calls.append(1)
        return WebhookResult(204)

    result = handle_message(
        Incoming("chip-dev", "status?", "member", author_id="900000000000000002"),
        config,
        chip=chip,
        webhook=webhook,
        environ=_env(),
    )
    assert not result.ok
    assert result.alert == "empty chip reply"
    assert result.chip_calls == 2
    assert webhook_calls == [1]


def test_dispatch_error_channel_visibility() -> None:
    assert dispatch_error_visible_in_channel("empty chip reply")
    assert dispatch_error_visible_in_channel("webhook 400")
    assert not dispatch_error_visible_in_channel("slow_call_killed")
    assert not dispatch_error_visible_in_channel("malformed message")
    assert format_channel_dispatch_error("empty chip reply") == "[chip-relay] ERROR: empty chip reply"
