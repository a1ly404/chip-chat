"""Relay skeleton tests. No Discord and no OpenRouter network."""

from __future__ import annotations

import pytest

from chip.openrouter import ChipConfigError
from chip_relay.config import RoomBinding, RelayConfig, load_config
from chip_relay.listener import ListenerNotWired, ListenerStub
from chip_relay.startup import check_startup


def _env() -> dict[str, str]:
    return {
        "INFISICAL_CLIENT_ID": "boot-id",
        "INFISICAL_CLIENT_SECRET": "boot-secret",
        "CF_ACCESS_CLIENT_ID": "boot-cf-id",
        "CF_ACCESS_CLIENT_SECRET": "boot-cf-secret",
        "DISCORD_CHIP_RELAY_TOKEN": "test-token-not-real",
        "CHIP_WEBHOOK_DEV": "https://example.invalid/dev",
        "CHIP_WEBHOOK_SELFTEST": "https://example.invalid/selftest",
        "CHIP_RELAY_AUTO": "false",
    }


def test_load_fixed_rooms_and_auto_defaults_false() -> None:
    config = load_config(environ={})
    assert config.auto is False
    assert config.turn_limit == 1
    assert config.channel == "chip-relay"
    assert [room.id for room in config.rooms] == ["relay", "dev", "selftest"]
    assert config.room("dev").as_agent == "dev"
    assert config.room("dev").speakers_declared is True
    assert config.room("relay").speakers_declared is True


def test_unknown_room_is_not_created() -> None:
    config = load_config(environ={})
    try:
        config.room("brand-new")
    except KeyError as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("expected KeyError")


def test_startup_ok_when_key_check_passes() -> None:
    from pathlib import Path

    rooms = Path(__file__).resolve().parent / "fixtures" / "relay_rooms.json"
    config = load_config(path=rooms, environ=_env())
    assert check_startup(config, key_check=lambda: None, environ=_env()) == []


def test_startup_ok_when_pm_speaker_on_production_rooms() -> None:
    config = load_config(environ=_env())
    errors = check_startup(config, key_check=lambda: None, environ=_env())
    assert not any("speakers map" in err for err in errors)
    dev = config.room("dev")
    relay = config.room("relay")
    assert dev.speakers_by_id.get("1553148704494714890") == "pm"
    assert dev.speakers_by_id.get("187044496872046592") == "pm"
    assert relay.speakers_by_id.get("187044496872046592") == "pm"
    assert "TODO_DEV_DISCORD_USER_ID" not in dev.speakers_by_id


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
        rooms=(RoomBinding("dev", "dev", "CHIP_WEBHOOK_DEV", "chip-dev"),),
    )
    errors = check_startup(config, key_check=lambda: None, environ=env)
    assert any("turn_limit" in err for err in errors)
    assert any("CHIP_RELAY_AUTO" in err for err in errors)


def test_missing_boot_reports_names_only() -> None:
    config = load_config(environ={})
    errors = check_startup(config, key_check=lambda: None, environ={})
    joined = " ".join(errors)
    assert "INFISICAL_CLIENT_SECRET" in joined
    assert "boot-secret" not in joined


def test_infisical_login_stops_on_html(monkeypatch: pytest.MonkeyPatch) -> None:
    from chip_relay.infisical_rest import InfisicalAccessError, fetch_runtime_secrets

    def fake_request(url: str, **kwargs):
        del url, kwargs
        return 200, "text/html", "<!DOCTYPE html>"

    monkeypatch.setattr("chip_relay.infisical_rest._request", fake_request)
    try:
        fetch_runtime_secrets(_env())
    except InfisicalAccessError as exc:
        assert "non-JSON" in str(exc)
    else:
        raise AssertionError("expected stop")


def test_inject_main_fetches_runtime_secrets_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    def fake_fetch() -> dict[str, str]:
        order.append("fetch")
        return {"DISCORD_CHIP_RELAY_TOKEN": "from-infisical"}

    def fake_run_inject(**_kwargs: object) -> int:
        order.append("inject")
        return 0

    monkeypatch.setattr("chip_relay.infisical_rest.fetch_runtime_secrets", fake_fetch)
    monkeypatch.setattr("chip_relay.inject.run_inject", fake_run_inject)
    from chip_relay.startup import main

    code = main(["inject", "-m", "hi", "--as-ally", "--mock"])
    assert code == 0
    assert order == ["inject"]

    order.clear()

    def fake_apply() -> None:
        order.append("fetch")
        return None

    monkeypatch.setattr("chip_relay.startup.apply_runtime_secrets", fake_apply)
    main(["inject", "-m", "hi", "--as-ally"])
    assert order == ["fetch", "inject"]


def test_post_webhook_sets_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    from chip_relay.gateway import post_webhook

    captured: list[object] = []

    class FakeResp:
        status = 204

        def __enter__(self) -> FakeResp:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(req: object, timeout: int = 30) -> FakeResp:
        del timeout
        captured.append(req)
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = post_webhook("https://example.invalid/hook", "dev", "hello")
    assert result.status == 204
    assert captured
    headers = {k.lower(): v for k, v in captured[0].header_items()}  # type: ignore[union-attr]
    assert headers.get("user-agent") == "chip-relay"


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
