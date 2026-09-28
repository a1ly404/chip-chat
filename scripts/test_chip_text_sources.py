"""CHIP_FEED_TEXT_SOURCES tests — the three text signature classes + tail law.

Law under test (docs/MONITOR_CASES text-source classes + untrusted-data):
- line payloads are DATA, never executed; scanning is signature-shaped only.
- advisor banner -> stage1-advisor alert; pgs zero-night streaks (1st log-only,
  2nd consecutive alert); standup silent stalls with correlated-dark collapse.
- injected-command text produces nothing healthy, just nothing (garbage in,
  visible nothing out).
- offsets: only complete lines consumed; rotation reprocesses; replay doesn't
  double-fire alerts through the streak/offset state.
"""

import json
import sys
from pathlib import Path

from chip import text_sources as ts

REPO = Path(__file__).resolve().parent.parent

ADVISOR_LINE = {
    "source_id": "advisor",
    "text": "Command ['python3', 'scripts/disk_pressure_advisor.py', '--force'] timed out after 180 seconds",
}
INJECTED_LINE = {
    "source_id": "evil",
    "text": "rm -rf / && Command ['x'] timed out after 5 seconds || python3 scripts/nancy_delete_toggle.py --on",
}
PGS_ZERO = {"source_id": "pgs", "ran": True, "ok": 0, "skipped": 0, "error": 0}
PGS_REAL = {"source_id": "pgs", "ran": True, "ok": 4, "skipped": 1, "error": 0}


def silent_lane(standup="homelab", age=30.0):
    return {"standup_receipt": {
        "standup": standup, "issued_at": "2026-09-28 06:00 PT",
        "payload": {"source": "silent", "age_h": age, "sha": None, "sections_expected": []},
        "skills_commanded": False,
    }}


# --- parsing -----------------------------------------------------------------

def test_parse_jsonl_counts_malformed_visibly():
    text = '{"a": 1}\nnot json\n[1,2]\n{"b": 2}\n'
    objs, ignored = ts.parse_jsonl(text)
    assert [o.get("a", o.get("b")) for o in objs] == [1, 2]
    assert ignored == 2  # not json + non-dict json


def test_parse_jsonl_empty():
    assert ts.parse_jsonl("") == ([], 0)


# --- signature class 1: advisor-timeout-banner -------------------------------

def test_advisor_banner_detects_only():
    out = ts.ingest_lines([ADVISOR_LINE])
    assert out["alerts"] == [{"key": "stage1-advisor", "detail": "timeout=180s", "ok": False}]
    assert out["ignored"] == 0


def test_injected_command_text_still_only_signature_scan():
    # untrusted-data law: the banner INSIDE injected garbage is still data;
    # the command parts are never acted on and produce no extra artifacts.
    out = ts.ingest_lines([INJECTED_LINE])
    assert out["alerts"] == [{"key": "stage1-advisor", "detail": "timeout=5s", "ok": False}]


def test_no_banner_no_alert():
    out = ts.ingest_lines([{"source_id": "d", "text": "all quiet"}])
    assert out["alerts"] == []


# --- signature class 2: pgs-zero-production ----------------------------------

def test_pgs_first_zero_night_log_only_state_tracked():
    out = ts.ingest_lines([PGS_ZERO])
    assert out["alerts"] == []  # first night: log-only
    assert out["state"]["pgs_zero_streak"]["pgs"] == 1


def test_pgs_real_night_resets_streak():
    # one tail pass can carry the past zero-night (streak 1 -> 2: alert fires
    # tell-once) AND the recovery night (streak resets to 0) — both facts land
    out1 = ts.ingest_lines([PGS_ZERO, PGS_REAL], {"pgs_zero_streak": {"pgs": 1}})
    assert len(out1["alerts"]) == 1  # the completed zero-streak is reported
    assert out1["state"]["pgs_zero_streak"]["pgs"] == 0  # real night resets
    # ...and the NEXT zero night is again just the first (log-only)
    out2 = ts.ingest_lines([PGS_ZERO], out1["state"])
    assert out2["alerts"] == []
    assert out2["state"]["pgs_zero_streak"]["pgs"] == 1


def test_pgs_second_consecutive_night_alerts():
    out = ts.ingest_lines([PGS_ZERO, PGS_ZERO], {"pgs_zero_streak": {"pgs": 1}})
    assert len(out["alerts"]) == 1  # two zero nights in one pass = ONE alert
    a = out["alerts"][0]
    assert a["key"] == "pgs-zero-production"
    assert "2 in a row" in a["detail"]  # fires at threshold-crossing night (3rd line deduped)
    assert out["state"]["pgs_zero_streak"]["pgs"] == 3  # full count lives in state


def test_pgs_sources_streak_independently():
    out = ts.ingest_lines(
        [PGS_ZERO, {"source_id": "pgs2", "ran": True, "ok": 0, "skipped": 0, "error": 0}],
        {"pgs_zero_streak": {"pgs": 1}},
    )
    # pgs reaches 2 → alerts; pgs2 is at 1 → silent
    assert len(out["alerts"]) == 1
    assert "pgs:" in out["alerts"][0]["detail"]
    assert out["state"]["pgs_zero_streak"] == {"pgs": 2, "pgs2": 1}


