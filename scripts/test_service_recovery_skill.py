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
    # offline law: default qwen/PM model calls must NEVER reach the network
    # (the 2026-10-05 storm leaked exactly this way); fake the chat transport
    monkeypatch.setattr(
        service_recovery_skill, "_openrouter_chat",
        lambda model, messages, environ, max_tokens: "1. read incident 2. restart 3. verify 4. stop",
    )
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
        if self.ok:
            return (True, f"executor reports restart ok for {container} (verify uptime)")
        return (False, "executor script failed rc=1")


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
    """Suppression wall holds (wireguard). W-015 law drift canary: tautulli was
    re-allowlisted in service_recovery.json — it must be RESTARTABLE again, and
    this test fails loudly if the law and the tests drift apart."""
    executor = _FakeExecutor()
    out = service_recovery_skill.maybe_run(
        _alert(detail="container=wireguard error: 500 Server Error"),
        now=_now(), report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go,
    )
    assert out["status"] == "suppressed"
    out2 = service_recovery_skill.maybe_run(
        _alert(detail="container=tautulli error: 500 Server Error", alert_id="a2"),
        now=_now(), report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go,
    )
    assert out2["status"] == "recovery-attempted"
    assert [c[2] for c in executor.calls] == ["tautulli"]


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
        qwen_runner=lambda *a: "stub run sheet",
    )
    out2 = service_recovery_skill.maybe_run(
        _alert(detail="container=caddy error: restart timed out"), now=_now() + timedelta(minutes=5),
        report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go,
        qwen_runner=lambda *a: "stub run sheet",
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


def test_qwen_call_fault_fails_closed_w019(data_dir):
    """W-019: a qwen run-sheet fault NEVER proceeds to the executor. The
    incident stays open (strike, not success), the receipt says the restart
    was NOT sent, and a retry after the backoff window can try again."""
    executor = _FakeExecutor()

    def _qwen_boom(law, incident, learnings, environ):
        raise RuntimeError("qwen down")

    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(), executor_runner=executor,
        pm_decider=_pm_go, qwen_runner=_qwen_boom,
    )
    assert out["status"] == "qwen-fault-no-action"
    assert len(executor.calls) == 0
    state = service_recovery_skill.load_state()
    fp = out["fingerprint"]
    assert fp in state["failed_fps"]  # strike, not a consumed incident
    assert fp not in state["attempted_fps"]
    # immediate repeat is backoff-paced, not a fresh attempt
    out2 = service_recovery_skill.maybe_run(
        _alert(alert_id="a2"), now=_now() + timedelta(seconds=60),
        report=_FakeReport(), executor_runner=executor,
        pm_decider=_pm_go, qwen_runner=_qwen_boom,
    )
    assert out2["status"] == "backoff-skipped"
    # after the backoff window the open incident retries (still no executor)
    out3 = service_recovery_skill.maybe_run(
        _alert(alert_id="a3"), now=_now() + timedelta(minutes=11),
        report=_FakeReport(), executor_runner=executor,
        pm_decider=_pm_go, qwen_runner=_qwen_boom,
    )
    assert out3["status"] == "qwen-fault-no-action"
    assert len(executor.calls) == 0


def test_qwen_fallback_serves_when_primary_faults(data_dir, monkeypatch):
    """W-013: guardrail-blocked primary (qwen) falls back to the configured stand-in."""
    calls = []

    def _chat(model, messages, environ, max_tokens):
        calls.append(model)
        if model == "qwen/qwen3-30b-a3b":
            raise RuntimeError("guardrail 404")
        return "1. read 2. restart 3. verify 4. stop"

    monkeypatch.setattr(service_recovery_skill, "_openrouter_chat", _chat)
    law = {"executor_model": "qwen/qwen3-30b-a3b", "executor_model_fallback": "deepseek/deepseek-v4.1-flash"}
    sheet = service_recovery_skill.default_qwen_run_playbook(law, {"container": "caddy", "failure_class": "daemon-error", "error_excerpt": "x"}, [], os.environ)
    assert calls == ["qwen/qwen3-30b-a3b", "deepseek/deepseek-v4.1-flash"]
    assert "restart" in sheet
    assert "qwen-call-fault" not in sheet


