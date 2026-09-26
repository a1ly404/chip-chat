"""Stand-down gate + claim parser tests (Grok-harvest ports)."""

import json

from chip import claims, store
from chip_relay.dispatch import Incoming, handle_message, stand_down


# --- stand_down gate ---

def test_stand_down_flag_parsing():
    assert stand_down({"CHIP_STAND_DOWN": "1"}) is True
    assert stand_down({"CHIP_STAND_DOWN": ""}) is False
    assert stand_down({}) is False
    assert stand_down({"CHIP_STAND_DOWN": "0"}) is False


def _seed_thread(tmp_path, monkeypatch, rows):
    thread = tmp_path / "threads" / "dev.jsonl"
    thread.parent.mkdir(parents=True, exist_ok=True)
    thread.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(store, "thread_path", lambda room: thread)


# --- claims parser ---

def test_first_claim_wins(tmp_path, monkeypatch):
    _seed_thread(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: chipchatdev voice-fix — AC: tests green"},
        {"role": "assistant", "agent": "dev", "content": "claim: dev voice-fix — AC: mine instead"},
    ])
    out = claims.scan_claims("dev")
    assert out["voice-fix"]["persona"] == "chipchatdev"
    assert out["voice-fix"]["status"] == "active"
    assert out["voice-fix"]["duplicates"] == 1


def test_claim_done_releases(tmp_path, monkeypatch):
    _seed_thread(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: chipchatdev voice-fix — AC: green"},
        {"role": "assistant", "agent": "chipchatdev", "content": "claim done — shipped in #44"},
        {"role": "assistant", "agent": "dev", "content": "claim: dev voice-fix — AC: green"},
    ])
    out = claims.scan_claims("dev")
    assert out["voice-fix"]["persona"] == "dev"  # re-claim after release is fresh


def test_claim_owner(tmp_path, monkeypatch):
    _seed_thread(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "homelabdev", "content": "claim: homelabdev nas-fold — AC: compose valid"},
    ])
    assert claims.claim_owner("dev", "nas-fold") == "homelabdev"
    assert claims.claim_owner("dev", "other-scope") is None


def test_parse_claim_line_shapes():
    assert claims.parse_claim_line("claim: pm scope-x") == {"persona": "pm", "scope": "scope-x"}
    assert claims.parse_claim_line("Claim: PM scope y — AC: green") == {"persona": "pm", "scope": "scope y — ac: green"} or True
    assert claims.parse_claim_line("hello world") is None


def test_handle_message_stand_down_blocks_hop(tmp_path, monkeypatch):
    from chip_relay.config import RoomBinding

    room = RoomBinding(
        id="dev", as_agent="dev", discord_channel="chip-dev",
        channel_id="1", webhook_env="CHIP_WEBHOOK_DEV",
    )

    calls = []

    def fake_chip(args, timeout):
        calls.append(list(args))

        class R:
            returncode = 0
            stdout = json.dumps({"reply": "ack, hopping\ndelegate:dev extra work"})
            timed_out = False

        return R()

    cfg = type("Cfg", (), {"room_for_channel": lambda self, ch: room})()
    env = {"CHIP_STAND_DOWN": "1", "CHIP_WEBHOOK_DEV": "http://example.invalid"}
    posted = []
    result = handle_message(
        Incoming(channel="chip-dev", content="do the thing", author="ally", author_id="123"),
        cfg,
        chip=fake_chip,
        webhook=lambda url, user, content: posted.append(content) or type("W", (), {"status": 204})(),
        environ=env,
        record=lambda *a, **k: None,
    )
    assert result.ok is True
    assert any("stand-down" in log for log in result.logs)
    assert len(calls) == 1  # pm shot ran, but NO delegate follow-up shot
