"""Grok round-5 law harness: malformed envelopes must be rejected (spec verbatim)."""

import json

import pytest

from chip import claims, store
from chip.authorize import authorize


def _seed(tmp_path, monkeypatch, rows):
    thread = tmp_path / "threads" / "dev.jsonl"
    thread.parent.mkdir(parents=True, exist_ok=True)
    thread.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(store, "thread_path", lambda room: thread)


# test_claim_requires_persona_and_scope
def test_claim_requires_persona_and_scope(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: chipchatdev"},          # no scope
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: chipchatdev *"},        # wildcard
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: ally reserved-scope"},  # reserved id
        {"role": "assistant", "agent": "chipchatdev", "content": "I say claim: chipchatdev buried-scope"},  # buried
        {"role": "assistant", "agent": "dev", "content": "claim: chipchatdev spoofed-scope"},    # persona ≠ speaker
        {"role": "assistant", "agent": "chipchatdev", "content": "\u200bclaim: chipchatdev ok-scope"},  # ZWSP (cleaned → valid)
        {"role": "assistant", "agent": "chipchatdev", "content": "claim： chipchatdev nfkc-scope"},  # fullwidth colon (NFKC → valid)
    ])
    out = claims.scan_claims("dev")
    assert "reserved-scope" not in out and "buried-scope" not in out and "spoofed-scope" not in out
    assert "*" not in out
    assert out["ok-scope"]["persona"] == "chipchatdev"
    assert out["nfkc-scope"]["persona"] == "chipchatdev"  # NFKC normalization is the fix, not a bypass


# test_claim_first_wins_duplicate_counted
def test_claim_first_wins_duplicate_counted(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: chipchatdev fix"},
        {"role": "assistant", "agent": "dev", "content": "claim: dev fix"},
    ])
    out = claims.scan_claims("dev")
    assert out["fix"]["persona"] == "chipchatdev" and out["fix"]["duplicates"] == 1


# test_claim_done_only_own_active
def test_claim_done_only_own_active(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: chipchatdev mine"},
        {"role": "assistant", "agent": "dev", "content": "claim: dev theirs"},
        {"role": "assistant", "agent": "dev", "content": "claim done"},  # dev releases THEIRS only
    ])
    out = claims.scan_claims("dev")
    assert out["mine"]["status"] == "active"
    assert out["theirs"]["status"] == "done"


# test_parked_requires_why_next_evidence — parser-level: parked shape lives in law;
# here we prove the malformed-parked string is NOT a claim and doesn't touch claims.
def test_parked_is_not_a_claim(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "chipchatdev", "content": "parked: env down"},
    ])
    out = claims.scan_claims("dev")
    assert out == {}  # parked never creates/moves claims


# test_learning_shape_and_class_enum — learning lines are not claims; token-shaped secrets never parsed
def test_learning_lines_and_token_shapes_ignored(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "chipchatdev", "content": "learning: x\nclass: script\nwhen: 2026-09-26\nevidence: PR #1\nsk-abc123def456ghi789"},
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: chipchatdev real"},
    ])
    out = claims.scan_claims("dev")
    assert list(out.keys()) == ["real"]


# test_verified_as_of_bound_to_checkbox — verified-as-of text never moves claims
def test_verified_as_of_not_a_claim(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "chipchatdev", "content": "verified-as-of: 2026-09-26 — box ✓"},
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: chipchatdev ev"},
    ])
    out = claims.scan_claims("dev")
    assert list(out.keys()) == ["ev"]


# test_handoff_requires_confirm_fields — bare handoff line never moves ownership
def test_handoff_line_not_a_claim(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "pm", "content": "handoff: dev the-work"},
        {"role": "assistant", "agent": "pm", "content": "claim: pm the-work"},
    ])
    out = claims.scan_claims("dev")
    assert out["the-work"]["persona"] == "pm"  # handoff prose doesn't grant claims


# test_envelope_must_be_whole_message
def test_envelope_must_be_whole_message(tmp_path, monkeypatch):
    assert claims.parse_claim_line("claim: dev scope1") is not None
    assert claims.parse_claim_line("hello claim: dev scope1") is None
    assert claims.parse_claim_line("```claim: dev scope1```") is None


# test_stand_down_blocks_claim_and_execute
def test_stand_down_blocks_claim_and_execute(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: chipchatdev scoped"},
    ])
    env = {"CHIP_STAND_DOWN": "1"}
    ok, reason = authorize("dev", "scoped", "execute", environ=env, room="dev", tool="pytest")
    assert ok is False and "stand-down" in reason
    ok, reason = authorize("chipchatdev", "scoped", "claim", environ=env, room="dev")
    assert ok is False and "stand-down" in reason


# authorize chokepoint extras
def test_authorize_execute_requires_claim_owner(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: chipchatdev scoped"},
    ])
    ok, reason = authorize("dev", "scoped", "execute", environ={}, room="dev", tool="pytest")
    assert ok is False and "claim_owner" in reason
    ok, reason = authorize("chipchatdev", "scoped", "execute", environ={}, room="dev", tool="pytest")
    assert ok is True


def test_authorize_rejects_unknown_tool_and_wildcard(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: chipchatdev scoped"},
    ])
    ok, reason = authorize("chipchatdev", "scoped", "execute", environ={}, room="dev", tool="rm")
    assert ok is False and "manifest" in reason
    ok, reason = authorize("chipchatdev", "*", "claim", environ={}, room="dev")
    assert ok is False and "wildcard" in reason


def test_authorize_human_hot_path_even_under_stand_down():
    ok, _ = authorize("ally", "x", "execute", environ={"CHIP_STAND_DOWN": "1"}, is_human_or_inject=True)
    assert ok is True


# round-7 guardrail: service allowlist in the executor
def test_authorize_blocks_native_profile_targets(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: chipchatdev restart-plex"},
    ])
    ok, reason = authorize("chipchatdev", "restart-plex", "execute", environ={}, room="dev", tool="pytest")
    assert ok is False and "blocked target" in reason


def test_authorize_allows_allowlisted_service_target(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"role": "assistant", "agent": "chipchatdev", "content": "claim: chipchatdev restart-discord-bot"},
    ])
    ok, reason = authorize("chipchatdev", "restart-discord-bot", "execute", environ={}, room="dev", tool="pytest")
    assert ok is True