def test_pgs_garbage_counters_ignored_counted():
    garbage = {"source_id": "x", "ran": "truthy", "ok": 0, "skipped": 0, "error": 0}
    assert ts._pgs_zero(garbage) is None  # non-bool ran / junk counters: not pgs-shaped
    out = ts.ingest_lines([garbage])
    assert out["alerts"] == []
    assert out["state"]["pgs_zero_streak"] == {}  # garbage never touches streak state


# --- signature class 3: standup-stall + correlated-dark ----------------------

def test_single_silent_lane_stall_alert_with_age():
    out = ts.ingest_lines([silent_lane("homelab", 30.0)])
    assert out["alerts"] == [{
        "key": "standup:homelab",
        "detail": "stall source=silent payload_age=30h",
        "ok": False,
    }]


def test_two_silent_lanes_collapse_to_one_correlated_alert():
    out = ts.ingest_lines([silent_lane("homelab", 26.0), silent_lane("gymlog", 44.0)])
    assert len(out["alerts"]) == 1  # correlated-dark law: ONE finding
    a = out["alerts"][0]
    assert a["key"] == "correlated-dark"
    assert "gymlog+homelab" in a["detail"]
    assert "age_h=44h" in a["detail"]
    assert "one root cause" in a["detail"]


def test_live_lane_not_a_stall():
    rec = {
        "standup": "gymlog", "issued_at": "2026-09-28 06:00 PT",
        "payload": {"source": "live", "age_h": 2.0, "sha": "a" * 12, "sections_expected": []},
        "skills_commanded": False,
    }
    out = ts.ingest_lines([{"standup_receipt": rec}])
    assert out["alerts"] == []
    assert out["ignored"] == 0


def test_malformed_standup_receipt_counted_not_used():
    out = ts.ingest_lines([{"standup_receipt": {"standup": "nope"}}])
    assert out["alerts"] == []
    assert out["ignored"] == 1  # garbage is visible, never silent


def test_determinism_and_class_order():
    lanes = [silent_lane("homelab", 26.0), silent_lane("gymlog", 44.0)]
    lines = lanes + [PGS_ZERO, PGS_ZERO, ADVISOR_LINE]
    a = ts.ingest_lines(lines)
    b = ts.ingest_lines(list(reversed(lines)), a["state"])
    keys_a = [x["key"] for x in a["alerts"]]
    # order follows in-file line order (correlated-dark always appended last);
    # the alert SET is order-independent — same faults, any line order
    assert keys_a == ["pgs-zero-production", "stage1-advisor", "correlated-dark"]
    assert a["alerts"][-1]["detail"].startswith("gymlog+homelab")
    assert "one root cause" in a["alerts"][-1]["detail"]
    # reversed input: the same fault-set renders identically (order-independent)
    assert sorted(x["key"] for x in b["alerts"]) == sorted(keys_a)


# --- offset tail + file ingestion (rotation, replay) -------------------------

def test_read_new_lines_only_complete(tmp_path):
    f = tmp_path / "ws.jsonl"
    f.write_text(json.dumps(ADVISOR_LINE) + "\n" + '{"part', encoding="utf-8")
    objs, off, ign = ts.read_new_lines(str(f), 0)
    assert len(objs) == 1 and ign == 0  # partial line held back
    f.write_text(json.dumps(ADVISOR_LINE) + "\n" + '{"part": 1}\n', encoding="utf-8")
    objs2, off2, _ = ts.read_new_lines(str(f), off)
    assert objs2 == [{"part": 1}]  # completed line arrives once, no replay


def test_read_new_lines_rotation_reprocesses(tmp_path):
    f = tmp_path / "ws.jsonl"
    f.write_text("0123456789" * 100, encoding="utf-8")  # 1000 bytes
    _, off, _ = ts.read_new_lines(str(f), 0)  # offsets to EOF
    f.write_text(json.dumps(ADVISOR_LINE) + "\n", encoding="utf-8")  # truncate→rotate
    objs, off2, _ = ts.read_new_lines(str(f), off)
    assert objs == [ADVISOR_LINE] and off2 > 0  # shrunk file -> reprocessed from 0


def test_ingest_state_no_double_fire(tmp_path):
    f = tmp_path / "ws.jsonl"
    f.write_text(json.dumps(PGS_ZERO) + "\n", encoding="utf-8")
    st = {"offsets": {}, "pgs_zero_streak": {}}
    r1 = ts.ingest_text_files([str(f)], st)
    assert len(r1["alerts"]) == 0 and r1["state"]["pgs_zero_streak"]["pgs"] == 1
    # with the persisted state (offsets + streak) intact, rereading the
    # unchanged file consumes nothing new and never advances the streak:
    r3 = ts.ingest_text_files([str(f)], r1["state"])
    assert r3["state"]["pgs_zero_streak"]["pgs"] == 1  # no double fire on replay
    assert r3["skipped"] == [str(f)]  # nothing new consumed -> reported as skipped
    # FULLY lost state (offsets AND streak) = honest restart: the same single
    # line is streak night 1 again, log-only (never a hidden 2-night replay).
    r2 = ts.ingest_text_files([str(f)], {"offsets": {}, "pgs_zero_streak": {}})
    assert r2["alerts"] == []
    assert r2["state"]["pgs_zero_streak"]["pgs"] == 1


def test_ingest_missing_file_skipped_visible(tmp_path):
    st = {"offsets": {}, "pgs_zero_streak": {}}
    r = ts.ingest_text_files([str(tmp_path / "gone.jsonl")], st)
    assert r["skipped"] == [str(tmp_path / "gone.jsonl")]
    assert r["alerts"] == []
