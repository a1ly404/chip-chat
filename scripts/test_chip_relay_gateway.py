"""Gateway routing and inject path (no live Discord)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from chip_relay.config import RelayConfig, RoomBinding, load_config
from chip_relay.dispatch import CHIP_TIMEOUT_S, ChipResult, Incoming, WebhookResult, handle_inbound_delegate, handle_message
from chip_relay.routing import (
    ALLY_INJECT_AUTHOR_ID,
    RouteKind,
    classify_gateway_message,
    inject_incoming_for_room,
    parse_inject_bot_ids,
)
from chip_relay.inject import resolve_inject_author_id, run_inject

ROOMS = Path(__file__).resolve().parent / "fixtures" / "relay_rooms.json"


def _attachment(
    *,
    url: str = "https://cdn.discordapp.com/attachments/1/message.txt",
    filename: str = "message.txt",
    content_type: str = "text/plain",
    size: int = 12,
) -> SimpleNamespace:
    return SimpleNamespace(url=url, filename=filename, content_type=content_type, size=size)


def _message(
    *,
    content: str,
    author_id: int = 111,
    author_name: str = "human",
    bot: bool = False,
    webhook_id: int | None = None,
    msg_id: int = 1,
    attachments: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=msg_id,
        content=content,
        webhook_id=webhook_id,
        attachments=attachments or [],
        author=SimpleNamespace(id=author_id, name=author_name, bot=bot),
    )


def _dev_room() -> RoomBinding:
    return RoomBinding(
        id="dev",
        as_agent="dev",
        webhook_env="CHIP_WEBHOOK_DEV",
        discord_channel="chip-dev",
        speakers_by_id={"187044496872046592": "pm", "900000000000000002": "dev"},
        speakers_declared=True,
    )


def test_empty_content_with_text_attachment_routes_human() -> None:
    room = _dev_room()
    go_body = "GO: long ally objective\nline two"
    decision = classify_gateway_message(
        _message(
            content="",
            attachments=[_attachment(size=len(go_body))],
        ),
        relay_bot_user_id=999,
        room=room,
        fetch_attachment=lambda _url: go_body,
    )
    assert decision is not None
    assert decision.kind == RouteKind.HUMAN
    assert decision.incoming is not None
    assert decision.incoming.content == go_body


def test_empty_content_without_attachment_still_ignored() -> None:
    room = _dev_room()
    assert (
        classify_gateway_message(
            _message(content=""),
            relay_bot_user_id=999,
            room=room,
        )
        is None
    )


def test_oversized_attachment_ignored() -> None:
    room = _dev_room()
    from chip_relay.routing import ATTACHMENT_TEXT_MAX_BYTES

    assert (
        classify_gateway_message(
            _message(
                content="",
                attachments=[_attachment(size=ATTACHMENT_TEXT_MAX_BYTES + 1)],
            ),
            relay_bot_user_id=999,
            room=room,
            fetch_attachment=lambda _url: "should not run",
        )
        is None
    )


def test_non_text_attachment_ignored() -> None:
    room = _dev_room()
    assert (
        classify_gateway_message(
            _message(
                content="",
                attachments=[
                    _attachment(
                        filename="photo.png",
                        content_type="image/png",
                        size=100,
                    )
                ],
            ),
            relay_bot_user_id=999,
            room=room,
            fetch_attachment=lambda _url: "binary",
        )
        is None
    )


def test_trusted_bot_go_in_attachment() -> None:
    room = _dev_room()
    env = {
        "CHIP_RELAY_INJECT_BOT_IDS": "555",
        "CHIP_RELAY_INJECT_AUTHOR_ID": ALLY_INJECT_AUTHOR_ID,
    }
    decision = classify_gateway_message(
        _message(
            content="",
            author_id=555,
            bot=True,
            attachments=[_attachment(size=8)],
        ),
        relay_bot_user_id=999,
        room=room,
        environ=env,
        fetch_attachment=lambda _url: "GO: sitrep",
    )
    assert decision is not None
    assert decision.kind == RouteKind.INJECT_BOT
    assert decision.incoming is not None
    assert decision.incoming.content == "GO: sitrep"


def test_classify_human_message() -> None:
    room = _dev_room()
    decision = classify_gateway_message(
        _message(content="hello"),
        relay_bot_user_id=999,
        room=room,
    )
    assert decision is not None
    assert decision.kind == RouteKind.HUMAN
    assert decision.incoming is not None
    assert decision.incoming.author_id == "111"


def test_skip_own_bot_and_plain_webhook() -> None:
    room = _dev_room()
    assert (
        classify_gateway_message(
            _message(content="heartbeat", author_id=999),
            relay_bot_user_id=999,
            room=room,
        )
        is None
    )
    assert (
        classify_gateway_message(
            _message(content="pm said hi", author_name="pm", webhook_id=42),
            relay_bot_user_id=999,
            room=room,
        )
        is None
    )
    assert (
        classify_gateway_message(
            _message(content="noise", author_name="pm", bot=True),
            relay_bot_user_id=999,
            room=room,
        )
        is None
    )


def test_trusted_bot_uses_inject_author_id() -> None:
    room = _dev_room()
    env = {
        "CHIP_RELAY_INJECT_BOT_IDS": "555",
        "CHIP_RELAY_INJECT_AUTHOR_ID": ALLY_INJECT_AUTHOR_ID,
    }
    decision = classify_gateway_message(
        _message(content="GO: sitrep", author_id=555, bot=True),
        relay_bot_user_id=999,
        room=room,
        environ=env,
    )
    assert decision is not None
    assert decision.kind == RouteKind.INJECT_BOT
    assert decision.incoming is not None
    assert decision.incoming.author_id == ALLY_INJECT_AUTHOR_ID


def test_webhook_delegate_line() -> None:
    room = _dev_room()
    decision = classify_gateway_message(
        _message(
            content="delegate:dev Reply with exactly: hop-ok",
            author_name="pm",
            webhook_id=88,
        ),
        relay_bot_user_id=999,
        room=room,
    )
    assert decision is not None
    assert decision.kind == RouteKind.WEBHOOK_DELEGATE
    assert decision.from_persona == "pm"


def test_inbound_delegate_wakes_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    room = _dev_room()
    calls: list[list[str]] = []
    posts: list[tuple[str, str, str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        assert timeout == CHIP_TIMEOUT_S
        calls.append(args)
        return ChipResult(0, '{"reply": "hop-ok"}')

    def webhook(url: str, username: str, reply: str) -> WebhookResult:
        posts.append((url, username, reply))
        return WebhookResult(204)

    result = handle_inbound_delegate(
        room,
        from_persona="pm",
        content="delegate:dev Reply with exactly: hop-ok",
        chip=chip,
        webhook=webhook,
        environ={"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"},
    )
    assert result.ok
    assert result.delegate_hops == 1
    assert calls[0][calls[0].index("--as") + 1] == "dev"
    assert calls[0][calls[0].index("-m") + 1] == "Reply with exactly: hop-ok"
    assert posts == [("https://example.invalid/dev", "dev", "hop-ok")]


def test_human_still_maps_to_pm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    config = load_config(environ={})
    calls: list[list[str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del timeout
        calls.append(args)
        return ChipResult(0, '{"reply": "ack"}')

    result = handle_message(
        Incoming("chip-dev", "hi", "ally", author_id=ALLY_INJECT_AUTHOR_ID),
        config,
        chip=chip,
        webhook=lambda *a: WebhookResult(204),
        environ={"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"},
    )
    assert result.ok
    assert calls[0][calls[0].index("--as") + 1] == "pm"


def test_inject_cli_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHIP_CHAT_MOCK", "1")
    monkeypatch.setenv("CHIP_WEBHOOK_DEV", "https://example.invalid/dev")

    def fake_chip(args: list[str], timeout: int) -> ChipResult:
        del timeout
        return ChipResult(0, '{"reply": "inject-ok"}')

    monkeypatch.setattr("chip_relay.inject.run_chip", fake_chip)
    monkeypatch.setattr(
        "chip_relay.inject.post_webhook",
        lambda url, username, reply: WebhookResult(204),
    )
    code = run_inject(room_id="dev", message="inject smoke", as_ally=True, mock_chip=True)
    assert code == 0


def test_resolve_inject_author_id() -> None:
    assert resolve_inject_author_id(as_ally=True, author_id="") == ALLY_INJECT_AUTHOR_ID
    assert resolve_inject_author_id(as_ally=False, author_id="42", environ={}) == "42"
    assert parse_inject_bot_ids({"CHIP_RELAY_INJECT_BOT_IDS": "1, 2"}) == frozenset({"1", "2"})


def test_inject_incoming_for_room() -> None:
    room = _dev_room()
    inc = inject_incoming_for_room(room, content="x", author_id="1")
    assert inc.channel == "chip-dev"
    assert inc.author_id == "1"
