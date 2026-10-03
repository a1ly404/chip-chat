"""#monitoring-logs listen path — pm skills, VPN law, no #plex-fix posts."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from chip_relay.config import RoomBinding, load_config
from chip_relay.dispatch import WebhookResult
from chip_relay.monitoring_listen import (
    MONITORING_LISTEN_ENV,
    handle_monitoring_alert,
    monitoring_listen_enabled,
)
from chip_relay.monitoring_skills import (
    PLEX_FIX_CHANNEL_ID,
    append_skill_learning,
    load_skill_learnings,
    match_skill,
    parse_monitoring_message,
    post_target_allowed,
    receipt_violates_vpn_law,
    run_skill_for_alert,
)
from chip_relay.routing import RouteKind, classify_gateway_message
from chip_relay.startup import check_startup


def _monitoring_room() -> RoomBinding:
    return RoomBinding(
        id="monitoring-logs",
        as_agent="pm",
        webhook_env="CHIP_WEBHOOK_MONITORING",
        discord_channel="monitoring-logs",
        channel_id="1480439800350769252",
        monitoring_listen=True,
    )


def test_rooms_json_declares_monitoring_logs() -> None:
    config = load_config()
    room = config.room("monitoring-logs")
    assert room.channel_id == "1480439800350769252"
    assert room.monitoring_listen is True
    assert room.as_agent == "pm"


def test_listen_flag_defaults_off() -> None:
    assert monitoring_listen_enabled({}) is False
    assert monitoring_listen_enabled({MONITORING_LISTEN_ENV: "1"}) is True


def test_startup_skips_monitoring_webhook_until_flag(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    rooms = Path(__file__).resolve().parent / "fixtures" / "relay_rooms.json"
    raw = json.loads(rooms.read_text(encoding="utf-8"))
    raw["rooms"].append(
        {
            "id": "monitoring-logs",
            "as": "pm",
            "discord_channel": "monitoring-logs",
            "channel_id": "1480439800350769252",
            "webhook_env": "CHIP_WEBHOOK_MONITORING",
            "monitoring_listen": True,
        }
    )
    fixture = tmp_path / "rooms.json"
    fixture.write_text(json.dumps(raw), encoding="utf-8")
    env = {
        "INFISICAL_CLIENT_ID": "x",
        "INFISICAL_CLIENT_SECRET": "x",
        "CF_ACCESS_CLIENT_ID": "x",
        "CF_ACCESS_CLIENT_SECRET": "x",
        "DISCORD_CHIP_RELAY_TOKEN": "tok",
        "CHIP_WEBHOOK_DEV": "https://example.invalid/dev",
        "CHIP_WEBHOOK_SELFTEST": "https://example.invalid/selftest",
    }
    config = load_config(fixture, environ=env)
    assert check_startup(config, key_check=lambda: None, environ=env) == []
    env_flag = {**env, MONITORING_LISTEN_ENV: "1"}
    errs = check_startup(config, key_check=lambda: None, environ=env_flag)
    assert any("CHIP_WEBHOOK_MONITORING" in e for e in errs)


def test_match_stale_gluetun_skill() -> None:
    alert = parse_monitoring_message("health monitor: GET /gluetun/health -> 404 No such container")
    skill = match_skill(alert)
    assert skill is not None
    assert skill.id == "stale-gluetun-health-target"


def test_gluetun_skill_receipt_forbids_vpn_mutations() -> None:
    alert = parse_monitoring_message("gluetun probe 404")
    run = run_skill_for_alert(alert)
    assert run.matched
    assert "gluetun" in run.receipt.lower()
    assert "never recreate" in run.receipt.lower() or "stale" in run.receipt.lower()
    assert receipt_violates_vpn_law("next: docker restart gluetun now") is not None
    assert receipt_violates_vpn_law("next: read-only triage only") is None


def test_qb_firewalled_not_treated_as_success() -> None:
    alert = parse_monitoring_message('{"key":"qb-connection","detail":"connection_status=firewalled"}')
    run = run_skill_for_alert(alert)
    assert run.skill_id == "qb-firewalled-not-success"
    assert "firewalled" in run.receipt.lower()


def test_learning_round_trip(tmp_path, monkeypatch) -> None:
    import os

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    append_skill_learning("stale-gluetun-health-target", "first run saw 404", tag="match")
    lines = load_skill_learnings("stale-gluetun-health-target")
    assert lines and "404" in lines[-1]
    alert = parse_monitoring_message("gluetun 404 again")
    run = run_skill_for_alert(alert)
    assert "prior=" in run.receipt


def test_post_target_refuses_plex_fix() -> None:
    ok, reason = post_target_allowed(
        webhook_env="CHIP_WEBHOOK_MONITORING",
        channel_id=PLEX_FIX_CHANNEL_ID,
        webhook_url="https://discord.com/api/webhooks/x",
    )
    assert ok is False
    assert "plex-fix" in reason
    ok2, _ = post_target_allowed(
        webhook_env="CHIP_WEBHOOK_PLEX_FIX",
        channel_id="1",
        webhook_url="https://discord.com/api/webhooks/x",
    )
    assert ok2 is False


def test_handle_monitoring_posts_pm_receipt(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(MONITORING_LISTEN_ENV, "1")
    posts: list[tuple[str, str, str]] = []

    def hook(url: str, user: str, body: str) -> WebhookResult:
        posts.append((url, user, body))
        return WebhookResult(status=204)

    env = {
        MONITORING_LISTEN_ENV: "1",
        "CHIP_WEBHOOK_MONITORING": "https://example.invalid/monitoring",
    }
    result = handle_monitoring_alert(
        _monitoring_room(),
        content="monitoring-api gluetun health 404",
        channel_id="1480439800350769252",
        webhook=hook,
        environ=env,
    )
    assert result.ok
    assert result.webhook_calls == 1
    assert posts[0][1] == "pm"
    assert "claim:" in posts[0][2]


def test_gateway_routes_bot_alert_when_flag_on() -> None:
    room = _monitoring_room()
    msg = SimpleNamespace(
        id=99,
        content="cron: gluetun container missing",
        author=SimpleNamespace(id=555, name="monitor-bot", bot=True),
        attachments=[],
        webhook_id=None,
    )
    decision = classify_gateway_message(
        msg,
        relay_bot_user_id=1,
        room=room,
        environ={MONITORING_LISTEN_ENV: "1"},
    )
    assert decision is not None
    assert decision.kind == RouteKind.MONITORING_ALERT


def test_gateway_ignores_monitoring_when_flag_off() -> None:
    room = _monitoring_room()
    msg = SimpleNamespace(
        id=99,
        content="cron: gluetun container missing",
        author=SimpleNamespace(id=555, name="monitor-bot", bot=True),
        attachments=[],
        webhook_id=None,
    )
    assert (
        classify_gateway_message(
            msg,
            relay_bot_user_id=1,
            room=room,
            environ={},
        )
        is None
    )


def test_gateway_ignores_pm_webhook_echo() -> None:
    room = _monitoring_room()
    msg = SimpleNamespace(
        id=100,
        content="claim: x\nstatus: blocked\nevidence: tool:t\nnext: y",
        author=SimpleNamespace(id=555, name="pm", bot=True),
        attachments=[],
        webhook_id=12345,
    )
    assert (
        classify_gateway_message(
            msg,
            relay_bot_user_id=1,
            room=room,
            environ={MONITORING_LISTEN_ENV: "1"},
        )
        is None
    )
