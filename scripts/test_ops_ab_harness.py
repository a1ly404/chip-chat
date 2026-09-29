"""Ops A/B harness tests: spend gates, transcript logging, scoring, gates.

Contract under test (per ruling):
- $0.20 alert / $0.25 refuse on the session tracker, fail-closed.
- Transcript rows are append-only JSONL with ts/arm/kind/actor/content.
- The runner refuses LIVE without --go-receipt AND CHIP_OPS_AB_GO=1.
- chip.collab absence = refuse (no improvised engine).
- Scoring: ground-truth-class discovery (2) + falsifiable (1) + cite (1).
"""

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

osmod = importlib.import_module("chip.spend_tracker")
opsy = importlib.import_module("chip.transcript_logger")
ab = importlib.import_module("chip.ops_session_a_b")


@pytest.fixture
def hidden_collab(monkeypatch):
    """Remove both the sys.modules entry and the package attr (post-merge main)."""
    import chip

    had_attr = hasattr(chip, "collab")
    if had_attr:
        monkeypatch.delattr(chip, "collab", raising=False)
    monkeypatch.delitem(sys.modules, "chip.collab", raising=False)
    yield
    if had_attr:
        monkeypatch.setattr(chip, "collab", sys.modules.get("chip.collab"), raising=False)


def test_missing_ledger_file_starts_zero(tmp_path):
    tracker = osmod.SpendTracker(tmp_path / "unused-path" / "spend.jsonl")
    assert tracker.cumulative_usd == 0.0


def test_corrupt_jsonl_row_fails_closed(tmp_path):
    ledger = tmp_path / "spend.jsonl"
    ledger.write_text('{"session_usd": 0.01}\nnot-json-garbage\n')
    with pytest.raises(osmod.SpendRefused):
        osmod.SpendTracker(ledger)


def test_older_numeric_field_in_row_fails(tmp_path):
    ledger = tmp_path / "spend.jsonl"
    ledger.write_text('{"session_usd": "abc"}\n')
    with pytest.raises(osmod.SpendRefused):
        osmod.SpendTracker(ledger)


def test_blank_lines_between_rows_count_correctly(tmp_path):
    ledger = tmp_path / "spend.jsonl"
    ledger.write_text('{"session_usd": 0.10}\n\n{"session_usd": 0.05}\n')
    tracker = osmod.SpendTracker(ledger)
    assert abs(tracker.cumulative_usd - 0.15) < 1e-9


def test_session_cap_refuses(tmp_path):
    tracker = osmod.SpendTracker(tmp_path / "spend.jsonl", arm="t", cap=0.25)
    import subprocess, sys as _sys

    # below cap: ok
    tracker = osmod.SpendTracker(tmp_path / "spend.jsonl", arm="t", cap=0.25)
    tracker.record_call(0.10, "r1", on_refuse=lambda: None)
    tracker.record_call(0.13, "r2", on_refuse=lambda: None)
    with pytest.raises(osmod.SpendRefused):
        tracker.record_call(0.03, "r3", on_refuse=lambda: None)


def test_session_alert_fires_once(tmp_path, capsys):
    tracker = osmod.SpendTracker(tmp_path / "s.jsonl", arm="t", cap=0.25, alert=0.20)
    tracker.record_call(0.19, "a", on_refuse=lambda: None)
    tracker.record_call(0.02, "b", on_refuse=lambda: None)
    assert tracker.alerted is True and tracker.cumulative_usd >= 0.20
    assert "SPEND ALERT" in capsys.readouterr().err


def test_garbage_spend_refuses(tmp_path):
    tracker = osmod.SpendTracker(tmp_path / "s.jsonl")
    with pytest.raises(osmod.SpendRefused):
        tracker.record_call("nonsense", "x", on_refuse=lambda: None)


def test_transcript_logger_roundtrip(tmp_path):
    log = opsy.TranscriptLogger("solo", root=tmp_path)
    row = log.event("reply", "chipchatdev", "hello", tokens_completion=42)
    assert json.loads(log.path.read_text().splitlines()[0])["actor"] == "chipchatdev"
    assert row["arm"] == "solo" and "ts" in row


def test_score_rubric(tmp_path):
    t = tmp_path / "trio.jsonl"
    rows = [
        {"ts": "x", "arm": "trio", "kind": "decision", "actor": "chair",
         "content": "probe-source falsifiability: reachability outside the tunnel, "
                    "re-probe with verified_as_of, inbound_reachable=False at 38.88.124.105."},
    ]
    t.write_text("\n".join(json.dumps(r) for r in rows))
    result = ab.score_arm(t)
    assert result["hits"]["ground_truth_class"] is True
    assert result["hits"]["falsifiable"] is True
    assert result["score"] == 4


def test_score_empty_transcript_is_zero(tmp_path):
    p = tmp_path / "solo.jsonl"
    p.write_text("")
    result = ab.score_arm(p)
    assert result["score"] == 0


