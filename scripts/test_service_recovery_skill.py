"""W-010/W-013 service-recovery skill tests (law gates; no live executor/webhook/LLM).

- scope: only auto-restart-failed alerts with a container= field reach the skill
- suppression + allowlist walls: known-broken, native, and unknown containers refuse
- incident identity: container+failure_class — repeat ingests dedupe (W-013)
- PM go/no-go: a structured PM-classifier call gates the executor lane
- qwen run-sheet: playbook + redacted context only; recorded in the receipt
- fails closed: missing HMAC secret / disabled law / unparsable expiry / PM no-go
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

PM_GO = {"go": True, "playbook": "service-recovery", "reason": "test-go"}


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "appdata"
    d.mkdir()
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(d))
    monkeypatch.setenv("CHIP_SERVICE_RECOVERY_EXPIRES_ISO", NOT_EXPIRED)
    monkeypatch.setenv("CHIP_SERVICE_RECOVERY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("HOMELAB_HMAC_SECRET", "test-secret")
    return d


def _alert(detail="container=caddy error: 500 Server Error", alert_id="a1"):
    return {"alert_id": alert_id, "key": "auto-restart-failed", "detail": detail}


def _now():
    return datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)


def _pm_go(law, incident, environ):
    return dict(PM_GO)


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


def test_dispatch_row_and_learning_appended(data_dir):
    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(), executor_runner=_FakeExecutor(), pm_decider=_pm_go
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


def test_suppressed_container_never_restarted(data_dir):
    executor = _FakeExecutor()
    for detail in [
        "container=tautulli error: 500 Server Error",
        "container=wireguard error: 500 Server Error",
    ]:
        out = service_recovery_skill.maybe_run(
            _alert(detail=detail), now=_now(), report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go
        )
        assert out["status"] == "suppressed"
    assert executor.calls == []


def test_allowlist_wall_only_restarts_configured_containers(data_dir):
    executor = _FakeExecutor()
    out = service_recovery_skill.maybe_run(
        _alert(detail="container=lumo-tamer error: 500 Server Error"),
        now=_now(), report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go,
    )
    assert out["status"] == "not-allowlisted"
    assert executor.calls == []


def test_native_service_never_reaches_executor(data_dir):
    """W-013: sonarr/radarr/plex are NATIVE Windows services — no executor restart."""
    executor = _FakeExecutor()
    for detail in [
        "container=sonarr error: 404 Client Error: Not Found (No such container: sonarr)",
        "container=radarr error: No such container: radarr",
        "container=plex error: not found",
    ]:
        out = service_recovery_skill.maybe_run(
            _alert(detail=detail), now=_now(), report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go
        )
        assert out["status"] == "not-a-container"
        assert out["failure_class"] == "missing-container"
    assert executor.calls == []


def test_repeat_ingests_dedupe_across_varying_error_text(data_dir):
    """W-013 dedup fix: the monitor re-pushes the same failing container with
    docker error strings that embed varying IDs — one attempt, one message."""
    executor = _FakeExecutor()
    report = _FakeReport()
    for i in range(5):
        out = service_recovery_skill.maybe_run(
            _alert(detail=f"container=caddy error: 500 Server Error (req id varies {i})", alert_id=f"a{i}"),
            now=_now() + timedelta(seconds=i),
            report=report,
            executor_runner=executor,
            pm_decider=_pm_go,
        )
        if i == 0:
            assert out["status"] == "recovery-attempted"
        else:
            assert out["status"] == "incident-repeat-skipped"
    assert len(executor.calls) == 1
    assert len(report.lines) == 1


def test_new_failure_class_is_a_new_incident(data_dir):
    executor = _FakeExecutor()
    out1 = service_recovery_skill.maybe_run(
        _alert(detail="container=caddy error: 500 Server Error"), now=_now(),
        report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go,
    )
    out2 = service_recovery_skill.maybe_run(
        _alert(detail="container=caddy error: restart timed out"), now=_now() + timedelta(minutes=5),
        report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go,
    )
    assert out1["status"] == "recovery-attempted"
    assert out2["status"] == "recovery-attempted"
    assert out1["fingerprint"] != out2["fingerprint"]
    assert len(executor.calls) == 2


def test_pm_refusal_blocks_the_executor(data_dir):
    executor = _FakeExecutor()

    def _pm_no(law, incident, environ):
        return {"go": False, "playbook": "service-recovery", "reason": "wait for human"}

    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(), executor_runner=executor, pm_decider=_pm_no
    )
    assert out["status"] == "pm-refused"
    assert out["pm_reason"] == "wait for human"
    assert executor.calls == []


def test_pm_call_fault_fails_closed(data_dir):
    executor = _FakeExecutor()

    def _pm_boom(law, incident, environ):
        raise RuntimeError("openrouter down")

    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(), executor_runner=executor, pm_decider=_pm_boom
    )
    assert out["status"] == "pm-refused"
    assert executor.calls == []


def test_qwen_run_sheet_recorded_in_receipt_and_learning(data_dir):
    executor = _FakeExecutor()

    def _qwen(law, incident, learnings, environ):
        assert incident["container"] == "caddy"
        return "1. read incident 2. restart caddy 3. verify channel 4. stop"

    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(), executor_runner=executor,
        pm_decider=_pm_go, qwen_runner=_qwen,
    )
    assert out["status"] == "recovery-attempted"
    assert "restart caddy" in out["run_sheet"]
    journal = service_recovery_skill.learnings_path().read_text(encoding="utf-8")
    assert "qwen run-sheet recorded" in journal


def test_qwen_call_fault_does_not_block_pm_authorized_restart(data_dir):
    executor = _FakeExecutor()

    def _qwen_boom(law, incident, learnings, environ):
        raise RuntimeError("qwen down")

    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(), executor_runner=executor,
        pm_decider=_pm_go, qwen_runner=_qwen_boom,
    )
    assert out["status"] == "recovery-attempted"
    assert "qwen-call-fault" in out["run_sheet"]
    assert len(executor.calls) == 1


def test_redaction_strips_secret_shaped_strings():
    text = "error: 500 Server Error token=abc123 bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 in log"
    red = service_recovery_skill._redact(text)
    assert "abc123" not in red
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in red
    assert "<redacted>" in red


def test_hard_cap_24h(data_dir):
    executor = _FakeExecutor()
    now = _now()
    verdicts = []
    containers = ["caddy", "bazarr", "ombi", "calibre-web"]  # distinct incidents
    for i, name in enumerate(containers):
        out = service_recovery_skill.maybe_run(
            _alert(detail=f"container={name} error: 500 Server Error", alert_id=f"inc{i}"),
            now=now + timedelta(minutes=i),
            report=_FakeReport(),
            executor_runner=executor,
            pm_decider=_pm_go,
        )
        verdicts.append(out["status"])
    assert verdicts == ["recovery-attempted", "recovery-attempted", "recovery-attempted", "cap-reached"]
    assert len(executor.calls) == 3


def test_missing_hmac_secret_fails_closed(data_dir, monkeypatch):
    monkeypatch.delenv("HOMELAB_HMAC_SECRET", raising=False)
    executor = _FakeExecutor()
    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go
    )
    assert out["status"] == "no-credentials"
    assert executor.calls == []


def test_disabled_config_fails_closed(data_dir, monkeypatch):
    monkeypatch.setenv("CHIP_SERVICE_RECOVERY_EXPIRES_ISO", NOT_EXPIRED)
    law_path = config.data_dir() / "law_override_sr.json"
    law_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
    monkeypatch.setattr(service_recovery_skill, "_law_file", lambda: law_path)
    executor = _FakeExecutor()
    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go
    )
    assert out["status"] == "disabled"
    assert executor.calls == []


def test_expired_disarms_once_and_never_acts(data_dir, monkeypatch):
    monkeypatch.setenv("CHIP_SERVICE_RECOVERY_EXPIRES_ISO", EXPIRED)
    report = _FakeReport()
    executor = _FakeExecutor()
    out1 = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=report, executor_runner=executor, pm_decider=_pm_go
    )
    assert out1["status"] == "disarmed"
    assert len(executor.calls) == 0
    assert len(report.lines) == 1 and "DISARMED" in report.lines[0]
    out2 = service_recovery_skill.maybe_run(
        _alert(alert_id="a2"), now=_now(), report=report, executor_runner=executor, pm_decider=_pm_go
    )
    assert out2["status"] == "disarmed"
    assert len(report.lines) == 1


def test_unparsable_expiry_fails_closed(data_dir, monkeypatch):
    monkeypatch.setenv("CHIP_SERVICE_RECOVERY_EXPIRES_ISO", "not-a-timestamp")
    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(), executor_runner=_FakeExecutor(), pm_decider=_pm_go
    )
    assert out["status"] == "disarmed"


def test_executor_failure_is_recorded_not_retried(data_dir):
    executor = _FakeExecutor(ok=False)
    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go
    )
    assert out["status"] == "recovery-failed"
    out2 = service_recovery_skill.maybe_run(
        _alert(alert_id="a2"), now=_now(), report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go
    )
    assert out2["status"] == "incident-repeat-skipped"


def test_learnings_tail_loaded_and_echoed(data_dir):
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
    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=report, executor_runner=_FakeExecutor(), pm_decider=_pm_go
    )
    assert out["status"] == "recovery-attempted"
    assert "never loop" in report.lines[0]
    rows = alert_ingest.logged_path().read_text(encoding="utf-8").splitlines()
    assert "never loop" in rows[-1]


def test_ingest_route_table_claims_auto_restart_failed():
    skill, verdict = alert_ingest.classify(_alert())
    assert verdict == "matched: razzorstack"
    assert "service-recovery" in skill


def test_ingest_route_table_claims_discord_bot():
    skill, verdict = alert_ingest.classify(
        {"alert_id": "d1", "key": "discord-bot", "detail": "status=error loops_ok=False"}
    )
    assert verdict == "matched: razzorstack"
    assert "discord-bot" in skill
    rec = alert_ingest.dispatch_record({"alert_id": "d1", "key": "discord-bot", "detail": "x"})
    assert rec["matched"] is True


def test_ingest_handle_alert_runs_skill_non_shadow(data_dir, monkeypatch):
    executor = _FakeExecutor()
    monkeypatch.setattr(
        service_recovery_skill,
        "_default_executor_restart",
        lambda base, script, c, s, t: executor(base, script, c, s, t),
    )
    monkeypatch.setattr(
        service_recovery_skill, "default_pm_decide", lambda law, inc, env: dict(PM_GO)
    )
    out = alert_ingest.handle_alert(_alert())
    assert out["ok"] is True
    assert out["service_recovery"]["status"] == "recovery-attempted"
    rows = alert_ingest.logged_path().read_text(encoding="utf-8").splitlines()
    assert any('"service-recovery"' in r for r in rows)
    # discord-bot alerts are now routed watch-only: matched (no UNMATCHED
    # banner), skill untouched, ledger row still shadow per dispatch_record law
    out2 = alert_ingest.handle_alert(
        {"alert_id": "a3", "key": "discord-bot", "detail": "loops_ok=False"}
    )
    assert out2["service_recovery"] is None
    assert out2["record"]["matched"] is True
    assert out2["banner_posted"] is False


def test_shadow_ledger_and_banner_dedupe(data_dir, monkeypatch):
    """W-013: repeat skillless alerts write ONE ledger row + ONE banner per window."""
    monkeypatch.setenv("CHIP_WEBHOOK_MONITORING", "https://discord.invalid/api/webhooks/w/mon")
    posted_banners = []

    def _fake_post(text, *, environ=None):
        posted_banners.append(text)
        return True, "posted"

    monkeypatch.setattr(
        "chip.monitor_watchdog.post_to_monitoring", _fake_post
    )
    for i in range(4):
        out = alert_ingest.handle_alert(
            {"alert_id": f"u{i}", "key": "unknown-source-x", "detail": f"noise {i}"}
        )
        if i == 0:
            assert out["shadow_deduped"] is False
            assert out["banner_posted"] is True
        else:
            assert out["shadow_deduped"] is True
            assert out["banner_posted"] is False
    rows = alert_ingest.logged_path().read_text(encoding="utf-8").splitlines()
    assert sum('"unknown-source-x"' in r for r in rows) == 1
    assert len(posted_banners) == 1


def test_shadow_dedupe_window_expiry(data_dir, monkeypatch):
    monkeypatch.setattr(
        "chip.monitor_watchdog.post_to_monitoring", lambda text, environ=None: (True, "posted")
    )
    assert alert_ingest._shadow_recent("k", now_ts=1000.0) is False
    alert_ingest._shadow_mark("k", now_ts=1000.0)
    assert alert_ingest._shadow_recent("k", now_ts=1000.0 + 899) is True
    assert alert_ingest._shadow_recent("k", now_ts=1000.0 + alert_ingest.SHADOW_DEDUPE_WINDOW_S + 1) is False


def test_report_failure_does_not_block_the_row(data_dir):
    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(ok=False), executor_runner=_FakeExecutor(), pm_decider=_pm_go
    )
    assert out["status"] == "recovery-attempted"
    assert out["report_posted"] is False
    rows = alert_ingest.logged_path().read_text(encoding="utf-8").splitlines()
    assert '"tell_posted": false' in rows[-1]


def test_mock_report_guard(data_dir, monkeypatch):
    """CHIP_CHAT_MOCK=1: the default report path never reaches the live channel."""
    monkeypatch.setenv("CHIP_CHAT_MOCK", "1")
    assert service_recovery_skill._report_to_monitoring(os.environ, "line") is False
