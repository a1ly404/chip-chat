"""In-job refusal of policy decisions (defense in depth).

A job / dev-bot subprocess carries ``CHIP_BGJOB_ID`` / ``CHIP_JOB_ACTOR`` (set by ``job_env``).
Even if code inside a job re-arms every flag plus YOLO, it must not be able to decide a job,
open a confirm request, write a YOLO grant, issue a decision ticket or append an authority row.
The legitimate PM path (outside any job) is unaffected. Keys are generated at runtime; nothing
runs for real (injected runner).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chip import cli, job_policy as jp, policy_exec as px

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
MARKERS = jp.IN_SYSTEM_MARKERS


@pytest.fixture(autouse=True)
def _clean():
    jp.clear_hooks()
    px.clear_notifiers()
    yield
    jp.clear_hooks()
    px.clear_notifiers()


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    op_dir = tmp_path / "operator"
    op_dir.mkdir()
    key = jp.OperatorKey.generate()
    key_file = op_dir / "op.key"
    key_file.write_text(key.seed_hex() + "\n", encoding="utf-8")
    policy = {
        "job_types": {
            "diagnostics": {
                "allow_commands": ["systemctl", "journalctl", "ls"],
                "allow_targets": ["/srv/app", "/srv/app/*"],
                "max_spend_usd": 0.1,
                "max_seconds": 300,
            },
        }
    }
    pol_file = op_dir / "policy.json"
    pol_file.write_text(json.dumps(policy), encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        jp.FLAG: "1",
        px.EXEC_FLAG: "1",
        px.NOTIFY_FLAG: "1",
        jp.YOLO_ENV: "0",
        jp.PUBKEY_ENV: key.public.hex(),
        jp.KEY_FILE_ENV: str(key_file),
        jp.POLICY_FILE_ENV: str(pol_file),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in MARKERS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(jp, "_now", lambda now=None: now or NOW)
    jp.pin_policy(key, env=env, now=NOW)
    return {"env": env, "key": key}


def req(**over):
    base = {
        "job_id": "j1",
        "requested_by": "dev:alpha",
        "job_type": "diagnostics",
        "action": "restart-service",
        "command": ["systemctl", "restart", "app"],
        "target": "/srv/app",
        "context": "repo-x",
        "sudo": True,
        "reason": "service down",
        "max_seconds": 60,
        "max_spend_usd": 0.05,
    }
    base.update(over)
    return base


def armed(setup, **markers):
    """Every flag re-armed, YOLO on, plus the given in-system markers."""
    return {**setup["env"], jp.YOLO_ENV: "1", px.EXEC_FLAG: "1", px.NOTIFY_FLAG: "1", **markers}


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def snapshot() -> dict[str, int]:
    return {
        "audit": _lines(jp.audit_path()),
        "requests": _lines(jp.requests_path()),
        "grants": _lines(jp.grants_path()),
        "authority": _lines(jp.authority_path()),
        "decisions": _lines(px.decisions_path()),
    }


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, env, timeout_s):
        self.calls.append(argv)
        return {"exit": 0, "stdout": "done", "stderr": "", "timed_out": False}


@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize("sudo", [True, False])
def test_in_job_evaluate_refused_with_every_flag_and_yolo_armed(setup, marker, sudo):
    before = snapshot()
    d = jp.evaluate(req(sudo=sudo), actor="pm", pm_reason="fix", env=armed(setup, **{marker: "j0"}), now=NOW)
    assert d.verdict == "deny" and "in-job refusal" in d.reason and marker in d.reason
    assert d.grant_id == "" and d.request_id == "" and d.rule_id == ""
    assert snapshot() == before  # no audit, request, grant, authority or decision row
    assert jp.grants() == []


@pytest.mark.parametrize("marker", MARKERS)
def test_marker_in_process_env_refuses_even_with_a_clean_env_mapping(setup, monkeypatch, marker):
    # code inside a job hands evaluate a hand-built env without the markers: the process env still counts
    monkeypatch.setenv(marker, "j0")
    before = snapshot()
    clean = armed(setup)
    assert not any(k in clean for k in MARKERS)
    d = jp.evaluate(req(), actor="pm", env=clean, now=NOW)
    assert d.verdict == "deny" and "in-job refusal" in d.reason
    a = px.authorize(req(), actor="pm", env=clean, now=NOW)
    assert a.verdict == "deny" and a.ticket is None and "in-job refusal" in a.reason
    assert snapshot() == before and jp.grants() == []


def test_in_job_authorize_run_dev_job_and_gate_job_issue_nothing(setup):
    inside = armed(setup, CHIP_BGJOB_ID="j0", CHIP_JOB_ACTOR="dev:alpha")
    sent = []
    before = snapshot()
    a = px.authorize(req(), actor="pm", env=inside, now=NOW, notifier=lambda m, row: sent.append(m))
    assert a.verdict == "deny" and a.ticket is None and "in-job refusal" in a.reason
    r = FakeRunner()
    out = px.run_dev_job(req(sudo=False), env=inside, now=NOW, runner=r)
    assert out["verdict"] == "deny" and "receipt" not in out and r.calls == []
    g = px.gate_job({"id": "j0", "params": {"policy_request": req()}}, env=inside, now=NOW)
    assert g["verdict"] == "deny" and "ticket" not in g
    assert snapshot() == before and sent == []


def test_in_job_job_env_output_is_refused(setup):
    # the exact env the executor hands a job, with YOLO and policy controls re-added by the job
    inside = {**jp.job_env(setup["env"], job_id="j0", actor="dev:alpha"), **armed(setup)}
    before = snapshot()
    assert jp.evaluate(req(), actor="pm", env=inside, now=NOW).verdict == "deny"
    assert snapshot() == before


def test_in_job_authority_writes_refused_even_holding_a_key(setup):
    # pending request opened legitimately by the PM outside any job
    d = jp.evaluate(req(), actor="pm", env=setup["env"], now=NOW)
    assert d.verdict == "ask" and d.request_id
    inside = armed(setup, CHIP_BGJOB_ID="j0")
    before = snapshot()
    with pytest.raises(jp.PolicyError, match="inside a job"):
        jp.confirm(d.request_id, setup["key"], env=inside, now=NOW)
    with pytest.raises(jp.PolicyError, match="inside a job"):
        jp.pin_policy(setup["key"], env=inside, now=NOW)
    with pytest.raises(jp.PolicyError, match="inside a job"):
        jp.OperatorKey.load(inside)
    assert snapshot() == before
    assert not jp.was_confirmed(d.request_id, setup["env"])


def test_cli_policy_check_inside_a_job_is_refused_and_writes_nothing(setup, monkeypatch, capsys):
    monkeypatch.setenv(jp.YOLO_ENV, "1")
    monkeypatch.setenv("CHIP_JOB_ACTOR", "dev:alpha")
    before = snapshot()
    assert cli.main(["policy", "check", "--request", json.dumps(req())]) == 2
    assert snapshot() == before and jp.grants() == []


def test_legit_pm_path_unaffected(setup):
    env = setup["env"]
    assert jp.in_job_marker(env) is None
    # non-sudo: PM approves, ticket issued, executor runs it in a marked child env
    a = px.authorize(req(sudo=False), actor="pm", env=env, now=NOW)
    assert a.verdict == "allow" and a.ticket is not None
    r = FakeRunner()
    rec = px.execute(a.ticket, req(sudo=False), env=env, now=NOW, runner=r)
    assert rec["status"] == "ok" and len(r.calls) == 1
    # sudo with YOLO off asks the operator; with YOLO on PM grants and logs it
    assert jp.evaluate(req(job_id="j2"), actor="pm", env=env, now=NOW).verdict == "ask"
    yolo = {**env, jp.YOLO_ENV: "1"}
    d = jp.evaluate(req(job_id="j3"), actor="pm", pm_reason="fix", env=yolo, now=NOW)
    assert d.verdict == "allow" and d.grant_id and [g["id"] for g in jp.grants()] == [d.grant_id]
    # operator confirm from the operator shell still works
    pend = jp.evaluate(req(job_id="j4"), actor="pm", env=env, now=NOW)
    out = jp.confirm(pend.request_id, jp.OperatorKey.load(env), env=env, now=NOW)
    assert out["rule"]["id"] and jp.was_confirmed(pend.request_id, env)
