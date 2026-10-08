from __future__ import annotations
"""Persona resolution for chip relay (no Discord or network)."""


from chip_relay.config import RoomBinding
from chip_relay.dispatch import ChipResult, Incoming, WebhookResult, handle_message
from chip_relay.personas import resolve_persona, resolve_persona_for_dispatch, speakers_maps_effective


def _dev_room(**kwargs) -> RoomBinding:
    defaults = {
        "id": "dev",
        "as_agent": "dev",
        "webhook_env": "CHIP_WEBHOOK_DEV",
        "discord_channel": "dev-room",
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


def test_todo_only_speakers_are_not_effective() -> None:
    room = _dev_room(
        speakers_declared=True,
        speakers_by_id={"TODO_PM_DISCORD_USER_ID": "pm"},
        speakers_by_handle={"TODO_pm_discord_handle": "pm"},
    )
    assert not speakers_maps_effective(room)
    persona, alert = resolve_persona_for_dispatch(room, author_id="123", author_handle="human")
    assert alert is not None
    assert "speakers map" in alert


def test_dispatch_refuses_unconfigured_speakers() -> None:
    from chip_relay.config import RelayConfig

    room = _dev_room(
        speakers_declared=True,
        speakers_by_id={"TODO_DEV_DISCORD_USER_ID": "dev"},
    )
    config = RelayConfig(channel="chip-relay", turn_limit=1, auto=False, rooms=(room,))
    calls: list[list[str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del args, timeout
        calls.append([])
        return ChipResult(0, '{"reply": "ok"}')

    result = handle_message(
        Incoming("dev-room", "hi", "human", author_id="1"),
        config,
        chip=chip,
        webhook=lambda *a: WebhookResult(204),
        environ={"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"},
    )
    assert not result.ok
    assert calls == []


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
        Incoming("dev-room", "hi", "discord_user", author_id="42"),
        config,
        chip=chip,
        webhook=webhook,
        environ={"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"},
    )
    assert result.ok
    assert calls[0][calls[0].index("--as") + 1] == "pm"
    assert posts[0][0] == "https://example.invalid/dev"
    assert posts[0][1] == "pm"
    assert posts[0][2] == "ok"