def test_pm_json_extraction_tolerates_fences(data_dir, monkeypatch):
    """The PM reply may arrive fenced/prose-prefixed — the object still parses."""
    monkeypatch.setattr(
        service_recovery_skill, "_openrouter_chat",
        lambda model, messages, environ, max_tokens: 'Sure!\n```json\n{"playbook": "service-recovery", "go": true, "reason": "ok"}\n```',
    )
    pm = service_recovery_skill.default_pm_decide({"pm_model": "m"}, {"container": "caddy"}, os.environ)
    assert pm["go"] is True and pm["playbook"] == "service-recovery"


def test_redaction_strips_secret_shaped_strings():
    text = "error: 500 Server Error token=abc123 bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 in log"
    red = service_recovery_skill._redact(text)
    assert "abc123" not in red
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in red
    assert "<redacted>" in red


def test_hard_cap_24h_binds_per_container(data_dir):
    """W-019: the 24h cap counts ALL attempts (successes and strikes) PER
    CONTAINER — key churn cannot evade it, and other containers are
    unaffected by one container's exhausted budget."""
    executor = _FakeExecutor(ok=False)  # every attempt fails -> strikes
    now = _now()
    verdicts = []
    for i in range(4):
        out = service_recovery_skill.maybe_run(
            _alert(detail="container=caddy error: 500 Server Error", alert_id=f"inc{i}"),
            now=now + timedelta(minutes=11 * i),  # past the strike backoff
            report=_FakeReport(),
            executor_runner=executor,
            pm_decider=_pm_go,
        )
        verdicts.append(out["status"])
    assert verdicts == [
        "recovery-failed", "recovery-failed", "recovery-failed", "cap-reached",
    ]
    assert len(executor.calls) == 3
    # per container, not global: bazarr's budget is untouched by caddy's cap
    out = service_recovery_skill.maybe_run(
        _alert(detail="container=bazarr error: 500 Server Error", alert_id="other"),
        now=now + timedelta(minutes=45),
        report=_FakeReport(),
        executor_runner=_FakeExecutor(),
        pm_decider=_pm_go,
    )
    assert out["status"] == "recovery-attempted"


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


def test_executor_failure_stays_open_with_paced_retry(data_dir):
    """W-019: a failed executor attempt does NOT consume the incident — the
    receipt is truthful (restart NOT performed), a strike is recorded, and a
    retry happens only after the backoff window."""
    executor = _FakeExecutor(ok=False)
    report = _FakeReport()
    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=report, executor_runner=executor, pm_decider=_pm_go
    )
    assert out["status"] == "recovery-failed"
    assert "NO executor restart performed" in report.lines[0]
    out2 = service_recovery_skill.maybe_run(
        _alert(alert_id="a2"), now=_now() + timedelta(seconds=30),
        report=report, executor_runner=executor, pm_decider=_pm_go
    )
    assert out2["status"] == "backoff-skipped"
    out3 = service_recovery_skill.maybe_run(
        _alert(alert_id="a3"), now=_now() + timedelta(minutes=11),
        report=report, executor_runner=executor, pm_decider=_pm_go
    )
    assert out3["status"] == "recovery-failed"
    assert len(executor.calls) == 2  # paced retry happened


def test_success_dedupes_and_receipt_is_truthful(data_dir):
    """A verified executor success consumes the incident; the receipt says the
    executor REPORTED success (never claims the relay verified it)."""
    executor = _FakeExecutor(ok=True)
    report = _FakeReport()
    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=report, executor_runner=executor, pm_decider=_pm_go
    )
    assert out["status"] == "recovery-attempted"
    assert "executor reports restart ok" in report.lines[0]
    out2 = service_recovery_skill.maybe_run(
        _alert(alert_id="a2"), now=_now() + timedelta(seconds=30),
        report=report, executor_runner=executor, pm_decider=_pm_go
    )
    assert out2["status"] == "incident-repeat-skipped"
    assert len(executor.calls) == 1


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


