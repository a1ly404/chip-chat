"""Heartbeat dead-man sensor tests (ocgo churn ruling; report-only law)."""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

mw = pytest.importorskip("chip")


def test_heartbeat_missing_lastrun_is_a_finding(tmp_path):
    from chip import monitor_watchdog as mwdev  # noqa: F401
    import chip.monitor_watchdog as m

    result = m.ops_ab_heartbeat_stale(tmp_path / "nowhere.json")
    assert result is not None
    assert result["id"] == "ops-ab-heartbeat-missing"


def test_heartbeat_fresh_is_ok(tmp_path):
    import chip.monitor_watchdog as m

    p = tmp_path / "lastrun.json"
    payload = {"ts": datetime.now(timezone.utc).isoformat(), "budget": 0.02}
    p.write_text(json.dumps(payload))
    result = m.ops_ab_heartbeat_stale(p)
    assert result is None


def test_heartbeat_stale_is_a_finding(tmp_path):
    import chip.monitor_watchdog as m

    p = tmp_path / "lastrun.json"
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    payload = {"ts": old.isoformat(), "budget": 0.02}
    p.write_text(json.dumps(payload))
    result = m.ops_ab_heartbeat_stale(p)
    assert result is not None
    assert result["id"] == "ops-ab-heartbeat-stale"


def test_heartbeat_garbage_json_is_a_finding(tmp_path):
    import chip.monitor_watchdog as m

    p = tmp_path / "lastrun.json"
    p.write_text("not json")
    result = m.ops_ab_heartbeat_stale(p)
    assert result is not None
    assert result["id"] == "ops-ab-heartbeat-garbage"
