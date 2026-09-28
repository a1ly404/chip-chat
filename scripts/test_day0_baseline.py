"""Day-0 baseline tests — the wall item (cron run #1 measures class shrinkage).

Baseline artifact: data/baselines/done_gate_baseline_2026-09-27.json — frozen
2026-09-27 done-gate-sweep findings (receipted in docs/DONE_GATE_QA.md chain,
PRs #64/#66). Tests lock: artifact shape/provenance, --baseline wiring in
monitor_watchdog (compose + fail-closed), and the deterministic compare.

Fixture law: synthetic ids (RAZ-94xx) in every comparison fixture; the
committed artifact is checked as DATA (real ids by design, tamper-evident).
"""

import json
import sys
from pathlib import Path

from chip.monitor_watchdog import baseline_report, class_counts, load_baseline

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "data" / "baselines" / "done_gate_baseline_2026-09-27.json"


def test_baseline_artifact_shape_and_provenance():
    raw = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert raw["schema"] == "done-gate-baseline-v1"
    assert raw["collected"] == "2026-09-27"
    assert raw["collected_by"] == "chip done-gate-sweep (Linear MCP read-only export)"
    fs = raw["findings_state"]
    assert isinstance(fs, dict) and len(fs) == raw["count_findings"]
    # class counts self-consistent with the frozen map
    counts = class_counts(fs)
    assert counts == raw["counts"]
    # day-0 frozen facts (never regenerated silently): the 7 reopen-required ids
    reopen = sorted(i for i, v in fs.items() if v["severity"] == "reopen-required")
    assert reopen == ["RAZ-109", "RAZ-14", "RAZ-182", "RAZ-199", "RAZ-255", "RAZ-45", "RAZ-82"]
    for ident, v in fs.items():
        assert set(v) == {"severity", "checked", "stamps"}
        assert v["checked"] >= 0 and v["stamps"] >= 0


def test_load_baseline_accepts_artifact():
    collected, state = load_baseline(BASELINE)
    assert collected == "2026-09-27"
    assert len(state) == 32


