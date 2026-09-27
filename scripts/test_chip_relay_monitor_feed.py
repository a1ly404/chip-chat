"""Phase-A monitor feed tests (Grok round-7 go-live, ENVELOPES.md phase table)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from chip_relay import monitor_feed


@pytest.fixture()
def feed_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    for var in (
        "CHIP_FEED_ENABLED",
        "CHIP_FEED_HOST",
        "CHIP_FEED_INTERVAL_S",
        "CHIP_FEED_TARGETS",
        "CHIP_STAND_DOWN",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# env gates + target parsing + interval clamping
# ---------------------------------------------------------------------------


def test_feed_disabled_by_default() -> None:
    assert monitor_feed.feed_enabled({}) is False
    assert monitor_feed.start_feed_polling({}) is None


def test_feed_enabled_truthy_variants() -> None:
    for val in ("1", "true", "yes", "on", "TRUE"):
        assert monitor_feed.feed_enabled({"CHIP_FEED_ENABLED": val}) is True


def test_feed_disabled_falsy_variants() -> None:
    for val in ("0", "false", "", "no", "garbage"):
        assert monitor_feed.feed_enabled({"CHIP_FEED_ENABLED": val}) is False


def test_default_targets() -> None:
    assert monitor_feed._targets({}) == {"monitoring-api": 48082, "discord-bot": 48080}


def test_targets_env_override() -> None:
    t = monitor_feed._targets({"CHIP_FEED_TARGETS": "foo=9999"})
    assert t == {"monitoring-api": 48082, "discord-bot": 48080, "foo": 9999}


def test_targets_invalid_entries_ignored() -> None:
    t = monitor_feed._targets({"CHIP_FEED_TARGETS": "x=notanumber,,y=3"})
    assert t == {"monitoring-api": 48082, "discord-bot": 48080, "y": 3}


def test_poll_interval_default_and_clamp() -> None:
    assert monitor_feed._poll_interval({}) == 300.0
    assert monitor_feed._poll_interval({"CHIP_FEED_INTERVAL_S": "90"}) == 90.0
    assert monitor_feed._poll_interval({"CHIP_FEED_INTERVAL_S": "30"}) == 60.0  # min clamp
    assert monitor_feed._poll_interval({"CHIP_FEED_INTERVAL_S": "junk"}) == 300.0


# ---------------------------------------------------------------------------
# alert derivation
# ---------------------------------------------------------------------------


def _feeder_receipt(target: str, status: str, extra: dict | None = None) -> dict:
    return {
        "target": target,
        "host": "x",
        "port": 48080,
        "http_status": 200,
        "error": "",
        "json": {"status": status, **(extra or {})},
        "ts": "t",
    }


def test_derive_alerts_healthy_targets_silent() -> None:
    healths = {
        "monitoring-api": _feeder_receipt("monitoring-api", "healthy"),
        "discord-bot": _feeder_receipt("discord-bot", "ok", {"loops_ok": True}),
    }
    assert monitor_feed.derive_alerts(healths) == []


def test_derive_alerts_discord_bot_loops_ok_false() -> None:
    healths = {"discord-bot": _feeder_receipt("discord-bot", "ok", {"loops_ok": False})}
    alerts = monitor_feed.derive_alerts(healths)
    assert len(alerts) == 1
    assert alerts[0]["key"] == "discord-bot"
    assert "loops_ok=False" in str(alerts[0]["detail"])


def test_derive_alerts_discord_bot_status_not_ok() -> None:
    healths = {"discord-bot": _feeder_receipt("discord-bot", "unhealthy")}
    alerts = monitor_feed.derive_alerts(healths)
    assert alerts and alerts[0]["key"] == "discord-bot"


def test_derive_alerts_monitoring_api_unhealthy() -> None:
    healths = {"monitoring-api": _feeder_receipt("monitoring-api", "degraded")}
    alerts = monitor_feed.derive_alerts(healths)
    assert len(alerts) == 1
    assert alerts[0]["key"] == "monitoring-api"


def test_derive_alerts_transport_failure() -> None:
    healths = {
        "monitoring-api": {
            "target": "monitoring-api",
            "host": "x",
            "port": 48082,
            "http_status": 0,
            "error": "URLError: connection refused",
            "json": {},
            "ts": "t",
        }
    }
    alerts = monitor_feed.derive_alerts(healths)
    assert any(a["key"] == "monitoring-api-probe" for a in alerts)


# ---------------------------------------------------------------------------
# classification + snapshot plumbing
# ---------------------------------------------------------------------------


def test_classify_receipts_verdicts_and_skip_key(feed_env: Path) -> None:
    healths = {"discord-bot": _feeder_receipt("discord-bot", "unhealthy")}
    receipt = monitor_feed.classify_receipts(healths, environ={}, persist=False)
    by_key = {v["key"]: v["verdict"] for v in receipt["verdicts"]}
    assert by_key["discord-bot"] == "actionable"
    assert receipt["stand_down"] is False


def test_classify_receipts_stand_down_flagged(feed_env: Path) -> None:
    receipt = monitor_feed.classify_receipts({}, environ={"CHIP_STAND_DOWN": "1"}, persist=False)
    assert receipt["stand_down"] is True


def test_tick_and_snapshot_written(feed_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    canned = {
        "monitoring-api": _feeder_receipt("monitoring-api", "healthy"),
        "discord-bot": _feeder_receipt("discord-bot", "ok", {"loops_ok": False}),
    }
    monkeypatch.setattr(monitor_feed, "_fetch_health", lambda host, port, name: canned[name])
    receipt = monitor_feed.tick(persist=True)
    snapshot = monitor_feed.snapshot_path()
    assert snapshot.is_file()
    loaded = json.loads(snapshot.read_text(encoding="utf-8"))
    assert loaded["verdicts"] == receipt["verdicts"]
    assert len(receipt["verdicts"]) == 1
    assert receipt["verdicts"][0]["verdict"] == "actionable"
    assert (feed_env / "monitor_state.json").is_file()


def test_run_feed_loop_one_tick_then_stop(feed_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    stop = threading.Event()

    def fake_tick(**kwargs):
        calls.append(kwargs)
        stop.set()
        return {"ts": "t", "verdicts": [], "stand_down": False}

    monkeypatch.setattr(monitor_feed, "tick", fake_tick)
    monkeypatch.setenv("CHIP_FEED_INTERVAL_S", "60")
    monitor_feed.run_feed_loop(stop)
    assert len(calls) == 1
