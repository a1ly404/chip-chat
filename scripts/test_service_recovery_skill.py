"""W-010 service-recovery skill tests (law gates; no live executor/webhook).

- scope: only auto-restart-failed alerts with a container= field reach the skill
- suppression + allowlist walls: known-broken and unknown containers refuse
- caps: one attempt per incident fingerprint; hard cap N per rolling 24h
- fails closed: missing HMAC secret / disabled law / unparsable expiry
- receipts: every decision appends a dispatch row + one learning reflection
- isolated tmp data dir via CHIP_CHAT_DATA_DIR (repo test convention)
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from chip import config
from chip_relay import alert_ingest, service_recovery_skill

NOT_EXPIRED = "2099-01-01T00:00:00Z"
EXPIRED = "2020-01-01T00:00:00Z"


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "appdata"
    d.mkdir()
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(d))
    monkeypatch.setenv("CHIP_SERVICE_RECOVERY_EXPIRES_ISO", NOT_EXPIRED)
    monkeypatch.setenv("CHIP_SERVICE_RECOVERY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("HOMELAB_HMAC_SECRET", "test-secret")
    return d


def _alert(detail="container=sonarr error: 500 Server Error", alert_id="a1"):
    return {"alert_id": alert_id, "key": "auto-restart-failed", "detail": detail}


def _now():
    return datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)


class _FakeReport:
    def __init__(self, ok=True):
        self.ok = ok
        self.lines = []

    def __call__(self, environ, line):
        self.lines.append(line)
        return self.ok


class _FakeExecutor:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def __call__(self, base_url, script, container, secret, timeout_s):
        self.calls.append((base_url, script, container))
        return (self.ok, f"executor restarted {container}" if self.ok else "executor script failed rc=1")


def _env(monkeypatch, **overrides):
    monkeypatch.delenv("CHIP_SERVICE_RECOVERY_EXPIRES_ISO", raising=False)
    for k, v in overrides.items():
        monkeypatch.setenv(k, v)
    return os.environ


def test_non_target_alerts_are_ignored(data_dir):
    for key, detail in [
        ("plex-external", "Plex /identity -> 530"),
        ("auto-restart-failed", "no container field here"),
        ("discord-bot", "status=error loops_ok=False"),
    ]:
        assert service_recovery_skill.maybe_run(_alert(detail=detail), now=_now()) is None


def test_dispatch_row_and_learning_appended(data_dir, monkeypatch):
    _env(monkeypatch)
    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(), executor_runner=_FakeExecutor()
    )
    assert out["status"] == "recovery-attempted"
    assert out["failure_class"] == "daemon-error"
    rows = alert_ingest.logged_path().read_text(encoding="utf-8").splitlines()
    rec = json.loads(rows[-1])
    assert rec["skill_selected"] == "service-recovery"
    assert rec["shadow"] is False
    assert rec["verdict"] == "recovery-attempted"
    journal = service_recovery_skill.learnings_path().read_text(encoding="utf-8").splitlines()
    entry = json.loads(journal[-1])
    assert set(entry) >= {"timestamp", "situation", "diagnosis", "action", "outcome", "lesson"}


def test_suppressed_container_never_restarted(data_dir, monkeypatch):
    _env(monkeypatch)
    executor = _FakeExecutor()
    for detail in [
        "container=tautulli error: 500 Server Error",
        "container=wireguard error: 500 Server Error",
    ]:
        out = service_recovery_skill.maybe_run(_alert(detail=detail), now=_now(), report=_FakeReport(), executor_runner=executor)
        assert out["status"] == "suppressed"
    assert executor.calls == []


def test_allowlist_wall_only_restarts_configured_containers(data_dir, monkeypatch):
    _env(monkeypatch)
    executor = _FakeExecutor()
    out = service_recovery_skill.maybe_run(
        _alert(detail="container=lumo-tamer error: 500 Server Error"), now=_now(), report=_FakeReport(), executor_runner=executor
    )
    assert out["status"] == "not-allowlisted"
    assert executor.calls == []


def test_one_attempt_per_incident_fingerprint(data_dir, monkeypatch):
    _env(monkeypatch)
    executor = _FakeExecutor()
    for i in range(3):
        service_recovery_skill.maybe_run(
            _alert(alert_id=f"a{i}"), now=_now(), report=_FakeReport(), executor_runner=executor
        )
    assert len(executor.calls) == 1
    out = service_recovery_skill.maybe_run(_alert(alert_id="a9"), now=_now(), report=_FakeReport(), executor_runner=executor)
    assert out["status"] == "incident-repeat-skipped"


def test_hard_cap_24h(data_dir, monkeypatch):
    _env(monkeypatch)
    executor = _FakeExecutor()
    now = _now()
    verdicts = []
    for i in range(4):
        # distinct fingerprints = distinct incidents (different container+error)
        out = service_recovery_skill.maybe_run(
            _alert(detail=f"container=sonarr error: boom {i}", alert_id=f"inc{i}"),
            now=now + timedelta(minutes=i),
            report=_FakeReport(),
            executor_runner=executor,
        )
        verdicts.append(out["status"])
    assert verdicts == ["recovery-attempted", "recovery-attempted", "recovery-attempted", "cap-reached"]
    assert len(executor.calls) == 3


def test_missing_hmac_secret_fails_closed(data_dir, monkeypatch):
    _env(monkeypatch)
    monkeypatch.delenv("HOMELAB_HMAC_SECRET", raising=False)
    executor = _FakeExecutor()
    out = service_recovery_skill.maybe_run(_alert(), now=_now(), report=_FakeReport(), executor_runner=executor)
    assert out["status"] == "no-credentials"
    assert executor.calls == []


def test_disabled_config_fails_closed(data_dir, monkeypatch):
    monkeypatch.setenv("CHIP_SERVICE_RECOVERY_EXPIRES_ISO", NOT_EXPIRED)
    law_path = config.data_dir() / "law_override_sr.json"
    law_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
    monkeypatch.setattr(service_recovery_skill, "_law_file", lambda: law_path)
    executor = _FakeExecutor()
    out = service_recovery_skill.maybe_run(_alert(), now=_now(), report=_FakeReport(), executor_runner=executor)
    assert out["status"] == "disabled"
    assert executor.calls == []


def test_expired_disarms_once_and_never_acts(data_dir, monkeypatch):
    _env(monkeypatch, CHIP_SERVICE_RECOVERY_EXPIRES_ISO=EXPIRED)
    report = _FakeReport()
    executor = _FakeExecutor()
    out1 = service_recovery_skill.maybe_run(_alert(), now=_now(), report=report, executor_runner=executor)
    assert out1["status"] == "disarmed"
    assert len(executor.calls) == 0
    assert len(report.lines) == 1 and "DISARMED" in report.lines[0]
    out2 = service_recovery_skill.maybe_run(_alert(alert_id="a2"), now=_now(), report=report, executor_runner=executor)
    assert out2["status"] == "disarmed"
    assert len(report.lines) == 1


def test_unparsable_expiry_fails_closed(data_dir, monkeypatch):
    _env(monkeypatch, CHIP_SERVICE_RECOVERY_EXPIRES_ISO="not-a-timestamp")
    out = service_recovery_skill.maybe_run(_alert(), now=_now(), report=_FakeReport(), executor_runner=_FakeExecutor())
    assert out["status"] == "disarmed"


def test_executor_failure_is_recorded_not_retried(data_dir, monkeypatch):
    _env(monkeypatch)
    executor = _FakeExecutor(ok=False)
    out = service_recovery_skill.maybe_run(_alert(), now=_now(), report=_FakeReport(), executor_runner=executor)
    assert out["status"] == "recovery-failed"
    out2 = service_recovery_skill.maybe_run(_alert(alert_id="a2"), now=_now(), report=_FakeReport(), executor_runner=executor)
    assert out2["status"] == "incident-repeat-skipped"


def test_learnings_tail_loaded_and_echoed(data_dir, monkeypatch):
    _env(monkeypatch)
    service_recovery_skill.append_learning(
        {
            "timestamp": "2026-10-05T00:00:00Z",
            "situation": "prior run",
            "diagnosis": "d",
            "action": "a",
            "outcome": "o",
            "lesson": "executor restarts are one-shot; never loop",
        }
    )
    report = _FakeReport()
    out = service_recovery_skill.maybe_run(_alert(), now=_now(), report=report, executor_runner=_FakeExecutor())
    assert out["status"] == "recovery-attempted"
    assert "never loop" in report.lines[0]
    rows = alert_ingest.logged_path().read_text(encoding="utf-8").splitlines()
    assert "never loop" in rows[-1]


def test_ingest_route_table_claims_auto_restart_failed():
    skill, verdict = alert_ingest.classify(_alert())
    assert verdict == "matched: razzorstack"
    assert "service-recovery" in skill


def test_ingest_handle_alert_runs_skill_non_shadow(data_dir, monkeypatch):
    _env(monkeypatch)
    executor = _FakeExecutor()
    monkeypatch.setattr(
        service_recovery_skill,
        "_default_executor_restart",
        lambda base, script, c, s, t: executor(base, script, c, s, t),
    )
    out = alert_ingest.handle_alert(_alert())
    assert out["ok"] is True
    assert out["service_recovery"]["status"] == "recovery-attempted"
    rows = alert_ingest.logged_path().read_text(encoding="utf-8").splitlines()
    assert any('"service-recovery"' in r for r in rows)
    # non-matching alerts keep the shadow record, skill untouched
    out2 = alert_ingest.handle_alert(
        {"alert_id": "a3", "key": "discord-bot", "detail": "loops_ok=False"}
    )
    assert out2["service_recovery"] is None
    assert out2["record"]["shadow"] is True


def test_report_failure_does_not_block_the_row(data_dir, monkeypatch):
    _env(monkeypatch)
    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(ok=False), executor_runner=_FakeExecutor()
    )
    assert out["status"] == "recovery-attempted"
    assert out["report_posted"] is False
    rows = alert_ingest.logged_path().read_text(encoding="utf-8").splitlines()
    assert '"tell_posted": false' in rows[-1]
