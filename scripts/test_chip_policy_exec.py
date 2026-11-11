"""Policy executor (parity D slice 2): allow decisions run bound, capped, scrubbed; attack matrix.

Attack matrix: argv/target/requester swap after the decision, sudo without a grant (flipped flag,
forged/edited ticket, revoked rule, expired YOLO grant), self-approval (dev bot as actor, hand-made
ticket, executor inside a job), running with the policy or executor flag off, expired and replayed
decisions, script or policy edited after the decision, forged ticket carried on a bg job record.
Keys and fake secrets are generated at runtime; sudo is never actually invoked (injected runner).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chip import bg_jobs, job_policy as jp, policy_exec as px

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean():
    jp.clear_hooks()
    px.clear_notifiers()
    yield
    jp.clear_hooks()
    px.clear_notifiers()


def _write_script(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    op_dir = tmp_path / "operator"
    op_dir.mkdir()
    key = jp.OperatorKey.generate()
    key_file = op_dir / "op.key"
    key_file.write_text(key.seed_hex() + "\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    show = work / "show.sh"
    show_sha = _write_script(show, 'echo "actor=$CHIP_JOB_ACTOR job=$CHIP_BGJOB_ID yolo=${CHIP_PM_YOLO:-unset}"\n')
    slow = work / "slow.sh"
    slow_sha = _write_script(slow, "sleep 5\n")
    policy = {
        "job_types": {
            "diagnostics": {
                "allow_commands": ["systemctl", "journalctl", "ls"],
                "allow_targets": ["/srv/app", "/srv/app/*"],
                "max_spend_usd": 0.1,
                "max_seconds": 300,
            },
            "build": {"allow_commands": ["make"], "allow_targets": ["/srv/app"], "sudo": "never"},
            "scripted": {"allow_commands": [], "scripts": {str(show): show_sha, str(slow): slow_sha}, "max_seconds": 30},
        }
    }
    pol_file = op_dir / "policy.json"
    pol_file.write_text(json.dumps(policy), encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        jp.FLAG: "1",
        px.EXEC_FLAG: "1",
        jp.YOLO_ENV: "0",
        jp.PUBKEY_ENV: key.public.hex(),
        jp.KEY_FILE_ENV: str(key_file),
        jp.POLICY_FILE_ENV: str(pol_file),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in ("CHIP_BGJOB_ID", "CHIP_JOB_ACTOR"):
        monkeypatch.delenv(k, raising=False)
    jp.pin_policy(key, env=env, now=NOW)
    return {"env": env, "key": key, "pol_file": pol_file, "show": show, "show_sha": show_sha,
            "slow": slow, "slow_sha": slow_sha}


def req(**over):
    base = {
        "job_id": "j1",
        "requested_by": "dev:alpha",
        "job_type": "diagnostics",
        "action": "restart-service",
        "command": ["systemctl", "restart", "app"],
        "target": "/srv/app",
        "context": "repo-x",
        "sudo": False,
        "reason": "service down",
        "max_seconds": 60,
        "max_spend_usd": 0.05,
    }
    base.update(over)
    return base


class FakeRunner:
    def __init__(self, **res):
        self.calls: list[tuple[list[str], dict[str, str], int]] = []
        self.res = {"exit": 0, "stdout": "done", "stderr": "", "timed_out": False, **res}

    def __call__(self, argv, env, timeout_s):
        self.calls.append((list(argv), dict(env), timeout_s))
        return dict(self.res)


def auth(setup, r, **kw):
    kw.setdefault("now", NOW)
    return px.authorize(r, actor=kw.pop("actor", "pm"), env=kw.pop("env", setup["env"]), **kw)


def run(setup, ticket, r, runner=None, **kw):
    kw.setdefault("now", NOW)
    return px.execute(ticket, r, env=kw.pop("env", setup["env"]), runner=runner or FakeRunner(), **kw)


# ------------------------------------------------------------------ happy paths


def test_allow_runs_bound_argv_under_scrubbed_env(setup):
    fake_name = "CHIP_FAKE_" + "TO" + "KEN"
    fake_val = uuid.uuid4().hex
    env = {**setup["env"], fake_name: fake_val, jp.YOLO_ENV: "0"}
    a = auth(setup, req(), env=env)
    assert a.verdict == "allow" and a.ticket and a.ticket.binding == a.ticket.compute_binding()
    r = FakeRunner()
    rec = run(setup, a.ticket, req(), r, env=env)
    assert rec["status"] == "ok" and rec["decision_id"] == a.ticket.id and rec["binding"] == a.ticket.binding
    argv, child, secs = r.calls[0]
    assert argv == ["systemctl", "restart", "app"]  # no sudo without a grant
    assert fake_name not in child and fake_val not in child.values()
    assert not any(k.startswith("CHIP_POLICY_") or k in {jp.FLAG, jp.YOLO_ENV} for k in child)
    assert child["CHIP_BGJOB_ID"] == "j1" and child["CHIP_JOB_ACTOR"] == "dev:alpha"
    assert secs == 60
    assert rec["command_sha256"] == jp.JobRequest.from_dict(req()).command_sha256()
    assert "systemctl" not in json.dumps(px.receipts())  # receipts hold hashes, not raw argv


def test_real_subprocess_hash_bound_script(setup):
    r = req(job_type="scripted", command=[str(setup["show"])], script_path=str(setup["show"]),
            script_sha256=setup["show_sha"], target="", max_seconds=10)
    a = auth(setup, r)
    assert a.verdict == "allow", a.reason
    rec = px.execute(a.ticket, r, env=setup["env"], now=NOW)
    assert rec["status"] == "ok", rec
    assert "actor=dev:alpha job=j1 yolo=unset" in rec["stdout_tail"]


def test_time_cap_kills_real_subprocess(setup):
    r = req(job_type="scripted", command=[str(setup["slow"])], script_path=str(setup["slow"]),
            script_sha256=setup["slow_sha"], target="", max_seconds=1)
    a = auth(setup, r)
    rec = px.execute(a.ticket, r, env=setup["env"], now=NOW)
    assert rec["status"] == "failed" and rec["reason"] == "time cap" and rec["seconds_cap"] == 1
    assert rec["seconds"] < 4


def test_time_cap_clamped_to_policy(setup):
    a = auth(setup, req(max_seconds=0))
    r = FakeRunner()
    run(setup, a.ticket, req(max_seconds=0), r)
    assert r.calls[0][2] == 300  # job type max_seconds


def test_spend_cap(setup):
    a = auth(setup, req())
    rec = run(setup, a.ticket, req(), FakeRunner(spend_usd=0.5))
    assert rec["status"] == "failed" and rec["reason"] == "spend cap"


def test_nonzero_exit_fails(setup):
    a = auth(setup, req())
    rec = run(setup, a.ticket, req(), FakeRunner(exit=3))
    assert rec["status"] == "failed" and rec["reason"] == "exit 3"


def test_sudo_via_operator_rule_prefixes_noninteractive_sudo(setup):
    first = auth(setup, req(sudo=True))
    assert first.verdict == "ask"
    jp.confirm(first.request_id, setup["key"], env=setup["env"], now=NOW)
    a = auth(setup, req(sudo=True, job_id="j2"))
    assert a.verdict == "allow" and a.ticket.rule_id and a.ticket.sudo
    r = FakeRunner()
    rec = run(setup, a.ticket, req(sudo=True, job_id="j2"), r)
    assert rec["status"] == "ok" and rec["rule_id"] == a.ticket.rule_id
    assert r.calls[0][0] == ["sudo", "-n", "--", "systemctl", "restart", "app"]


def test_sudo_via_yolo_grant(setup):
    env = {**setup["env"], jp.YOLO_ENV: "1"}
    a = auth(setup, req(sudo=True), env=env, pm_reason="app down")
    assert a.verdict == "allow" and a.ticket.grant_id
    r = FakeRunner()
    rec = run(setup, a.ticket, req(sudo=True), r, env=env)
    assert rec["status"] == "ok" and rec["grant_id"] == a.ticket.grant_id
    assert r.calls[0][0][:3] == ["sudo", "-n", "--"]
    assert jp.YOLO_ENV not in r.calls[0][1]


def test_dev_job_entry_point(setup):
    r = FakeRunner()
    out = px.run_dev_job(req(), pm_reason="triage", env=setup["env"], now=NOW, runner=r)
    assert out["verdict"] == "allow" and out["receipt"]["status"] == "ok" and len(r.calls) == 1


# ------------------------------------------------------------------ attack matrix


@pytest.mark.parametrize(
    "swap",
    [
        {"command": ["systemctl", "stop", "app"]},
        {"command": ["ls", "/srv/app"]},
        {"target": "/srv/app/logs"},
        {"context": "repo-y"},
        {"requested_by": "dev:beta"},
        {"job_id": "j-other"},
        {"sudo": True},
    ],
)
def test_swap_after_decision_refused(setup, swap):
    a = auth(setup, req())
    r = FakeRunner()
    rec = run(setup, a.ticket, req(**swap), r)
    assert rec["status"] == "refused" and "does not match the decision" in rec["reason"]
    assert r.calls == []


def test_sudo_without_grant_edited_ticket_refused(setup):
    a = auth(setup, req())
    forged = {**a.ticket.to_dict(), "sudo": True}
    forged["binding"] = px.Ticket.from_dict(forged).compute_binding()  # attacker recomputes the hash
    r = FakeRunner()
    rec = run(setup, forged, req(sudo=True), r)
    assert rec["status"] == "refused" and "altered" in rec["reason"]
    assert r.calls == []


def test_sudo_borrowing_another_rule_refused(setup):
    first = auth(setup, req(sudo=True))
    res = jp.confirm(first.request_id, setup["key"], env=setup["env"], now=NOW)
    a = auth(setup, req())  # non-sudo ticket
    forged = {**a.ticket.to_dict(), "rule_id": res["rule"]["id"]}
    forged["binding"] = px.Ticket.from_dict(forged).compute_binding()
    rec = run(setup, forged, req())
    assert rec["status"] == "refused"


def test_sudo_rule_revoked_between_decision_and_run(setup):
    first = auth(setup, req(sudo=True))
    res = jp.confirm(first.request_id, setup["key"], env=setup["env"], now=NOW)
    a = auth(setup, req(sudo=True, job_id="j2"))
    jp.revoke(res["rule"]["id"], setup["key"], env=setup["env"], now=NOW)
    r = FakeRunner()
    rec = run(setup, a.ticket, req(sudo=True, job_id="j2"), r)
    assert rec["status"] == "refused" and "revoked" in rec["reason"] and r.calls == []


def test_yolo_grant_expired_refused(setup):
    env = {**setup["env"], jp.YOLO_ENV: "1", jp.GRANT_TTL_ENV: "60", px.DECISION_TTL_ENV: "900"}
    a = auth(setup, req(sudo=True), env=env)
    r = FakeRunner()
    rec = run(setup, a.ticket, req(sudo=True), r, env=env, now=NOW + timedelta(seconds=120))
    assert rec["status"] == "refused" and "grant expired" in rec["reason"] and r.calls == []


def test_sudo_never_job_type_refused_even_with_yolo(setup):
    env = {**setup["env"], jp.YOLO_ENV: "1"}
    a = auth(setup, req(job_type="build", command=["make"], sudo=True), env=env)
    assert a.verdict == "deny" and a.ticket is None


def test_self_approval_dev_bot_actor_gets_no_ticket(setup):
    a = auth(setup, req(), actor="dev:alpha")
    assert a.verdict == "deny" and a.ticket is None
    assert not px.decisions_path().exists() or "dev:alpha" not in px.decisions_path().read_text()


def test_self_approval_hand_made_ticket_refused(setup):
    r0 = jp.JobRequest.from_dict(req(sudo=True))
    t = px.Ticket(id="dec-" + uuid.uuid4().hex[:16], job_id="j1", requested_by="dev:alpha", digest=r0.digest(),
                  command_sha256=r0.command_sha256(), sudo=True, rule_id="", grant_id="",
                  issued_at=jp._iso(NOW), expires_at=jp._iso(NOW + timedelta(hours=1)))
    t = px.Ticket(**{**t.to_dict(), "binding": t.compute_binding()})
    r = FakeRunner()
    rec = run(setup, t, req(sudo=True), r)
    assert rec["status"] == "refused" and "not issued by the PM" in rec["reason"] and r.calls == []


def test_executor_refuses_inside_a_job(setup):
    a = auth(setup, req())
    r = FakeRunner()
    rec = run(setup, a.ticket, req(), r, env={**setup["env"], "CHIP_BGJOB_ID": "x"})
    assert rec["status"] == "refused" and "inside a job" in rec["reason"] and r.calls == []


@pytest.mark.parametrize("flag", [jp.FLAG, px.EXEC_FLAG])
def test_flags_off_refuse(setup, flag):
    a = auth(setup, req())
    r = FakeRunner()
    rec = run(setup, a.ticket, req(), r, env={**setup["env"], flag: "0"})
    assert rec["status"] == "refused" and r.calls == []


def test_executor_default_off(setup):
    env = {k: v for k, v in setup["env"].items() if k != px.EXEC_FLAG}
    assert not px.exec_enabled(env)


def test_expired_decision_refused(setup):
    a = auth(setup, req())
    r = FakeRunner()
    rec = run(setup, a.ticket, req(), r, now=NOW + timedelta(seconds=901))
    assert rec["status"] == "refused" and "expired" in rec["reason"] and r.calls == []


def test_replay_refused(setup):
    a = auth(setup, req())
    assert run(setup, a.ticket, req())["status"] == "ok"
    r = FakeRunner()
    rec = run(setup, a.ticket, req(), r)
    assert rec["status"] == "refused" and "single use" in rec["reason"] and r.calls == []


def test_script_edited_after_decision_refused(setup):
    r0 = req(job_type="scripted", command=[str(setup["show"])], script_path=str(setup["show"]),
             script_sha256=setup["show_sha"], target="", max_seconds=10)
    a = auth(setup, r0)
    assert a.verdict == "allow", a.reason
    setup["show"].write_text("#!/bin/sh\necho evil\n", encoding="utf-8")
    r = FakeRunner()
    rec = run(setup, a.ticket, r0, r)
    assert rec["status"] == "refused" and "recheck" in rec["reason"] and r.calls == []


def test_policy_widened_after_decision_refused(setup):
    a = auth(setup, req())
    pol = json.loads(setup["pol_file"].read_text())
    pol["job_types"]["diagnostics"]["allow_commands"].append("*")
    setup["pol_file"].write_text(json.dumps(pol))
    rec = run(setup, a.ticket, req())
    assert rec["status"] == "refused" and "not pinned" in rec["reason"]


def test_new_hard_deny_after_decision_refused(setup):
    a = auth(setup, req())
    jp.register_hard_deny("plugin", lambda r: "service is off limits" if "app" in r.command else None)
    rec = run(setup, a.ticket, req())
    assert rec["status"] == "refused" and "hard deny" in rec["reason"]


def test_executor_module_and_decision_store_are_protected(setup):
    for target_tok in (str(Path(px.__file__)), str(px.decisions_path())):
        a = auth(setup, req(command=["ls", target_tok]))
        assert a.verdict == "deny" and "hard deny" in a.reason


def test_refusals_are_receipted(setup):
    a = auth(setup, req())
    run(setup, a.ticket, req(target="/srv/app/x"))
    rows = px.receipts()
    assert rows[-1]["status"] == "refused" and rows[-1]["decision_id"] == a.ticket.id


# ------------------------------------------------------------------ operator confirm notifications


def test_ask_posts_confirm_once_when_enabled(setup):
    got = []
    env = {**setup["env"], px.NOTIFY_FLAG: "1"}
    px.register_notifier(lambda msg, row: got.append((msg, row)))
    a = auth(setup, req(sudo=True), env=env, pm_reason="app down 10 min")
    assert a.verdict == "ask" and a.notify == "sent"
    msg = got[0][0]
    assert f"chip policy confirm {a.request_id}" in msg and "dev:alpha" in msg and "app down 10 min" in msg
    again = auth(setup, req(sudo=True, job_id="j2"), env=env)
    assert again.request_id == a.request_id and again.notify == "duplicate" and len(got) == 1


def test_notify_default_off_and_no_notifier(setup):
    got = []
    px.register_notifier(lambda msg, row: got.append(msg))
    a = auth(setup, req(sudo=True))
    assert a.notify == "off" and got == []
    px.clear_notifiers()
    b = auth(setup, req(sudo=True, target="/srv/app/x"), env={**setup["env"], px.NOTIFY_FLAG: "1"})
    assert b.notify == "no-notifier"


def test_notifier_error_does_not_raise_and_retries_later(setup):
    env = {**setup["env"], px.NOTIFY_FLAG: "1"}

    def boom(msg, row):
        raise RuntimeError("down")

    a = auth(setup, req(sudo=True), env=env, notifier=boom)
    assert a.verdict == "ask" and a.notify == "error"
    got = []
    b = auth(setup, req(sudo=True), env=env, notifier=lambda m, r: got.append(m))
    assert b.notify == "sent" and len(got) == 1


# ------------------------------------------------------------------ bg_jobs routing


def _bg_env(setup, **extra):
    return {**setup["env"], "CHIP_JOBS": "1", **extra}


def _exec_job(job_id, **over):
    return bg_jobs.enqueue("run approved command", now=NOW, mode="mutate", job_id=job_id, max_spend_usd=0.05,
                           params={"exec": True, "policy_request": req(**over), "pm_reason": "triage"})


def test_bg_mutate_without_request_is_denied_under_policy(setup):
    bg_jobs.enqueue("change something", now=NOW, mode="mutate", job_id="m1")
    res = bg_jobs.run_pending(NOW, env=_bg_env(setup))
    job = res["ran"][0]
    assert job["status"] == "failed" and job["reason"].startswith("policy deny")


def test_bg_exec_job_runs_through_executor(setup):
    _exec_job("m2")
    r = FakeRunner(stdout="restarted")
    res = bg_jobs.run_pending(NOW, env=_bg_env(setup), exec_runner=r)
    job = res["ran"][0]
    assert job["status"] == "ok" and "restarted" in job["hops"][0]["output"]
    assert r.calls[0][1]["CHIP_BGJOB_ID"] == "m2"
    assert px.receipts()[-1]["decision_id"] == job["policy"]["ticket"]["id"]


def test_bg_exec_sudo_asks_then_runs_after_confirm(setup):
    got = []
    px.register_notifier(lambda m, row: got.append(m))
    env = _bg_env(setup, **{px.NOTIFY_FLAG: "1"})
    _exec_job("m3", sudo=True)
    r = FakeRunner()
    res = bg_jobs.run_pending(NOW, env=env, exec_runner=r)
    assert res["ran"] == [] and "waiting on operator confirm" in res["deferred"][0]["reason"]
    rid = bg_jobs.load("m3")["policy"]["request_id"]
    assert bg_jobs.load("m3")["status"] == "queued" and len(got) == 1 and r.calls == []
    jp.confirm(rid, setup["key"], env=setup["env"], now=NOW)
    res = bg_jobs.run_pending(NOW, env=env, exec_runner=r)
    assert res["ran"][0]["status"] == "ok" and r.calls[0][0][:3] == ["sudo", "-n", "--"]


def test_bg_forged_ticket_on_record_is_ignored(setup):
    job = _exec_job("m4", sudo=True)
    job["policy"] = {"verdict": "allow", "ticket": {"id": "dec-forged", "sudo": True}}
    bg_jobs._save(job)
    r = FakeRunner()
    res = bg_jobs.run_pending(NOW, env=_bg_env(setup), exec_runner=r)
    assert res["ran"] == [] and r.calls == []  # re-evaluated: sudo asks, nothing ran


def test_bg_exec_job_with_policy_off_never_executes(setup):
    _exec_job("m5")
    r = FakeRunner()
    env = _bg_env(setup, **{jp.FLAG: "0"})
    res = bg_jobs.run_pending(NOW, env=env, exec_runner=r)
    assert res["ran"][0]["status"] == "ok" and r.calls == []  # mock step, executor untouched
    assert "mock" in res["ran"][0]["hops"][0]["output"]


def test_bg_exec_refusal_fails_job(setup):
    _exec_job("m6", target="/srv/elsewhere")
    res = bg_jobs.run_pending(NOW, env=_bg_env(setup), exec_runner=FakeRunner())
    assert res["ran"][0]["status"] == "failed" and "policy deny" in res["ran"][0]["reason"]


# ------------------------------------------------------------------ M5 attack matrix: explicit gap cases


def test_sudo_without_signed_rule_or_yolo_grant_refused(setup):
    r0 = jp.JobRequest.from_dict(req(sudo=True))
    rows = jp.verify_chain(setup["env"])
    assert jp.sudo_basis_ok(r0, rows=rows, env=setup["env"], now=NOW) == "sudo without an operator rule or YOLO grant"
    assert "no such YOLO grant" in jp.sudo_basis_ok(r0, grant_id="yg-forged", rows=rows, env=setup["env"], now=NOW)
    assert "revoked or expired" in jp.sudo_basis_ok(r0, rule_id="rule-forged", rows=rows, env=setup["env"], now=NOW)
    # with YOLO off and no rule, the PM can only ask; no ticket, nothing runs
    a = auth(setup, req(sudo=True))
    assert a.verdict == "ask" and a.ticket is None and jp.grants() == []


def test_yolo_flip_from_inside_a_job_has_no_effect(setup):
    # the env a job gets carries no YOLO / policy controls; flipping YOLO there leaves policy off
    inside = jp.job_env(setup["env"], job_id="j0", actor="dev:alpha")
    flipped = {**inside, jp.YOLO_ENV: "1"}
    d = jp.evaluate(req(sudo=True), actor="pm", env=flipped, now=NOW)
    assert d.verdict == "deny" and jp.grants() == []
    # commands that would flip YOLO are hard-denied
    for argv in (["export", "CHIP_PM_YOLO=1"], ["sed", "-i", "s/false/true/", "pm_yolo.json"]):
        assert auth(setup, req(command=argv)).verdict == "deny"
    # even with every flag re-armed inside the job, the executor refuses to run anything there
    rearmed = {**setup["env"], jp.YOLO_ENV: "1", "CHIP_BGJOB_ID": "j0", "CHIP_JOB_ACTOR": "dev:alpha"}
    a = auth(setup, req(sudo=True, job_id="j0"))  # PM outside the job: YOLO still off
    assert a.verdict == "ask" and a.ticket is None
    ok = auth(setup, req(job_id="j0"))
    r = FakeRunner()
    rec = run(setup, ok.ticket, req(job_id="j0"), r, env=rearmed)
    assert rec["status"] == "refused" and "inside a job" in rec["reason"] and r.calls == []
    # and back in the PM process YOLO is still off: sudo still asks the operator
    assert auth(setup, req(sudo=True, job_id="j3")).verdict == "ask"
