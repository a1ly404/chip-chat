"""monitor_watchdog tests (P2: dry-run default, posting stubbed, fixture law)."""

import json
import sys
from pathlib import Path

from chip.monitor_watchdog import build_table, post_to_monitoring

FIX = {
    "id": "RAZ-9401", "status": "Done", "statusType": "completed",
    "description": "## Acceptance\n- [x] a\n- [x] b\n",
}


def test_build_table_shapes():
    table = build_table([{"id": "RAZ-9401", "status": "Done", "checked": 2,
                          "stamps": 0, "severity": "reopen-required", "reason": "x"}])
    assert "RAZ-9401" in table and "reopen-required" in table
    assert "(clean" not in table
    assert "(clean — no findings)" in build_table([])


def test_post_skips_without_env(monkeypatch):
    monkeypatch.delenv("CHIP_WEBHOOK_MONITORING", raising=False)
    ok, note = post_to_monitoring("table", environ={})
    assert ok is False and "skipped" in note


def test_post_uses_chip_relay_UA(monkeypatch):
    calls = []

    class R:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    def fake_urlopen(req, timeout):
        calls.append((req.headers, req.data))
        return R()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ok, note = post_to_monitoring("table", environ={"CHIP_WEBHOOK_MONITORING": "https://x.invalid"})
    assert ok is True and note == "posted"
    headers, body = calls[0]
    assert headers["User-agent"] == "chip-relay"
    assert "chip-relay" in body.decode()


def test_main_dry_run_default(tmp_path, capsys):
    from chip import monitor_watchdog as mw

    f = tmp_path / "issues.json"
    f.write_text(json.dumps([FIX]), encoding="utf-8")
    rc = mw.main(["--issues-file", str(f)])
    assert rc == 2  # findings exit like the sweep
    out = capsys.readouterr().out
    assert "RAZ-9401" in out
    assert "skipped" not in out  # dry-run never mentions posting


def test_main_dry_run_never_posts_even_with_post_flag_without_env(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CHIP_WEBHOOK_MONITORING", raising=False)
    from chip import monitor_watchdog as mw

    f = tmp_path / "issues.json"
    f.write_text(json.dumps([FIX]), encoding="utf-8")
    rc = mw.main(["--issues-file", str(f), "--post"])
    assert rc == 2
    assert "post skipped" in capsys.readouterr().out


def test_delta_report_first_run_full_then_p2_deltas(tmp_path, capsys):
    import subprocess

    issues_v1 = [
        {"id": "RAZ-9401", "status": "Done", "statusType": "completed",
         "description": "## Acceptance\n- [x] a\n- [x] b\n"},
        {"id": "RAZ-9402", "status": "In Progress", "statusType": "started",
         "description": "## Acceptance\n- [x] c\n"},
    ]
    issues_v2 = [
        issues_v1[0],  # persists
        # 9402 resolved (stamps bound, sweeping away flipping-risk)
        {"id": "RAZ-9402", "status": "In Progress", "statusType": "started",
         "description": "## Acceptance\n- [x] c\nverified-as-of: 2026-09-27 07:00 — ok\n"},
        # new high-severity finding
        {"id": "RAZ-9403", "status": "Done", "statusType": "completed",
         "description": "## Acceptance\n- [x] d\n"},
    ]
    state = tmp_path / "wdog_state.json"

    def run(issues):
        f = tmp_path / "export.json"
        f.write_text(json.dumps(issues), encoding="utf-8")
        return json.loads(json.dumps(issues))

    from chip import monitor_watchdog as mw

    findings1 = mw.sweep(run(issues_v1))
    report1, rc1 = mw.delta_report(run(issues_v1), state)
    assert rc1 == 2  # findings exist but 1st run table = full (no prev state)
    assert "SITREP — Done-discipline findings" in report1

    # second run 30 "minutes" later: one resolved, one NEW, one unchanged
    report2, rc2 = mw.delta_report(run(issues_v2), state)
    assert "DELTA SITREP" in report2
    assert "RESOLVED RAZ-9402" in report2
    assert "NEW [reopen-required] RAZ-9403" in report2
    assert "(unchanged: 1 — RAZ-9401)" in report2
    assert rc2 == 2

    # steady-state third run: no findings delta → one-glance "(unchanged — N)"
    report3, rc3 = mw.delta_report(run(issues_v2), state)
    assert "(unchanged — 2 finding(s) persist)" in report3


def test_determinism_same_export_identical_output(tmp_path, capsys):
    from chip import monitor_watchdog as mw

    issues = [
        {"id": "RAZ-9401", "status": "Done", "statusType": "completed",
         "description": "## A\n- [x] a\n"},
        {"id": "RAZ-9402", "status": "Done", "statusType": "completed",
         "description": "## B\n- [x] b\n- [x] c\n"},
        {"id": "RAZ-9403", "status": "In Progress", "statusType": "started",
         "description": "## C\n- [x] d\n"},
    ]
    f = tmp_path / "issues.json"
    f.write_text(json.dumps(issues), encoding="utf-8")

    cap1 = capsys.readouterr()
    rc1 = mw.main(["--issues-file", str(f)])
    out1 = capsys.readouterr().out

    rc2 = mw.main(["--issues-file", str(f)])
    out2 = capsys.readouterr().out
    assert rc1 == rc2 == 2
    assert out1 == out2  # determinism bug would break here
    assert "RAZ-9401" in out1 and "RAZ-9402" in out1
