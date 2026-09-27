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
