"""standup_emit tests — STANDUP_EMISSION_SPEC task 3 (cron-ready emitter).

Law under test: mirror of monitor_watchdog (dry-run default, env-gated
post), typed stall rendering for absent/garbage receipts (never confident
narration), correlated-dark rc=3 page-tier, credit counter footer,
<=6 lines per window. Offline only; posting stubbed.
"""

import sys
from pathlib import Path

from chip import standup_emit as se

REPO = Path(__file__).resolve().parent.parent


def live_receipt(standup="homelab", age_h=2.0):
    return {
        "standup": standup, "issued_at": "2026-09-28 06:00 PT",
        "payload": {"source": "live", "age_h": age_h, "sha": "a" * 12, "sections_expected": []},
        "skills_commanded": False,
        "credit": {"phaseC_gate": True, "reason": "live payload within staleness"},
    }


# --- render_window ------------------------------------------------------------

def test_render_window_all_live_zero_stalls():
    text, credit = se.render_window([live_receipt("homelab", 1.0), live_receipt("gymlog", 3.0)])
    assert "source=live" in text and "STALL" not in text
    assert "phaseC credit: 2/2" in text
    assert text.count("\n") + 1 <= 6


def test_render_window_silent_renders_stall_not_confident():
    from chip import standup as su

    rec = su.make_silent_receipt("homelab", 30.0, issued_at="2026-09-28 06:00 PT")
    text, credit = se.render_window([rec])
    assert "STALL" in text and "source=silent" in text
    assert "phaseC credit: 0/1" in text
    assert su.confident_health_hits(text) == []


def test_render_window_correlated_dark_one_finding():
    from chip import standup as su

    lanes = [su.make_silent_receipt("gymlog", None, issued_at="2026-09-28 06:00 PT"),
             su.make_silent_receipt("homelab", 25.0, issued_at="2026-09-28 06:05 PT")]
    text, credit = se.render_window(lanes)
    assert text.count("correlated-dark:") == 1  # ONE finding per window
    assert "phaseC credit: 0/2" in text
    assert "STALL" in text  # per-lane stalls still visible in the report


def test_render_window_garbage_receipt_visible():
    text, _ = se.render_window([{}])
    assert "RECEIPT GARBAGE" in text
    assert "zero credit" in text


def test_render_window_lanes_sorted():
    text, _ = se.render_window([live_receipt("gymlog", 1.0), live_receipt("homelab", 1.0)])
    assert text.index("gymlog standup") < text.index("homelab standup")  # alphabetical: g < h


# --- load_receipts (fail-closed by construction) ------------------------------

def test_load_receipts_missing_lane_is_silent(tmp_path):
    lanes = ["homelab"]
    recs = se.load_receipts(str(tmp_path), lanes)
    assert recs[0]["payload"]["source"] == "silent"
    assert recs[0]["credit"]["phaseC_gate"] is False


def test_load_receipts_garbage_file_is_silent(tmp_path):
    (tmp_path / "homelab.json").write_text("{not json")
    recs = se.load_receipts(str(tmp_path), ["homelab"])
    assert recs[0]["payload"]["source"] == "silent"


def test_load_receipts_live_receipt_read(tmp_path):
    import json

    (tmp_path / "homelab.json").write_text(json.dumps(live_receipt()))
    recs = se.load_receipts(str(tmp_path), ["homelab"])
    assert recs[0]["payload"]["source"] == "live"


def test_load_receipts_no_dir_all_silent(tmp_path):
    recs = se.load_receipts(None, ["homelab", "gymlog"])
    assert [r["payload"]["source"] for r in recs] == ["silent", "silent"]


# --- main() CLI law -----------------------------------------------------------

def test_main_dry_run_never_posts(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CHIP_WEBHOOK_MONITORING", raising=False)
    rc = se.main(["--lanes", "homelab,gymlog", "--receipts-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 3  # two silent lanes = correlated-dark tier
    assert "correlated-dark:" in out
    assert "phaseC credit: 0/2" in out
    assert "post skipped" not in out  # dry-run never mentions posting


def test_main_single_silent_rc2(tmp_path, capsys):
    rc = se.main(["--lanes", "homelab", "--receipts-dir", str(tmp_path)])
    assert rc == 2
    assert "STALL" in capsys.readouterr().out


def test_main_all_live_rc0(tmp_path, capsys):
    import json

    d = tmp_path / "r"
    d.mkdir()
    (d / "homelab.json").write_text(json.dumps(live_receipt("homelab", 2.0)))
    (d / "gymlog.json").write_text(json.dumps(live_receipt("gymlog", 5.0)))
    rc = se.main(["--lanes", "homelab,gymlog", "--receipts-dir", str(d)])
    assert rc == 0
    assert "phaseC credit: 2/2" in capsys.readouterr().out


def test_main_json_receipt_stderr(tmp_path, capsys):
    rc = se.main(["--lanes", "homelab", "--receipts-dir", str(tmp_path), "--json"])
    err = capsys.readouterr().err
    assert '"credited": 0' in err
    assert rc == 2


def test_main_deterministic(tmp_path, capsys):
    r1 = se.main(["--lanes", "homelab,gymlog", "--receipts-dir", str(tmp_path)])
    o1 = capsys.readouterr().out
    r2 = se.main(["--lanes", "homelab,gymlog", "--receipts-dir", str(tmp_path)])
    o2 = capsys.readouterr().out
    assert r1 == r2 and o1 == o2


def test_main_post_gate_false_without_missing_receipts_env(tmp_path, capsys, monkeypatch):
    import json

    d = tmp_path / "r"
    d.mkdir()
    (d / "homelab.json").write_text(json.dumps(live_receipt("homelab", 2.0)))
    rc = se.main(["--lanes", "homelab", "--receipts-dir", str(d)])
    assert rc == 0
