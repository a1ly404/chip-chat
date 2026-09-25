"""Relay skeleton tests. No Discord and no OpenRouter network."""

from __future__ import annotations

from chip.openrouter import ChipConfigError
from chip_relay.config import RoomBinding, RelayConfig, load_config
from chip_relay.listener import ListenerNotWired, ListenerStub
from chip_relay.startup import check_startup


def _env() -> dict[str, str]:
    return {
        "DISCORD_CHIP_RELAY_TOKEN": "test-token-not-real",
        "DISCORD_CHIP_RELAY_WEBHOOK_PM": "https://example.invalid/pm",
        "DISCORD_CHIP_RELAY_WEBHOOK_DEV": "https://example.invalid/dev",
        "DISCORD_CHIP_RELAY_WEBHOOK_TESTER": "https://example.invalid/tester",
        "CHIP_RELAY_AUTO": "false",
    }


def test_load_fixed_rooms_and_auto_defaults_false() -> None:
    config = load_config(environ={})
    assert config.auto is False
    assert config.turn_limit == 1
    assert config.channel == "chip-relay"
    assert [room.id for room in config.rooms] == ["pm", "dev", "tester"]
    assert config.room("dev").as_agent == "dev"


def test_unknown_room_is_not_created() -> None:
    config = load_config(environ={})
    try:
        config.room("brand-new")
    except KeyError as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("expected KeyError")


def test_startup_ok_when_key_check_passes() -> None:
    config = load_config(environ=_env())
    assert check_startup(config, key_check=lambda: None, environ=_env()) == []


def test_startup_reports_key_health_failure() -> None:
    config = load_config(environ=_env())

    def bad() -> None:
        raise ChipConfigError("OpenRouter key health failed HTTP 401")

    errors = check_startup(config, key_check=bad, environ=_env())
    assert any("401" in err for err in errors)


def test_startup_refuses_auto_and_bad_turn_limit() -> None:
    env = _env()
    env["CHIP_RELAY_AUTO"] = "true"
    config = RelayConfig(
        channel="chip-relay",
        turn_limit=2,
        auto=True,
        rooms=(RoomBinding("pm", "pm", "DISCORD_CHIP_RELAY_WEBHOOK_PM"),),
    )
    errors = check_startup(config, key_check=lambda: None, environ=env)
    assert any("turn_limit" in err for err in errors)
    assert any("CHIP_RELAY_AUTO" in err for err in errors)


def test_listener_stub_does_not_connect() -> None:
    stub = ListenerStub()
    config = load_config(environ={})
    assert stub.run(config) == 0
    assert stub.started is False
    try:
        stub.connect(config)
    except ListenerNotWired:
        assert stub.started is False
    else:
        raise AssertionError("expected ListenerNotWired")
