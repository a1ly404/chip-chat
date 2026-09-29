"""Persona wake (@mention / persona: prefix) and inbound handoff parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from chip_relay.dispatch import CHIP_TIMEOUT_S, ChipResult, Incoming, WebhookResult, handle_inbound_delegate, handle_message
from chip_relay.routing import RouteKind, classify_gateway_message, is_relay_self_inject_content
from chip_relay.wake import apply_wake_to_turn, parse_inbound_handoff, parse_wake_line
from chip_relay.config import RoomBinding, load_config
from chip_relay.routing import ALLY_INJECT_AUTHOR_ID


def _dev_room() -> RoomBinding:
    return RoomBinding(
        id="dev",
        as_agent="dev",
        webhook_env="CHIP_WEBHOOK_DEV",
        discord_channel="chip-dev",
        speakers_by_id={"187044496872046592": "pm"},
        speakers_declared=True,
    )


def test_parse_wake_and_delegate() -> None:
    assert parse_wake_line("@dev fix the flake").persona == "dev"
    assert parse_wake_line("@dev fix the flake").task == "fix the flake"
    assert parse_wake_line("pm: ship it").persona == "pm"
    assert parse_wake_line("delegate:dev nope") is None
    assert parse_inbound_handoff("delegate:dev hop").persona == "dev"
    assert parse_inbound_handoff("@pm status check").persona == "pm"


def test_apply_wake_to_turn() -> None:
    persona, text = apply_wake_to_turn("@dev only task", "pm")
    assert persona == "dev"
    assert text == "only task"


def test_chipchatdev_wake_refuses_without_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    config = load_config(environ={})
    called = {"n": 0}

    def chip(args: list[str], timeout: int) -> ChipResult:
        del args, timeout
        called["n"] += 1
        return ChipResult(0, '{"reply": "ack"}')

    result = handle_message(
        Incoming("chip-dev", "@chipchatdev read scope:raz-9 notes", "ally", author_id=ALLY_INJECT_AUTHOR_ID),
        config,
        chip=chip,
        webhook=lambda *a: WebhookResult(204),
        environ={"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"},
    )
    assert result.ok is False
    assert result.alert and "claim_owner" in result.alert
    assert called["n"] == 0


def test_human_wake_routes_as_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    config = load_config(environ={})
    calls: list[list[str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del timeout
        calls.append(args)
        return ChipResult(0, '{"reply": "ack"}')

    result = handle_message(
        Incoming("chip-dev", "@dev Reply with exactly: wake-ok", "ally", author_id=ALLY_INJECT_AUTHOR_ID),
        config,
        chip=chip,
        webhook=lambda *a: WebhookResult(204),
        environ={"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"},
    )
    assert result.ok
    assert calls[0][calls[0].index("--as") + 1] == "dev"
    assert calls[0][calls[0].index("-m") + 1] == "Reply with exactly: wake-ok"


def test_webhook_wake_delegates_like_delegate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    room = _dev_room()
    decision = classify_gateway_message(
        SimpleNamespace(
            id=1,
            content="dev: Reply with exactly: wake-hop",
            webhook_id=9,
            author=SimpleNamespace(id=1, name="pm", bot=False),
        ),
        relay_bot_user_id=999,
        room=room,
    )
    assert decision is not None
    assert decision.kind == RouteKind.WEBHOOK_DELEGATE

    calls: list[list[str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        assert timeout == CHIP_TIMEOUT_S
        calls.append(args)
        return ChipResult(0, '{"reply": "wake-hop"}')

    result = handle_inbound_delegate(
        room,
        from_persona="pm",
        content="dev: Reply with exactly: wake-hop",
        chip=chip,
        webhook=lambda *a: WebhookResult(204),
        environ={"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"},
    )
    assert result.ok
    assert calls[0][calls[0].index("--as") + 1] == "dev"


def test_self_inject_markers() -> None:
    assert is_relay_self_inject_content("GO: sitrep")
    assert is_relay_self_inject_content("--inject\nhello")
    assert is_relay_self_inject_content("@pm ping")
    assert not is_relay_self_inject_content("heartbeat ok")


def test_relay_self_message_inject_route() -> None:
    from types import SimpleNamespace

    room = _dev_room()
    env = {"CHIP_RELAY_INJECT_AUTHOR_ID": ALLY_INJECT_AUTHOR_ID}
    decision = classify_gateway_message(
        SimpleNamespace(
            id=2,
            content="GO: ally hour",
            webhook_id=None,
            author=SimpleNamespace(id=999, name="chip-relay", bot=True),
        ),
        relay_bot_user_id=999,
        room=room,
        environ=env,
    )
    assert decision is not None
    assert decision.kind == RouteKind.INJECT_BOT
    assert decision.incoming is not None
    assert decision.incoming.author_id == ALLY_INJECT_AUTHOR_ID
