"""Pipeline wiring tests (no live OpenRouter)."""

from __future__ import annotations

from chip_relay.case_intake import CaseFile
from chip_relay.case_intake_pipeline import extract_case_json, channel_intake_active
from chip_relay.dispatch import Incoming, WebhookResult


def test_extract_json_from_fence() -> None:
    raw = '```json\n{"id":"a","description":"d","repro_context":"r","evidence_path":"e"}\n```'
    parsed = extract_case_json(raw)
    assert parsed["id"] == "a"


def test_channel_intake_requires_flag() -> None:
    assert channel_intake_active("monitoring-logs", {"CASE_INTAKE_ENABLED": "0"}) is False
    assert channel_intake_active("monitoring-logs", {"CASE_INTAKE_ENABLED": "1"}) is True
    assert channel_intake_active("chip-dev", {"CASE_INTAKE_ENABLED": "1"}) is False


def test_handle_intake_posts_echo(monkeypatch, tmp_path) -> None:
    from chip import config
    from chip_relay import case_intake_pipeline as pipe
    from chip_relay.config import RoomBinding

    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(pipe, "_open_cache_path", lambda: tmp_path / "cache.json")
    (tmp_path / "cache.json").write_text('{"cases": []}\n', encoding="utf-8")
    pipe._STATE = {"echo_pending": {}, "recent_cases": [], "pager_last": {}, "strike_until": {}}
    monkeypatch.setattr(
        "chip_relay.case_intake_coverage.load",
        lambda: {
            "alerts_seen": 0,
            "parsed_clean": 0,
            "pending_clarify": 0,
            "failed_closed": 0,
            "misroutes": 0,
            "failed_open": 0,
            "consecutive_clean": 0,
            "open_failures_by_class": {},
            "killed_classes": [],
            "feed_halted": False,
            "halt_reason": "",
            "fixtures_captured": [],
        },
    )
    monkeypatch.setattr("chip_relay.case_intake_coverage.save", lambda _s: None)
    monkeypatch.setattr("chip_relay.case_intake_coverage.check_feed_halt", lambda _e: (False, ""))
    monkeypatch.setattr("chip_relay.case_intake_coverage.class_killed", lambda _c: False)
    monkeypatch.setattr("chip_relay.case_intake_coverage.record_outcome", lambda *a, **k: {})

    def fake_live(msg: str, model: str) -> dict:
        return {
            "id": "live-1",
            "description": "dup shape",
            "repro_context": "ctx",
            "evidence_path": "docs/x",
            "_tokens": 9,
        }

    monkeypatch.setattr(pipe, "live_parser", fake_live)
    posts: list[str] = []

    def webhook(url: str, user: str, body: str) -> WebhookResult:
        posts.append(body)
        return WebhookResult(status=204)

    room = RoomBinding(
        id="dev",
        as_agent="dev",
        webhook_env="CHIP_WEBHOOK_DEV",
        discord_channel="monitoring-logs",
        channel_id="1",
        monitoring_listen=True,
    )
    incoming = Incoming(
        channel="monitoring-logs",
        content="plex wont play on atv",
        author="ally",
        author_id="187044496872046592",
    )
    env = {
        "CASE_INTAKE_ENABLED": "1",
        "CASE_INTAKE_CHANNELS": "monitoring-logs",
        "CHIP_WEBHOOK_MONITORING": "http://example.invalid",
    }
    result = pipe.handle_case_intake(incoming, room, webhook=webhook, environ=env)
    assert result.ok
    assert posts and "solver: mock" in posts[0]