def test_key_churn_dedupes_to_one_attempt(data_dir):
    """W-019: incident identity is key-INDEPENDENT. The same container+class
    re-keyed every cycle (the 2026-10-05 storm shape) dedupes to ONE attempt
    and ONE message; the alert key never mints a new incident."""
    executor = _FakeExecutor()
    report = _FakeReport()
    keys = ["auto-restart-failed", "AUTO-RESTART-FAILED", " auto-restart-failed", "auto-restart-failed\n"]
    for i, key in enumerate(keys):
        out = service_recovery_skill.maybe_run(
            {"alert_id": f"churn-{i}", "key": key,
             "detail": f"container=caddy error: 500 Server Error (req id {i})"},
            now=_now() + timedelta(seconds=i),
            report=report, executor_runner=executor, pm_decider=_pm_go,
        )
        if i == 0:
            assert out["status"] == "recovery-attempted"
        else:
            assert out["status"] == "incident-repeat-skipped"
    assert len(executor.calls) == 1
    assert len(report.lines) == 1
    # a feed-borne fingerprint stub is corroboration only — never identity
    out = service_recovery_skill.maybe_run(
        {"alert_id": "stub", "key": "auto-restart-failed",
         "detail": "container=caddy error: 500 Server Error",
         "fingerprint": "totally-different-stub"},
        now=_now() + timedelta(seconds=9),
        report=report, executor_runner=executor, pm_decider=_pm_go,
    )
    assert out["status"] == "incident-repeat-skipped"


def test_key_churn_across_distinct_classes_stays_separate(data_dir):
    """Different failure classes of the same container remain distinct
    incidents (key-independence must not collapse genuine distinctions)."""
    executor = _FakeExecutor()
    out1 = service_recovery_skill.maybe_run(
        _alert(detail="container=caddy error: 500 Server Error"), now=_now(),
        report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go,
    )
    out2 = service_recovery_skill.maybe_run(
        _alert(detail="container=caddy error: restart timed out"), now=_now() + timedelta(minutes=5),
        report=_FakeReport(), executor_runner=executor, pm_decider=_pm_go,
    )
    assert out1["fingerprint"] != out2["fingerprint"]
    assert len(executor.calls) == 2


def test_target_health_gate_blocks_stale_alerts(data_dir):
    """W-019 verify-before-act: a container the relay can PROVE is running
    means the alert is stale — ONE notice, ZERO executor calls, and no
    attempt consumed; repeats of the same stale incident are silent."""
    executor = _FakeExecutor()
    report = _FakeReport()
    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=report, executor_runner=executor,
        pm_decider=_pm_go, container_running=lambda c: True,
    )
    assert out["status"] == "target-healthy"
    assert executor.calls == []
    assert "reports RUNNING" in report.lines[0]
    state = service_recovery_skill.load_state()
    assert out["fingerprint"] not in state["attempted_fps"]  # nothing consumed
    out2 = service_recovery_skill.maybe_run(
        _alert(alert_id="a2"), now=_now() + timedelta(seconds=30),
        report=report, executor_runner=executor,
        pm_decider=_pm_go, container_running=lambda c: True,
    )
    assert out2["status"] == "target-healthy"
    assert len(report.lines) == 1  # repeat notice suppressed
    assert len(executor.calls) == 0
    # cannot verify (None) -> proceed on the alert's evidence, as before
    out3 = service_recovery_skill.maybe_run(
        _alert(alert_id="a3"), now=_now() + timedelta(seconds=60),
        report=report, executor_runner=executor,
        pm_decider=_pm_go, container_running=lambda c: None,
    )
    assert out3["status"] == "recovery-attempted"
    assert len(executor.calls) == 1