def test_load_baseline_fails_closed(tmp_path):
    import pytest

    missing = tmp_path / "nope.json"
    with pytest.raises((FileNotFoundError, OSError)):
        load_baseline(missing)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_baseline(bad)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"collected": "x", "findings_state": {"RAZ-9401": {"severity": "vague-done"}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_baseline(wrong)  # missing checked/stamps integers
    notobj = tmp_path / "notobj.json"
    notobj.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_baseline(notobj)


def _mini_state(ids, severity="vague-done"):
    return {i: {"severity": severity, "checked": 3, "stamps": 0} for i in ids}


def test_baseline_report_shrinkage():
    day0 = {**_mini_state(["RAZ-9401", "RAZ-9402", "RAZ-9403"]),
            **_mini_state(["RAZ-9407"], "reopen-required")}
    now = _mini_state(["RAZ-9401", "RAZ-9402"])  # 9403 AND 9407 now clean → resolved
    out = baseline_report(now, day0, "2026-09-27")
    assert "BASELINE COMPARE (day-0 2026-09-27)" in out
    assert "vague-done: 3 → 2 (Δ -1)" in out
    assert "reopen-required: 1 → 0 (Δ -1)" in out
    assert "resolved-since-day0 (2): RAZ-9403, RAZ-9407" in out


def test_baseline_report_growth_and_new():
    day0 = _mini_state(["RAZ-9401"])
    now = {**_mini_state(["RAZ-9401", "RAZ-9402"]), **_mini_state(["RAZ-9409"], "reopen-required")}
    out = baseline_report(now, day0, "2026-09-27")
    assert "vague-done: 1 → 2 (Δ +1)" in out
    assert "reopen-required: 0 → 1 (Δ +1)" in out
    assert "new-since-day0 (2): RAZ-9402, RAZ-9409" in out


def test_baseline_report_changed_and_unchanged():
    day0 = {**_mini_state(["RAZ-9401"]),
            "RAZ-9402": {"severity": "vague-done", "checked": 1, "stamps": 0}}
    now = {**_mini_state(["RAZ-9401"]),
           "RAZ-9402": {"severity": "vague-done", "checked": 4, "stamps": 2}}
    out = baseline_report(now, day0, "2026-09-27")
    assert "changed-since-day0 (1): RAZ-9402" in out
    assert "(no per-ticket movement)" not in out
    assert "(unchanged: 1: RAZ-9401)" in out


def test_baseline_report_unchanged_persist_line_caps_ids():
    ids = [f"RAZ-94{i:02d}" for i in range(12)]
    day0, now = _mini_state(ids), _mini_state(ids)
    out = baseline_report(now, day0, "2026-09-27")
    assert "(unchanged: 12" in out
    assert "RAZ-9400" not in out.split("(unchanged")[1]  # ids omitted above cap
    assert "(no per-ticket movement)" not in out  # count line present instead


def test_baseline_report_all_resolved():
    day0 = _mini_state(["RAZ-9401"])
    out = baseline_report({}, day0, "2026-09-27")
    assert "vague-done: 1 → 0 (Δ -1)" in out
    assert "resolved-since-day0 (1): RAZ-9401" in out


def test_determinism_baseline_output():
    day0 = {**_mini_state(["RAZ-9401", "RAZ-9402"]), **_mini_state(["RAZ-9403"], "reopen-required")}
    now = _mini_state(["RAZ-9401"])
    assert baseline_report(now, day0, "d") == baseline_report(now, day0, "d")


# --- main() wiring -----------------------------------------------------------

def test_main_wires_real_day0_baseline(tmp_path, capsys):
    # cron run #1 rehearsal against the committed day-0 artifact. Severity
    # classes match sweep_one law: Done+zero-boxes= vague-done,
    # Done+unchecked-boxes= reopen-required.
    export = [
        {"id": "RAZ-9401", "status": "Done", "statusType": "completed",
         "description": "## Done\nclean, no boxes\n"},                       # vague-done
        {"id": "RAZ-9499", "status": "Done", "statusType": "completed",
         "description": "## Done\nalso clean\n"},                            # vague-done
        {"id": "RAZ-199", "status": "Done", "statusType": "completed",
         "description": "## Acceptance\n- [x] a\n- [x] b\n"},                # reopen-required (changed vs day-0 4 boxes)
    ]
    f = tmp_path / "export.json"
    f.write_text(json.dumps(export), encoding="utf-8")
    from chip import monitor_watchdog as mw

    rc = mw.main(["--issues-file", str(f), "--baseline",
                  str(BASELINE)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "BASELINE COMPARE (day-0 2026-09-27)" in out
    assert "vague-done: 25 → 2 (Δ -23)" in out
    assert "reopen-required: 7 → 1 (Δ -6)" in out
    assert "new-since-day0 (2): RAZ-9401, RAZ-9499" in out
    assert "changed-since-day0 (1): RAZ-199" in out
    assert "resolved-since-day0 (31" in out  # 32 day-0 findings − RAZ-199 persisting


def test_main_baseline_missing_fails_closed(tmp_path, capsys):
    from chip import monitor_watchdog as mw

    f = tmp_path / "export.json"
    f.write_text("[]", encoding="utf-8")
    rc = mw.main(["--issues-file", str(f), "--baseline", str(tmp_path / "nope.json")])
    assert rc == 1
    assert "baseline file not found" in capsys.readouterr().err


def test_main_baseline_garbage_fails_closed(tmp_path, capsys):
    from chip import monitor_watchdog as mw

    f = tmp_path / "export.json"
    f.write_text("[]", encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    rc = mw.main(["--issues-file", str(f), "--baseline", str(bad)])
    assert rc == 1
    assert "baseline" in capsys.readouterr().err.lower()
