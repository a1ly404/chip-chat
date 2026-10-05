"""W-006 plex-external fix skill tests (law gates; no live docker/webhook).

- scope: only plex-external* + 530/identity/health-down details reach the skill
- expiry: checked BEFORE any action; disarm notice exactly once; then inert
- caps: one attempt per incident fingerprint; hard cap N per rolling 24h
- allowlist wall: only the configured containers are ever restarted
- receipts: every decision appends a dispatch row + one learning reflection
- learnings: tail-loaded each run; the loaded lesson shows up in receipts
- isolated tmp data dir via CHIP_CHAT_DATA_DIR (repo test convention)
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from chip import config
from chip_relay import alert_ingest, plex_fix_skill

NOT_EXPIRED = "2099-01-01T00:00:00Z"
EXPIRED = "2020-01-01T00:00:00Z"


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "appdata"
    d.mkdir()
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(d))
    monkeypatch.setenv("CHIP_PLEX_FIX_EXPIRES_ISO", NOT_EXPIRED)
    monkeypatch.delenv("CHIP_PLEX_FIX_MAX_ATTEMPTS", raising=False)
    return d


def _alert(key="plex-external", detail="Plex /identity -> 530 (19 cycles)", alert_id="a1"):
    return {"alert_id": alert_id, "key": key, "detail": detail}


def _now():
    return datetime(2026, 10, 4, 21, 45, tzinfo=timezone.utc)


class _FakeWebhook:
    def __init__(self, status=204):
        self.status = status
        self.lines = []

    def __call__(self, url, username, content):
        self.lines.append(content)
        return type("R", (), {"status": self.status})()


class _FakeDocker:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def __call__(self, targets, timeout_s):
        self.calls.append(list(targets))
        return (self.ok, f"restarted {','.join(targets)}" if self.ok else "restart boom")


class _FakeProbe:
    def __init__(self, result="reachable:200"):
        self.result = result
        self.urls = []

    def __call__(self, url, timeout_s):
        self.urls.append(url)
        return self.result


def _env(monkeypatch, webhook_url="https://discord.invalid/api/webhooks/w/dev"):
    monkeypatch.setenv("CHIP_WEBHOOK_DEV", webhook_url)
    return os.environ


def test_non_target_alerts_are_ignored(data_dir):
    for key, detail in [
        ("discord-bot", "status=error loops_ok=False"),
        ("monitoring-api", "status=unhealthy"),
        ("plex-external", "unrelated detail"),  # right key, no 530/identity fingerprint
        ("plex", "530"),  # skip-key class must stay with the noise floor
    ]:
        assert plex_fix_skill.maybe_run(_alert(key, detail), now=_now()) is None


def test_dispatch_row_and_learning_appended(data_dir, monkeypatch):
    _env(monkeypatch)
    hook = _FakeWebhook()
    out = plex_fix_skill.maybe_run(
        _alert(), now=_now(), webhook=hook, docker_runner=_FakeDocker(), prober=_FakeProbe()
    )
    assert out["status"] == "fix-attempted"
    rows = alert_ingest.logged_path().read_text(encoding="utf-8").splitlines()
    rec = json.loads(rows[-1])
    assert rec["skill_selected"] == "plex-external-fix"
    assert rec["shadow"] is False
    assert rec["verdict"] == "fix-attempted"
    journal = plex_fix_skill.learnings_path().read_text(encoding="utf-8").splitlines()
    entry = json.loads(journal[-1])
    assert set(entry) >= {"timestamp", "situation", "diagnosis", "action", "outcome", "lesson"}


def test_allowlist_wall_only_restarts_configured_containers(data_dir, monkeypatch):
    _env(monkeypatch)
    docker = _FakeDocker()
    plex_fix_skill.maybe_run(_alert(), now=_now(), webhook=_FakeWebhook(), docker_runner=docker, prober=_FakeProbe())
    law = plex_fix_skill.law_config()
    assert docker.calls == [list(law["service_allowlist"])]
    assert "caddy" in docker.calls[0] and "cloudflared" in docker.calls[0]


def test_one_attempt_per_incident_fingerprint(data_dir, monkeypatch):
    _env(monkeypatch)
    docker = _FakeDocker()
    for i in range(3):
        plex_fix_skill.maybe_run(
            _alert(alert_id=f"a{i}"), now=_now(), webhook=_FakeWebhook(), docker_runner=docker, prober=_FakeProbe()
        )
    assert len(docker.calls) == 1
    out = plex_fix_skill.maybe_run(_alert(alert_id="a9"), now=_now(), webhook=_FakeWebhook(), docker_runner=docker, prober=_FakeProbe())
    assert out["status"] == "incident-repeat-skipped"


def test_hard_cap_24h(data_dir, monkeypatch):
    monkeypatch.setenv("CHIP_PLEX_FIX_MAX_ATTEMPTS", "3")
    _env(monkeypatch)
    docker = _FakeDocker()
    now = _now()
    verdicts = []
    for i in range(4):
        # distinct fingerprints = distinct incidents
        out = plex_fix_skill.maybe_run(
            _alert(alert_id=f"inc{i}", detail=f"530 (incident {i})"),
            now=now + timedelta(minutes=i),
            webhook=_FakeWebhook(),
            docker_runner=docker,
            prober=_FakeProbe(),
        )
        verdicts.append(out["status"])
    assert verdicts == ["fix-attempted", "fix-attempted", "fix-attempted", "cap-reached"]
    assert len(docker.calls) == 3


def test_expired_disarms_once_and_never_acts(data_dir, monkeypatch):
    monkeypatch.setenv("CHIP_PLEX_FIX_EXPIRES_ISO", EXPIRED)
    _env(monkeypatch)
    hook = _FakeWebhook()
    docker = _FakeDocker()
    out1 = plex_fix_skill.maybe_run(_alert(), now=_now(), webhook=hook, docker_runner=docker, prober=_FakeProbe())
    assert out1["status"] == "disarmed"
    assert len(docker.calls) == 0
    assert len(hook.lines) == 1 and "DISARMED" in hook.lines[0]
    # second call: still disarmed, no second notice
    out2 = plex_fix_skill.maybe_run(_alert(alert_id="a2"), now=_now(), webhook=hook, docker_runner=docker, prober=_FakeProbe())
    assert out2["status"] == "disarmed"
    assert len(hook.lines) == 1
    # learnings receipt exists for the disarm
    assert "disarm" in plex_fix_skill.learnings_path().read_text(encoding="utf-8")


def test_unparsable_expiry_fails_closed(data_dir, monkeypatch):
    monkeypatch.setenv("CHIP_PLEX_FIX_EXPIRES_ISO", "not-a-timestamp")
    _env(monkeypatch)
    out = plex_fix_skill.maybe_run(_alert(), now=_now(), webhook=_FakeWebhook(), docker_runner=_FakeDocker(), prober=_FakeProbe())
    assert out["status"] == "disarmed"


def test_restart_failure_is_recorded_not_retried(data_dir, monkeypatch):
    _env(monkeypatch)
    docker = _FakeDocker(ok=False)
    out = plex_fix_skill.maybe_run(_alert(), now=_now(), webhook=_FakeWebhook(), docker_runner=docker, prober=_FakeProbe())
    assert out["status"] == "fix-failed"
    assert "external_probe" in out
    out2 = plex_fix_skill.maybe_run(_alert(alert_id="a2"), now=_now(), webhook=_FakeWebhook(), docker_runner=docker, prober=_FakeProbe())
    assert out2["status"] == "incident-repeat-skipped"


def test_disabled_config_fails_closed(data_dir, monkeypatch):
    _env(monkeypatch)
    law_path = config.data_dir() / "law_override.json"
    law_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
    monkeypatch.setattr(plex_fix_skill, "_law_file", lambda: law_path)
    docker = _FakeDocker()
    out = plex_fix_skill.maybe_run(_alert(), now=_now(), webhook=_FakeWebhook(), docker_runner=docker, prober=_FakeProbe())
    assert out["status"] == "disabled"
    assert docker.calls == []


def test_learnings_tail_loaded_and_echoed(data_dir, monkeypatch):
    _env(monkeypatch)
    plex_fix_skill.append_learning(
        {
            "timestamp": "2026-10-04T21:00:00Z",
            "situation": "prior run",
            "diagnosis": "d",
            "action": "a",
            "outcome": "o",
            "lesson": "caddy reload window matters",
        }
    )
    hook = _FakeWebhook()
    out = plex_fix_skill.maybe_run(_alert(), now=_now(), webhook=hook, docker_runner=_FakeDocker(), prober=_FakeProbe())
    assert out["status"] == "fix-attempted"
    assert "caddy reload window matters" in hook.lines[0]
    rows = alert_ingest.logged_path().read_text(encoding="utf-8").splitlines()
    assert "caddy reload window matters" in rows[-1]


def test_ingest_route_table_claims_plex_external():
    skill, verdict = alert_ingest.classify(_alert())
    assert verdict == "matched: chip"
    assert "plex-external-fix" in skill


def test_ingest_handle_alert_runs_skill_non_shadow(data_dir, monkeypatch):
    _env(monkeypatch)
    docker = _FakeDocker()
    monkeypatch.setattr(
        plex_fix_skill, "_default_docker", lambda t, s: docker(list(t), s)
    )
    monkeypatch.setattr(plex_fix_skill, "_default_probe", lambda u, s: "reachable:200")
    monkeypatch.setenv("CHIP_WEBHOOK_DEV", "")  # no live webhook in tests
    out = alert_ingest.handle_alert(_alert())
    assert out["ok"] is True
    assert out["plex_fix"]["status"] == "fix-attempted"
    rows = alert_ingest.logged_path().read_text(encoding="utf-8").splitlines()
    assert any('"plex-external-fix"' in r for r in rows)
    # non-plex alerts still shadow-only, skill untouched
    out2 = alert_ingest.handle_alert(_alert(key="discord-bot", detail="loops_ok=False", alert_id="a3"))
    assert out2["plex_fix"] is None
    assert out2["record"]["shadow"] is True


def test_tell_failure_does_not_block_the_row(data_dir, monkeypatch):
    _env(monkeypatch, webhook_url="https://discord.invalid/api/webhooks/w/dev")
    hook = _FakeWebhook(status=500)
    out = plex_fix_skill.maybe_run(_alert(), now=_now(), webhook=hook, docker_runner=_FakeDocker(), prober=_FakeProbe())
    assert out["status"] == "fix-attempted"
    assert out["tell_posted"] is False
    rows = alert_ingest.logged_path().read_text(encoding="utf-8").splitlines()
    assert '"tell_posted": false' in rows[-1]