def test_live_report_requires_law_flag(data_dir, monkeypatch):
    """W-019 (the storm): the DEFAULT report path posts to the live channel
    ONLY when the law says live_report AND no mock/pytest context is active.
    Doubtful = silent."""
    posted = []
    monkeypatch.setattr(
        "chip.monitor_watchdog.post_to_monitoring",
        lambda text, *, environ=None: (posted.append(text), (True, "posted"))[1],
    )
    law_path = config.data_dir() / "law_live_report.json"
    env = {"HOMELAB_HMAC_SECRET": "s"}
    # 1. law without the flag: log-only
    law_path.write_text(json.dumps({"enabled": True, "live_report": False}), encoding="utf-8")
    monkeypatch.setattr(service_recovery_skill, "_law_file", lambda: law_path)
    assert service_recovery_skill._report_to_monitoring(env, "line") is False
    assert posted == []
    # 2. law flag on: posts
    law_path.write_text(json.dumps({"enabled": True, "live_report": True}), encoding="utf-8")
    assert service_recovery_skill._report_to_monitoring(env, "line") is True
    assert len(posted) == 1
    # 3. mock still wins over the law flag
    assert service_recovery_skill._report_to_monitoring({**env, "CHIP_CHAT_MOCK": "1"}, "line") is False
    assert len(posted) == 1
    # 4. pytest context always wins (the storm leaked from a harness run)
    assert service_recovery_skill._report_to_monitoring(
        {**env, "PYTEST_CURRENT_TEST": "test_x"}, "line"
    ) is False
    assert len(posted) == 1
    # 5. env override
    assert service_recovery_skill._report_to_monitoring(
        {**env, "CHIP_SERVICE_RECOVERY_LIVE_REPORT": "0"}, "line"
    ) is False
    assert service_recovery_skill._report_to_monitoring(
        {**env, "CHIP_SERVICE_RECOVERY_LIVE_REPORT": "1"}, "line"
    ) is True


def test_qwen_unconfigured_fails_closed(data_dir, monkeypatch):
    """No executor model configured = an executor-path gap: fail-closed."""
    law_path = config.data_dir() / "law_no_model.json"
    law_path.write_text(
        json.dumps({"enabled": True, "executor_model": "", "service_allowlist": ["caddy"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(service_recovery_skill, "_law_file", lambda: law_path)
    executor = _FakeExecutor()
    out = service_recovery_skill.maybe_run(
        _alert(), now=_now(), report=_FakeReport(), executor_runner=executor,
        pm_decider=_pm_go, qwen_runner=service_recovery_skill.default_qwen_run_playbook,
    )
    assert out["status"] == "qwen-fault-no-action"
    assert executor.calls == []


def test_sock_inspect_parse_plain_200_w019():
    """W-019 gate-6 fix: plain HTTP/1.0 200 reply parses State.Running."""
    from chip_relay.service_recovery_skill import _running_from_response

    body = json.dumps({"State": {"Running": True}}).encode()
    assert _running_from_response(b"HTTP/1.0 200 OK\r\nContent-Type: json\r\n", body) is True


def test_sock_inspect_parse_chunked_200_w019():
    """W-019 gate-6 fix: a chunked HTTP/1.1 reply (docker daemon default)
    must de-chunk before JSON parse — raw parse made the health gate return
    None (cannot verify) for every container, so stale alerts never fired."""
    from chip_relay.service_recovery_skill import _running_from_response

    payload = json.dumps({"State": {"Running": True}, "Name": "/bazarr"}).encode()
    body = (f"{len(payload):x}\r\n".encode() + payload + b"\r\n0\r\n\r\n")
    head = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
    assert _running_from_response(head, body) is True


def test_sock_inspect_parse_non_200_is_none_w019():
    """W-019 gate-6 fix: non-200 (daemon 500 during compose recreation —
    the exact W-019 false-positive trigger) must read as cannot-verify."""
    from chip_relay.service_recovery_skill import _running_from_response

    assert _running_from_response(b"HTTP/1.1 500 Internal Server Error\r\n", b"{}") is None
