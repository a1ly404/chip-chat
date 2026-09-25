"""Persona resolution for chip relay (no Discord or network)."""

from __future__ import annotations

from chip_relay.config import RoomBinding
from chip_relay.dispatch import ChipResult, Incoming, WebhookResult, handle_message
from chip_relay.personas import resolve_persona


def _dev_room(**kwargs) -> RoomBinding:
    defaults = {
        "id": "dev",
        "as_agent": "dev",
        "webhook_env": "CHIP_WEBHOOK_DEV",
        "discord_channel": "chip-dev",
    }
    defaults.update(kwargs)
    return RoomBinding(**defaults)


def test_resolve_by_discord_id() -> None:
    room = _dev_room(speakers_by_id={"999": "pm"}, speakers_by_handle={"alice": "tester"})
    assert resolve_persona(room, author_id="999", author_handle="alice") == "pm"


def test_resolve_by_handle_case_insensitive() -> None:
    room = _dev_room(speakers_by_handle={"alice": "pm"})
    assert resolve_persona(room, author_id="", author_handle="Alice") == "pm"


def test_fallback_to_room_default() -> None:
    room = _dev_room(speakers_by_id={"1": "pm"})
    assert resolve_persona(room, author_id="unknown", author_handle="stranger") == "dev"


def test_dispatch_uses_mapped_persona_for_chip_and_webhook() -> None:
    from chip_relay.config import RelayConfig

    room = _dev_room(
        speakers_by_id={"42": "pm"},
    )
    config = RelayConfig(
        channel="chip-relay",
        turn_limit=1,
        auto=False,
        rooms=(room,),
    )
    calls: list[list[str]] = []
    posts: list[tuple[str, str, str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del timeout
        calls.append(args)
        return ChipResult(0, '{"reply": "ok"}')

    def webhook(url: str, username: str, reply: str) -> WebhookResult:
        posts.append((url, username, reply))
        return WebhookResult(204)

    result = handle_message(
        Incoming("chip-dev", "hi", "discord_user", author_id="42"),
        config,
        chip=chip,
        webhook=webhook,
        environ={"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"},
    )
    assert result.ok
    assert calls[0][calls[0].index("--as") + 1] == "pm"
    assert posts == [("https://example.invalid/dev", "pm", "ok")]