def _simulate_absent_collab():
    """Force ImportError for chip.collab even if another test already imported it."""
    import chip as chip_pkg

    sentinel = sys.modules.get("chip.collab")
    had_attr = hasattr(chip_pkg, "collab")
    attr = getattr(chip_pkg, "collab", None) if had_attr else None
    sys.modules["chip.collab"] = None
    if had_attr:
        delattr(chip_pkg, "collab")
    return chip_pkg, sentinel, had_attr, attr


def _restore_collab(chip_pkg, sentinel, had_attr, attr):
    if sentinel is not None:
        sys.modules["chip.collab"] = sentinel
    elif "chip.collab" in sys.modules:
        del sys.modules["chip.collab"]
    if had_attr and attr is not None:
        chip_pkg.collab = attr
        sys.modules["chip.collab"] = attr


def test_missing_collab_refuses(monkeypatch, tmp_path):
    chip_pkg, sentinel, had_attr, attr = _simulate_absent_collab()
    try:
        with pytest.raises(RuntimeError) as exc:
            ab._maybe_collab()
        assert "fail-closed" in str(exc.value) or "refuses" in str(exc.value)
    finally:
        _restore_collab(chip_pkg, sentinel, had_attr, attr)


def test_live_double_gate_refuses():
    assert ab.main(["--live"]) == 2


def test_go_receipt_gate(tmp_path):
    receipt = tmp_path / "go.txt"
    receipt.write_text("GO RAZ-335-ops-ab lylamorning\n")
    token = ab.gate_check(receipt)
    assert token == "lylamorning"
    bad = tmp_path / "bad.txt"
    bad.write_text("go vibes\n")
    with pytest.raises(SystemExit):
        ab.gate_check(bad)


def test_participants_sealed_and_scenario_contents():
    assert ab.SOLO_PARTICIPANTS == ["chipchatdev"]
    assert ab.TRIO_PARTICIPANTS == ["opsdev", "homelabdev", "chipchatdev"]
    assert len(ab.SOLO_PARTICIPANTS) < len(ab.TRIO_PARTICIPANTS)
    # scenario contains the probing target + the confound, and no leaks
    text = ab.SCENARIO_GOAL.lower()
    for term in ("54543", "38.88.124.105", "probe source"):
        assert term in text
    for leak in ("inbound_reachable=false", "config actual_reachability"):
        assert leak not in text


def test_wake_text_size_guard(tmp_path, monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return type("P", (), {"returncode": 0,
                              "stdout": json.dumps({"reply": "r", "session_usd": 0.01}),
                              "stderr": ""})()

    big = "x" * 2100
    with pytest.raises(ValueError):
        ab._chip_room_reply(tmp_path, "rm", "chipchatdev", big, live=False)


def test_arm_flow_mock_blocked_premerge(tmp_path, monkeypatch):
    """Runner does nothing live pre-merge: _maybe_collab must refuse."""
    monkeypatch.setenv("CHIP_OPS_AB_LIVE", "0")
    chip_pkg, sentinel, had_attr, attr = _simulate_absent_collab()
    try:
        with pytest.raises(RuntimeError):
            ab._maybe_collab()
    finally:
        _restore_collab(chip_pkg, sentinel, had_attr, attr)


def test_mock_arm_refused_by_default():
    """pm review fix #4: default arm run must not mutate the room board."""
    assert ab.main(["--arm", "solo"]) == 4


def test_mock_arm_allowed_with_explicit_flag(tmp_path, monkeypatch):
    """--allow-mock-mutation + absent collab still refuses (fail-closed)."""
    monkeypatch.delenv("CHIP_OPS_AB_GO", raising=False)
    chip_pkg, sentinel, had_attr, attr = _simulate_absent_collab()
    try:
        assert ab.main(["--arm", "solo", "--allow-mock-mutation"]) == 1
    finally:
        _restore_collab(chip_pkg, sentinel, had_attr, attr)


def test_record_call_signature_matches_runner(tmp_path):
    """pm review fix #1: runner calls record_call(cost, label) w/o on_refuse."""
    tracker = osmod.SpendTracker(tmp_path / "s.jsonl", arm="t")
    row = tracker.record_call(0.01, "solo/hypothesis")  # no on_refuse kwarg
    assert row.cumulative_usd == 0.01


def test_live_token_threaded_and_refused_empty(monkeypatch, tmp_path):
    receipt = tmp_path / "go.txt"
    receipt.write_text("GO RAZ-335-ops-ab lyla\n")
    token = ab.gate_check(receipt)
    assert token == "lyla"
    monkeypatch.setattr(ab, "_LIVE_STATE", {"live": True, "token": ""})
    with pytest.raises(RuntimeError):
        ab.run_solo(Path("/tmp"), osmod.SpendTracker(tmp_path / "s.jsonl"),
                    opsy.TranscriptLogger("solo", root=tmp_path), "")


def test_one_live_predicate(tmp_path):
    """pm review fix #2: is_live() is fed only by main's --live path."""
    assert ab.is_live() is False
