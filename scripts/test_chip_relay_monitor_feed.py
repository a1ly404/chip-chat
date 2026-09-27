"""Phase-A monitor feed tests (Grok round-7 go-live, ENVELOPES.md phase table)."""

from __future__ import annotations

import json
import os
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


# ---------------------------------------------------------------------------
# probe retry (soak fix: transient transport blips)
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload: bytes = b'{"status": "healthy"}') -> None:
        self.status = 200
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_fetch_health_retries_transport_blip(feed_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request as ur

    calls = {"n": 0}

    def flaky_urlopen(url: str, timeout: float) -> _FakeResp:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("timed out")
        return _FakeResp()

    monkeypatch.setattr(ur, "urlopen", flaky_urlopen)
    h = monitor_feed._fetch_health("h", 48082, "monitoring-api")
    assert h["http_status"] == 200
    assert not h.get("error")
    assert calls["n"] == 2
    assert h["attempts"] == 2


def test_fetch_health_http_error_not_retried(feed_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error
    import urllib.request as ur

    calls = {"n": 0}

    def always_503(url: str, timeout: float) -> _FakeResp:
        calls["n"] += 1
        raise urllib.error.HTTPError(url, 503, "unavailable", None, None)

    monkeypatch.setattr(ur, "urlopen", always_503)
    h = monitor_feed._fetch_health("h", 48080, "discord-bot")
    assert h["http_status"] == 503
    assert calls["n"] == 1


def test_fetch_health_persistent_transport_error(feed_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request as ur

    calls = {"n": 0}

    def dead(url: str, timeout: float) -> _FakeResp:
        calls["n"] += 1
        raise ConnectionError("refused")

    monkeypatch.setattr(ur, "urlopen", dead)
    h = monitor_feed._fetch_health("h", 48080, "discord-bot")
    assert h["http_status"] == 0
    assert calls["n"] == 2
    assert "ConnectionError" in str(h["error"])


# ---------------------------------------------------------------------------
# #chip-dev routing (verdict-transition lines, cooldown, stand-down)
# ---------------------------------------------------------------------------


def _receipt_actionable(key: str = "monitoring-api", detail: str = "status=degraded") -> dict:
    return {
        "ts": "t",
        "stand_down": False,
        "results": {},
        "verdicts": [{"key": key, "detail": detail, "ok": False, "verdict": "actionable"}],
    }


class _RecordingWebhook:
    def __init__(self, status: int = 204) -> None:
        self.status = status
        self.posts: list[tuple[str, str]] = []

    def __call__(self, url: str, username: str, content: str):
        self.posts.append((username, content))
        return type("R", (), {"status": self.status})()


def test_post_alerts_posts_new_fingerprint(feed_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_FEED_ENABLED", "1")
    monkeypatch.setenv("CHIP_WEBHOOK_DEV", "https://example.invalid/dev")
    hook = _RecordingWebhook()
    assert monitor_feed.post_alerts(_receipt_actionable(), environ=dict(os.environ), webhook=hook) == 1
    username, content = hook.posts[0]
    assert username == "chip-relay"
    assert content.startswith("[chip-feed] monitoring-api actionable")


def test_post_alerts_cooldowns_repeat(feed_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_FEED_ENABLED", "1")
    monkeypatch.setenv("CHIP_WEBHOOK_DEV", "https://example.invalid/dev")
    hook = _RecordingWebhook()
    receipt = _receipt_actionable()
    assert monitor_feed.post_alerts(receipt, environ=dict(os.environ), webhook=hook, now=1000.0) == 1
    assert monitor_feed.post_alerts(receipt, environ=dict(os.environ), webhook=hook, now=1100.0) == 0
    assert len(hook.posts) == 1  # cooldown honed at 4h per fingerprint


def test_post_alerts_dedup_flap_skip_never_post(feed_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time as time_mod

    monkeypatch.setenv("CHIP_FEED_ENABLED", "1")
    monkeypatch.setenv("CHIP_WEBHOOK_DEV", "https://example.invalid/dev")
    hook = _RecordingWebhook()
    receipt = {
        "ts": "t",
        "stand_down": False,
        "results": {},
        "verdicts": [
            {"key": "monitoring-api", "detail": "status=degraded", "ok": False, "verdict": "dedup"},
            {"key": "discord-bot", "detail": "loops_ok=False", "ok": False, "verdict": "flap"},
            {"key": "plex", "detail": "down", "ok": False, "verdict": "skip-key"},
        ],
    }
    assert monitor_feed.post_alerts(receipt, environ=dict(os.environ), webhook=hook, now=1000.0) == 0
    assert hook.posts == []


def test_post_alerts_stand_down_frozen(feed_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_FEED_ENABLED", "1")
    monkeypatch.setenv("CHIP_WEBHOOK_DEV", "https://example.invalid/dev")
    monkeypatch.setenv("CHIP_STAND_DOWN", "1")
    hook = _RecordingWebhook()
    assert monitor_feed.post_alerts(_receipt_actionable(), environ=dict(os.environ), webhook=hook) == 0
    assert hook.posts == []


def test_post_alerts_requires_webhook_env(feed_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_FEED_ENABLED", "1")
    monkeypatch.delenv("CHIP_WEBHOOK_DEV", raising=False)
    hook = _RecordingWebhook()
    assert monitor_feed.post_alerts(_receipt_actionable(), environ=dict(os.environ), webhook=hook) == 0


def test_post_alerts_failed_post_not_counted(feed_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_FEED_ENABLED", "1")
    monkeypatch.setenv("CHIP_WEBHOOK_DEV", "https://example.invalid/dev")
    hook = _RecordingWebhook(status=500)
    assert monitor_feed.post_alerts(_receipt_actionable(), environ=dict(os.environ), webhook=hook, now=1000.0) == 0
    # a later success (cooldown not yet stamped) posts again
    hook2 = _RecordingWebhook(status=204)
    assert monitor_feed.post_alerts(_receipt_actionable(), environ=dict(os.environ), webhook=hook2, now=1010.0) == 1
