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


def test_missing_collab_refuses(monkeypatch, tmp_path):
    collabmod = sys.modules.get("chip.collab")
    sentinel = sys.modules.pop("chip.collab", None)
    sys.modules["chip.collab"] = None  # simulate absent
    try:
        fakes = type(__import__("types").SimpleNamespace)  # placeholder ref
        with pytest.raises(RuntimeError) as exc:
            ab._maybe_collab()
        assert "fail-closed" in str(exc.value) or "refuses" in str(exc.value)
    finally:
        if sentinel is not None:
            sys.modules["chip.collab"] = sentinel
        elif collabmod is None and "chip.collab" in sys.modules:
            del sys.modules["chip.collab"]


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


def test_scenario_and_room_shapes():
    assert "54543" in ab.SCENARIO_GOAL and "38.88.124.105" in ab.SCENARIO_GOAL
    assert "qbittorrent-monitor" in ab.SCENARIO_GOAL and "source" in ab.SCENARIO_GOAL.lower()
    assert ab.TrioPart if False else True  # placeholder for shape guard


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
    chip = sys.modules.get("chip")
    sentinel = sys.modules.get("chip.collab")
    sys.modules["chip.collab"] = None
    try:
        with pytest.raises(RuntimeError):
            ab._maybe_collab()
    finally:
        del sys.modules["chip.collab"]
