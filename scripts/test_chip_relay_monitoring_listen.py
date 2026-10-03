"""#monitoring-logs — ingest shapes, universal soak, config-driven skills."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chip_relay.config import RoomBinding, load_config
from chip_relay.dispatch import WebhookResult
from chip_relay.monitoring_listen import (
    MONITORING_LISTEN_ENV,
    handle_monitoring_alert,
    monitoring_listen_enabled,
)
from chip_relay.monitoring_outage import DEFAULT_OUTAGE_SOAK_S, note_outage_sighting
from chip_relay.monitoring_skills import (
    PLEX_FIX_CHANNEL_ID,
    append_skill_learning,
    create_overlay_skill,
    execute_fix_action,
    load_policy,
    load_skill_learnings,
    match_skill,
    overlay_registry_path,
    parse_monitoring_message,
    post_target_allowed,
    receipt_violates_vpn_law,
    run_skill_for_alert,
    should_ignore_alert,
)
from chip_relay.routing import (
    RouteKind,
    classify_gateway_message,
    effective_monitoring_content,
    is_pm_monitoring_receipt_echo,
)
from chip_relay.startup import check_startup

SOAK = DEFAULT_OUTAGE_SOAK_S


def _monitoring_room() -> RoomBinding:
    return RoomBinding(
        id="monitoring-logs",
        as_agent="pm",
        webhook_env="CHIP_WEBHOOK_MONITORING",
        discord_channel="monitoring-logs",
        channel_id="1480439800350769252",
        monitoring_listen=True,
    )


def _after_soak(t0: float) -> float:
    return t0 + SOAK + 1


def test_rooms_json_declares_monitoring_logs() -> None:
    config = load_config()
    room = config.room("monitoring-logs")
    assert room.channel_id == "1480439800350769252"
    assert room.monitoring_listen is True


def test_registry_policy_soak_10m() -> None:
    policy = load_policy()
    assert policy.soak_seconds == 600.0
    assert "gluetun" in policy.forbidden_restart_containers
    assert "wireguard" in policy.allowed_restart_containers


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


def test_match_stale_gluetun_skill() -> None:
    alert = parse_monitoring_message(
        "AirVPN port-health: gluetun /v1/portforward 404 No such container"
    )
    skill = match_skill(alert)
    assert skill is not None
    assert skill.id == "stale-vpn-gluetun-label-probe"
    assert skill.fix_action.get("kind") == "record_retired_target"


def test_universal_soak_blocks_first_sighting(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    t0 = 1_000_000.0
    alert = parse_monitoring_message(
        "AirVPN port-health: gluetun /v1/portforward 404 No such container"
    )
    run1 = run_skill_for_alert(alert, now=t0)
    assert "tool:monitoring-outage.watch" in run1.receipt
    run2 = run_skill_for_alert(alert, now=_after_soak(t0))
    assert "status: done" in run2.receipt


def test_gluetun_never_restart_in_receipt() -> None:
    assert receipt_violates_vpn_law("next: docker restart gluetun now") is not None


def test_config_driven_wireguard_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    t0 = 2_000_000.0
    alert = parse_monitoring_message("wg0-handshake stale")
    run_skill_for_alert(alert, now=t0)
    skill = match_skill(alert)
    assert skill and skill.fix_action.get("kind") == "docker_restart_if_down"
    with patch("chip_relay.monitoring_skills._docker_running", return_value=False), patch(
        "chip_relay.monitoring_skills._docker_restart", return_value=(True, "exit=0")
    ) as restart:
        run = run_skill_for_alert(alert, now=_after_soak(t0))
    restart.assert_called_once()
    assert restart.call_args[0][0] == "wireguard"
    assert "status: done" in run.receipt


def test_overlay_skill_watch_only_without_code_change(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    alert = parse_monitoring_message("brand-new recurring cron failure xyz-9000")
    skill = create_overlay_skill(alert)
    assert skill.fix_action.get("kind") == "watch_only"
    t0 = 4_000_000.0
    run_skill_for_alert(alert, skill=skill, create_if_missing=False, now=t0)
    run = run_skill_for_alert(alert, skill=skill, create_if_missing=False, now=_after_soak(t0))
    assert "watch_only" in run.learning_line or "cataloged" in run.receipt.lower()


def test_new_fix_action_via_overlay_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    overlay = {
        "skills": [
            {
                "id": "custom-disk-pressure",
                "title": "disk pressure",
                "match_any": ["disk pressure"],
                "fix_action": {"kind": "homelab_delegate", "message": "check downloads mount"},
                "followup": "delegate:homelabdev disk pressure",
            }
        ]
    }
    path = tmp_path / "chip_state" / "monitoring_skills_overlay.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(overlay), encoding="utf-8")
    alert = parse_monitoring_message("disk pressure advisor timeout")
    skill = match_skill(alert)
    assert skill and skill.id == "custom-disk-pressure"
    t0 = 5_000_000.0
    run_skill_for_alert(alert, now=t0)
    run = run_skill_for_alert(alert, now=_after_soak(t0))
    assert "homelab" in run.receipt.lower()


def test_effective_monitoring_content_embed_only() -> None:
    embed = SimpleNamespace(
        title="WireGuard handshake stale",
        description="wg0 latest handshake > 180s",
        author=None,
        footer=None,
        fields=[],
    )
    msg = SimpleNamespace(
        content="",
        embeds=[embed],
        attachments=[],
        author=SimpleNamespace(name="Razzorstack Bot"),
    )
    text = effective_monitoring_content(msg)
    assert "WireGuard" in text


def test_gateway_routes_webhook_monitor_post() -> None:
    room = _monitoring_room()
    msg = SimpleNamespace(
        id=99,
        content="Monitoring Bot: gluetun health probe 404",
        author=SimpleNamespace(id=555, name="Monitoring Bot", bot=True),
        attachments=[],
        webhook_id=4242,
    )
    decision = classify_gateway_message(
        msg,
        relay_bot_user_id=1,
        room=room,
        environ={MONITORING_LISTEN_ENV: "1"},
    )
    assert decision is not None
    assert decision.kind == RouteKind.MONITORING_ALERT


def test_gateway_routes_embed_only_razzorstack_bot() -> None:
    room = _monitoring_room()
    embed = SimpleNamespace(
        title="wg0-handshake",
        description="latest handshake stale (240s)",
        author=None,
        footer=None,
        fields=[],
    )
    msg = SimpleNamespace(
        id=100,
        content="",
        embeds=[embed],
        author=SimpleNamespace(id=556, name="Razzorstack Bot", bot=True),
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


def test_gateway_ignores_relay_bot_create_message() -> None:
    room = _monitoring_room()
    msg = SimpleNamespace(
        id=101,
        content="internal",
        author=SimpleNamespace(id=1, name="chip-relay", bot=True),
        attachments=[],
        webhook_id=None,
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


def test_pm_receipt_echo_filtered() -> None:
    body = (
        "claim: pm skill stale-vpn-gluetun-label-probe\n"
        "status: done\n"
        "evidence: tool:monitoring-skill.stale-vpn-gluetun-label-probe\n"
        "next: recorded retired target"
    )
    msg = SimpleNamespace(webhook_id=1, author=SimpleNamespace(name="pm"))
    assert is_pm_monitoring_receipt_echo(msg, body) is True


def test_post_target_refuses_plex_fix() -> None:
    ok, reason = post_target_allowed(
        webhook_env="CHIP_WEBHOOK_MONITORING",
        channel_id=PLEX_FIX_CHANNEL_ID,
        webhook_url="https://discord.com/api/webhooks/x",
    )
    assert ok is False
    assert "plex-fix" in reason


def test_forbidden_restart_gluetun_in_executor() -> None:
    from chip_relay.monitoring_skills import SkillDef

    policy = load_policy()
    skill = match_skill(parse_monitoring_message("wg0-handshake stale"))
    assert skill is not None
    skill_bad = SkillDef(
        id=skill.id,
        title=skill.title,
        match_any=skill.match_any,
        body_path=skill.body_path,
        fix_action={"kind": "docker_restart_if_down", "container": "gluetun"},
        followup=skill.followup,
    )
    fix = execute_fix_action(skill_bad, parse_monitoring_message("x"), policy=policy)
    assert fix.applied is False
    assert "gluetun" in fix.detail.lower()


def _catalog_fixture() -> dict:
    path = (
        Path(__file__).resolve().parent / "fixtures" / "monitoring_skill_catalog.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "skill_id,sample",
    [
        (sid, text)
        for sid, text in json.loads(
            (
                Path(__file__).resolve().parent
                / "fixtures"
                / "monitoring_skill_catalog.json"
            ).read_text(encoding="utf-8")
        )["samples"].items()
    ],
)
def test_catalog_fault_class_matches(skill_id: str, sample: str) -> None:
    catalog = _catalog_fixture()
    expected = catalog.get("sample_expected_skill", {}).get(skill_id, skill_id)
    skill = match_skill(parse_monitoring_message(sample))
    assert skill is not None, f"no match for {skill_id!r}: {sample!r}"
    assert skill.id == expected


@pytest.mark.parametrize(
    "skill_id,sample",
    [
        (sid, text)
        for sid, text in json.loads(
            (
                Path(__file__).resolve().parent
                / "fixtures"
                / "monitoring_skill_catalog.json"
            ).read_text(encoding="utf-8")
        )["samples"].items()
    ],
)
def test_catalog_short_blip_does_not_fix_before_soak(
    skill_id: str, sample: str, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / skill_id))
    t0 = 8_000_000.0
    alert = parse_monitoring_message(sample)
    blip = run_skill_for_alert(alert, now=t0)
    assert "tool:monitoring-outage.watch" in blip.receipt
    early = run_skill_for_alert(alert, now=t0 + 120)
    assert "tool:monitoring-outage.watch" in early.receipt
    assert "status: done" not in early.receipt


def test_catalog_excluded_noise_does_not_match() -> None:
    for line in _catalog_fixture()["excluded_non_faults"]:
        alert = parse_monitoring_message(line)
        assert match_skill(alert) is None
        assert should_ignore_alert(alert) is True
        run = run_skill_for_alert(alert)
        assert run.ignored is True
        assert run.receipt == ""
        assert run.created is False


def test_handle_monitoring_alert_ignores_status_noise_without_webhook(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    line = "🟢 Monitoring API started. Auto-restart and port sync enabled."
    room = _monitoring_room()
    environ = {
        MONITORING_LISTEN_ENV: "1",
        "CHIP_WEBHOOK_MONITORING": "https://example.invalid/monitoring",
    }
    calls: list[tuple[str, str, str]] = []

    def webhook(url: str, username: str, body: str) -> WebhookResult:
        calls.append((url, username, body))
        return WebhookResult(status=204)

    result = handle_monitoring_alert(
        room,
        content=line,
        channel_id=room.channel_id,
        webhook=webhook,
        environ=environ,
    )
    assert result.ok is True
    assert result.webhook_calls == 0
    assert calls == []
    assert not overlay_registry_path().is_file()


def test_handle_monitoring_alert_posts_real_fault_on_soak_path(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    line = "AirVPN port-health: gluetun /v1/portforward 404 No such container"
    room = _monitoring_room()
    environ = {
        MONITORING_LISTEN_ENV: "1",
        "CHIP_WEBHOOK_MONITORING": "https://example.invalid/monitoring",
    }
    calls: list[str] = []

    def webhook(url: str, username: str, body: str) -> WebhookResult:
        calls.append(body)
        return WebhookResult(status=204)

    result = handle_monitoring_alert(
        room,
        content=line,
        channel_id=room.channel_id,
        webhook=webhook,
        environ=environ,
    )
    assert result.ok is True
    assert result.webhook_calls == 1
    assert len(calls) == 1
    assert "tool:monitoring-outage.watch" in calls[0]


def test_no_skill_health_checks_or_restarts_gluetun_container() -> None:
    import json as _json

    from chip_relay.monitoring_skills import REGISTRY_FILE

    raw = _json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    for row in raw.get("skills") or []:
        action = row.get("fix_action") or {}
        kind = str(action.get("kind") or "")
        blob = _json.dumps(action).lower()
        assert action.get("container") != "gluetun"
        assert "docker_health" not in kind
        if kind == "docker_restart_if_down":
            assert "gluetun" not in blob
        if "gluetun" in str(row.get("id", "")).lower() or "gluetun" in _json.dumps(
            row.get("match_any") or []
        ).lower():
            assert action.get("kind") == "record_retired_target"
            assert action.get("target") == "gluetun"


def test_lumocheck_manual_note_only_after_soak(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    t0 = 9_500_000.0
    alert = parse_monitoring_message("lumocheck timed out waiting for lumo-tamer")
    run_skill_for_alert(alert, now=t0)
    run = run_skill_for_alert(alert, now=_after_soak(t0))
    assert run.skill_id == "lumocheck-lumo-tamer-timeout"
    assert "status: done" in run.receipt
    assert "manual" in run.receipt.lower()
    assert "delegate:homelabdev" not in run.receipt.lower()


def test_gluetun_label_alert_only_retires_probe(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    t0 = 9_000_000.0
    alert = parse_monitoring_message("VPN (Gluetun) health check failed")
    run_skill_for_alert(alert, now=t0)
    run = run_skill_for_alert(alert, now=_after_soak(t0))
    assert run.skill_id == "stale-vpn-gluetun-label-probe"
    assert "status: done" in run.receipt
    assert "wireguard" in run.receipt.lower() or "retired" in run.receipt.lower()
