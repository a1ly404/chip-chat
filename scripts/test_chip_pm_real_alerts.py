"""Parity P1.4e: real relay alert shapes and failed routines become PM jobs with no human (fakes only)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chip import bg_jobs, bg_picker, pm_tick, routines

T0 = datetime(2026, 3, 8, 9, 30, tzinfo=timezone.utc)
ON = {"CHIP_PM_TICK": "1", "CHIP_PICKER": "1", "CHIP_JOBS": "1", "CHIP_ROUTINES": "1"}
SCRIPTS = Path(__file__).resolve().parent


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    for k in (
        "CHIP_PM_TICK", "CHIP_PICKER", "CHIP_JOBS", "CHIP_ROUTINES", "CHIP_PICKER_DAILY_MAX", "CHIP_JOBS_DAILY_MAX",
        "CHIP_JOBS_READ_CONCURRENCY", "CHIP_SPEND_CAP_USD", "CHIP_SPEND_THRIFT_PCT", "CHIP_PM_ALERTS_PATH",
        "CHIP_PM_LINEAR", "CHIP_LINEAR_API_KEY", "CHIP_JOBS_MODEL_STEP",
    ):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def relay_row(key: str, detail: str, *, gate: str = "ok", posted: bool = True) -> dict:
    """Shape written by chip_relay.monitoring_listen to chip_state/monitoring_dispatch.jsonl."""
    raw = f"key={key} | detail={detail}"
    return {
        "room": "alerts", "skill_id": "s1", "created_skill": False,
        "alert": {"key": key, "detail": detail, "raw": raw},
        "learning": "", "gate": gate, "posted": posted,
    }


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# ------------------------------------------------------------------ severity inference


@pytest.mark.parametrize(
    "text,sev",
    [
        ("svc-down container exited", "warning"),
        ("web container unhealthy for 5m", "warning"),
        ("nightly job failed with exit 1", "warning"),
        ("api crashloop", "warning"),
        ("probe timed out", "warning"),
        ("full outage on edge", "critical"),
        ("egress leak detected", "critical"),
        ("svc-down resolved", "info"),
        ("web recovered, healthy again", "info"),
        ("weekly digest posted", ""),
        ("", ""),
    ],
)
def test_infer_severity_defaults(text: str, sev: str) -> None:
    assert pm_tick.infer_severity(text) == sev


def test_unhealthy_is_not_read_as_healthy_again() -> None:
    assert pm_tick.infer_severity("db unhealthy") == "warning"


def test_policy_rules_override_and_bad_rules_are_skipped() -> None:
    rules = pm_tick.severity_rules({"alert_severity_rules": [["(", "critical"], ["disk", "bogus"], ["disk", "Critical"]]})
    assert [s for _, s in rules] == ["critical"]
    assert pm_tick.infer_severity("disk 95%", rules) == "critical"
    assert pm_tick.infer_severity("svc down", rules) == ""  # custom list replaces defaults
    assert len(pm_tick.severity_rules({"alert_severity_rules": []})) == len(pm_tick.DEFAULT_SEVERITY_RULES)


def test_policy_file_rules_reach_alert_stream(tmp_path: Path) -> None:
    bg_picker.policy_path().write_text(json.dumps({"alert_severity_rules": [["backlog", "warning"]]}), encoding="utf-8")
    src = tmp_path / "a.jsonl"
    _write(src, [relay_row("queue", "backlog growing"), relay_row("svc-down", "container exited")])
    items, _ = pm_tick.AlertStream(src).read()
    assert [(i["id"], i["severity"]) for i in items] == [("queue", "warning"), ("svc-down", "")]


# ------------------------------------------------------------------ relay row normalization


def test_relay_row_gets_inferred_severity_and_key_id() -> None:
    item = pm_tick.normalize_alert(relay_row("svc-down", "container exited 2m ago"))
    assert item["id"] == "svc-down" and item["severity"] == "warning"
    assert item["severity_source"] == "inferred" and item["detail"] == "container exited 2m ago"


def test_explicit_severity_wins_over_inference() -> None:
    item = pm_tick.normalize_alert({"id": "x", "title": "svc down", "severity": "info"})
    assert item["severity"] == "info" and "severity_source" not in item


def test_relay_ignored_rows_are_dropped() -> None:
    assert pm_tick.normalize_alert(relay_row("svc-down", "exited", gate="ignored", posted=False)) is None
    assert pm_tick.normalize_alert({"id": "x", "title": "down", "ignored": True}) is None
    # cooldown rows are still real alerts; the picker's per-day dedupe handles repeats
    assert pm_tick.normalize_alert(relay_row("svc-down", "exited", gate="cooldown", posted=False)) is not None


def test_keyless_relay_alert_gets_stable_fingerprint() -> None:
    a = pm_tick.normalize_alert({"alert": {"key": "", "detail": "Web  container unhealthy", "raw": "x"}})
    b = pm_tick.normalize_alert({"alert": {"key": "", "detail": "web container unhealthy", "raw": "x"}})
    assert a["id"].startswith("fp-") and a["id"] == b["id"] and a["severity"] == "warning"


# ------------------------------------------------------------------ end to end: alert -> job with no human


def test_real_relay_alert_becomes_job_and_runs_without_human(tmp_path: Path) -> None:
    src = tmp_path / "chip_state" / "monitoring_dispatch.jsonl"
    _write(
        src,
        [
            relay_row("svc-down", "container exited"),
            relay_row("web-health", "container unhealthy"),
            relay_row("svc-down", "resolved"),  # same key, recovery: already picked + info
            relay_row("noise", "flapping probe", gate="ignored", posted=False),
            relay_row("digest", "weekly digest posted"),
        ],
    )
    res = pm_tick.tick(T0, alerts=pm_tick.AlertStream(pm_tick.default_alerts_path({})), env=ON)
    assert res["status"] == "ok" and res["alerts"]["ignored"] == 1
    assert len(res["picked"]) == 2 and {r["status"] for r in res["ran"]} == {"ok"}
    jobs = {j["params"]["pick_key"]: j for j in bg_jobs.list_all()}
    assert set(jobs) == {"alert:svc-down", "alert:web-health"}
    assert all(j["mode"] == "read" and j["status"] == "ok" for j in jobs.values())
    assert 'severity: "warning"' in jobs["alert:svc-down"]["prompt"]
    reasons = {x["key"]: x["reason"] for x in res["skipped"]}
    assert reasons["alert:digest"].startswith("alert severity ?")
    # next tick: nothing new, cursor advanced
    res2 = pm_tick.tick(T0 + timedelta(minutes=15), alerts=pm_tick.AlertStream(src), env=ON)
    assert res2["picked"] == [] and res2["alerts"]["lines"] == 0


# ------------------------------------------------------------------ failed routines -> jobs


def _routine(rid: str, kind: str = "prompt") -> None:
    routines.add(rid, schedule="every:1h", kind=kind, prompt="p", now=T0 - timedelta(hours=2))


def _fail_runner(routine: dict) -> dict:
    return {"status": "failed", "summary": f"{routine['id']} exploded: exit 2", "spend_usd": 0.0}


def test_routine_failures_first_run_starts_at_end_then_reads_new(tmp_path: Path) -> None:
    _routine("backup")
    routines.tick(T0, _fail_runner, env=ON)  # old failure before the adapter existed
    rf = pm_tick.RoutineFailures()
    items, info = rf.read()
    assert items == [] and info["routines"] == 1
    rf.commit()
    routines.tick(T0 + timedelta(hours=2), _fail_runner, env=ON)
    items, info = rf.read()
    assert [i["id"] for i in items] == ["routine-failed:backup"] and items[0]["severity"] == "warning"
    assert "exploded" in items[0]["detail"]
    rf.commit()
    assert rf.read()[0] == []


def test_routine_failures_backfill_skips_ok_rows_and_pm_tick_kind() -> None:
    _routine("backup")
    _routine("pm", kind="pm-tick")
    routines.tick(T0, _fail_runner, env=ON)
    routines.tick(T0 + timedelta(hours=2), routines.mock_runner, env=ON)
    items, info = pm_tick.RoutineFailures(backfill=True).read()
    assert [i["id"] for i in items] == ["routine-failed:backup"]
    assert info["new_rows"] == 4 and info["failed"] == 1


def test_routine_failures_uncommitted_read_replays() -> None:
    _routine("backup")
    rf = pm_tick.RoutineFailures(backfill=True)
    routines.tick(T0, _fail_runner, env=ON)
    assert len(rf.read()[0]) == 1
    assert len(rf.read()[0]) == 1  # not committed -> same rows again


def test_failed_routine_becomes_pm_job_via_tick() -> None:
    _routine("backup")
    rf = pm_tick.RoutineFailures(backfill=True)
    routines.tick(T0, _fail_runner, env=ON)
    res = pm_tick.tick(T0, routine_failures=rf, env=ON)
    assert res["routine_failures"]["failed"] == 1 and len(res["picked"]) == 1
    job = bg_jobs.list_all()[0]
    assert job["params"]["pick_key"] == "alert:routine-failed:backup" and job["status"] == "ok"
    # cursor committed: the same failure is not re-read
    res2 = pm_tick.tick(T0 + timedelta(minutes=5), routine_failures=rf, env=ON)
    assert res2["routine_failures"]["new_rows"] == 0 and res2["picked"] == []


def test_dry_run_does_not_commit_routine_cursor() -> None:
    _routine("backup")
    rf = pm_tick.RoutineFailures(backfill=True)
    routines.tick(T0, _fail_runner, env=ON)
    res = pm_tick.tick(T0, routine_failures=rf, dry_run=True, env={})
    assert len(res["picked"]) == 1 and not rf.cursor_path.exists()


def test_picker_refusal_keeps_routine_cursor() -> None:
    _routine("backup")
    rf = pm_tick.RoutineFailures(backfill=True)
    routines.tick(T0, _fail_runner, env=ON)
    with pytest.raises(bg_picker.PickerDisabled):
        pm_tick.tick(T0, routine_failures=rf, env={"CHIP_PM_TICK": "1"})
    assert not rf.cursor_path.exists()


def test_cli_tick_dry_run_reports_routine_failures(_data_dir: Path) -> None:
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("CHIP_")}
    env.update(CHIP_CHAT_DATA_DIR=str(_data_dir), PYTHONPATH=str(SCRIPTS))
    out = subprocess.run(
        [sys.executable, "-m", "chip", "bgjob", "tick", "--dry-run", "--mock", "--no-alerts"],
        cwd=SCRIPTS, env=env, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    doc = json.loads(out.stdout)
    assert doc["status"] == "dry-run" and "routine_failures" in doc
    out2 = subprocess.run(
        [sys.executable, "-m", "chip", "bgjob", "tick", "--dry-run", "--mock", "--no-alerts", "--no-routine-failures"],
        cwd=SCRIPTS, env=env, capture_output=True, text=True, timeout=60,
    )
    assert out2.returncode == 0 and "routine_failures" not in json.loads(out2.stdout)
