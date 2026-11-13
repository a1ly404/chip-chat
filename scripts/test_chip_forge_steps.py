"""chip.forge_steps: the PM drives the forge driver's open / ci / review / merge through a step runner.

Offline. The fake runner runs the real ``chip.forge_driver`` CLI in-process (fake forge + fake git,
static token in the DRIVER's env only), so the arguments the orchestrator builds are parsed by the
real driver and the real dev_pr ledger moves. Covers: default off, no runner, order (no review before
open, no review/merge while CI is not green, no merge without an APPROVE, head moved), PM-only (not
inside a job), reason required, step subsets, tick advance (merge approved, poll submitted, never
open/review), runner env scrub (no CHIP_FORGE_* / secrets), command runner argv + JSON parse, token
never in the step log, pm_tick hook.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess

import pytest

from chip import dev_pr as dp, forge_driver as fd, forge_steps as fs, job_policy as jp, pm_tick
from test_chip_dev_pr import AUTHOR, REVIEWER, assigned, env, job_receipt  # noqa: F401
from test_chip_forge_driver import MERGED, MOVED, SHA, TOK, FakeForge, FakeGit
from test_chip_policy_exec import NOW, setup  # noqa: F401


class DriverRunner:
    """Stands in for the key-holding one-shot process: runs forge_driver.main with its own env."""

    def __init__(self, env, forge=None, git=None):
        self.denv = {**env, "CHIP_FORGE_TOKEN": TOK}
        self.forge, self.git = forge or FakeForge(), git or FakeGit()
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, step, args):
        self.calls.append((step, list(args)))
        buf = io.StringIO()
        d = fd.Driver(env=self.denv, http=self.forge, git=self.git, now=NOW)
        with contextlib.redirect_stdout(buf):
            rc = fd.main([step, *args], driver=d)
        return fs.StepResult(rc, fs.parse_output(buf.getvalue()))

    def steps(self):
        return [s for s, _ in self.calls]


@pytest.fixture
def oenv(env):  # noqa: F811
    return {**env, fs.FLAG: "1"}


def steps(oenv, runner):
    return fs.Steps(env=oenv, runner=runner, now=NOW)


def opened(oenv, runner):
    it = assigned(oenv)
    dec = job_receipt(oenv)
    out = steps(oenv, runner).open(it["id"], worktree="/wt", branch="dev/alpha/fix-7", title="fix",
                                   decision_id=dec, reason="ticket 7 job ok, open the PR")
    return it["id"], out


def approved(oenv, runner):
    iid, _ = opened(oenv, runner)
    steps(oenv, runner).review(iid, verdict="approve", reason="reviewer bot says lgtm")
    return iid


# ------------------------------------------------------------------ happy path


def test_pm_drives_open_ci_review_merge_end_to_end(oenv):
    r = DriverRunner(oenv)
    iid, out = opened(oenv, r)
    assert out["status"] == "ok" and out["pr"] == 41 and out["head_sha"] == SHA and out["state"] == "submitted"
    open_args = r.calls[0][1]
    assert open_args[0] == iid and f"--actor={AUTHOR}" in open_args
    rv = steps(oenv, r).review(iid, verdict="approve", reason="reviewer bot says lgtm")
    assert rv["status"] == "ok" and rv["state"] == "approved"
    assert f"--actor={REVIEWER}" in r.calls[-1][1]
    m = steps(oenv, r).merge(iid, reason="approved and green")
    assert m["status"] == "ok" and m["merged"] is True and m["merged_sha"] == MERGED
    assert r.steps() == ["open", "ci", "review", "ci", "merge"]
    assert f"--actor={jp.PM}" in r.calls[-1][1]
    assert dp.show(iid)["state"] == "merged"
    log = fs.history(iid)
    assert [x["step"] for x in log] == ["open", "ci", "review", "ci", "merge"]
    assert all(x["reason"] for x in log) and log[-1]["reason"] == "approved and green"
    assert log[-1]["result"]["merged_sha"] == MERGED
    assert TOK not in fs.log_path().read_text()


# ------------------------------------------------------------------ default off / config


def test_default_off_refuses_and_logs_without_running(env):  # noqa: F811
    r = DriverRunner(env)
    it = assigned(env)
    with pytest.raises(fs.StepRefused, match="not enabled"):
        fs.Steps(env=env, runner=r, now=NOW).ci(it["id"], reason="poll")
    assert r.calls == []
    assert fs.history(it["id"])[-1]["status"] == "refused"
    for off in ("", "0", "off", "nonsense"):
        assert not fs.enabled({fs.FLAG: off})


def test_step_subsets():
    assert fs.allowed_steps({fs.FLAG: "ci, review"}) == {"ci", "review"}
    assert fs.allowed_steps({fs.FLAG: "all"}) == set(fs.STEPS)


def test_no_runner_configured_refuses(oenv):
    it = assigned(oenv)
    dec = job_receipt(oenv)
    with pytest.raises(fs.StepRefused, match=fs.CMD_ENV):
        fs.Steps(env=oenv, now=NOW).open(it["id"], worktree="/wt", branch="dev/x", title="t", decision_id=dec,
                                         reason="r")


def test_command_parse():
    assert fs.command({}) == []
    assert fs.command({fs.CMD_ENV: "docker compose run"}) == []
    assert fs.command({fs.CMD_ENV: '{"a": 1}'}) == []
    assert fs.command({fs.CMD_ENV: '["x", ""]'}) == []
    assert fs.command({fs.CMD_ENV: '["docker", "compose", "run", "--rm", "chip-forge"]'})[-1] == "chip-forge"


# ------------------------------------------------------------------ order


def test_review_before_open_refused(oenv):
    r = DriverRunner(oenv)
    it = assigned(oenv)
    with pytest.raises(fs.StepRefused, match="open first"):
        steps(oenv, r).review(it["id"], verdict="approve", reason="r")
    assert r.calls == []


@pytest.mark.parametrize("runs,want", [
    ([{"status": "in_progress", "conclusion": None}], "pending"),
    ([{"status": "completed", "conclusion": "failure"}], "failure"),
    ([], "none"),
])
def test_approve_refused_unless_ci_green(oenv, runs, want):
    r = DriverRunner(oenv, forge=FakeForge(runs=runs))
    iid, _ = opened(oenv, r)
    with pytest.raises(fs.StepRefused, match=f"CI is '{want}'"):
        steps(oenv, r).review(iid, verdict="approve", reason="r")
    assert "review" not in r.steps() and dp.show(iid)["state"] == "submitted"


def test_changes_verdict_needs_no_green_ci(oenv):
    r = DriverRunner(oenv, forge=FakeForge(runs=[{"status": "completed", "conclusion": "failure"}]))
    iid, _ = opened(oenv, r)
    out = steps(oenv, r).review(iid, verdict="changes", reason="tests fail")
    assert out["status"] == "ok" and dp.show(iid)["state"] == "changes_requested"


def test_merge_without_approve_refused(oenv):
    r = DriverRunner(oenv)
    iid, _ = opened(oenv, r)
    with pytest.raises(fs.StepRefused, match="no APPROVE"):
        steps(oenv, r).merge(iid, reason="r")
    assert "merge" not in r.steps()


def test_merge_refused_when_ci_red_after_approval(oenv):
    forge = FakeForge()
    r = DriverRunner(oenv, forge=forge)
    iid = approved(oenv, r)
    forge.runs = [{"status": "completed", "conclusion": "failure"}]
    with pytest.raises(fs.StepRefused, match="not success"):
        steps(oenv, r).merge(iid, reason="r")
    assert r.steps().count("merge") == 0 and dp.show(iid)["state"] == "approved"


def test_merge_refused_when_head_moved_after_approval(oenv):
    forge = FakeForge()
    r = DriverRunner(oenv, forge=forge)
    iid = approved(oenv, r)
    forge.head = MOVED
    with pytest.raises(fs.StepRefused, match="head moved"):
        steps(oenv, r).merge(iid, reason="r")
    assert "merge" not in r.steps()


def test_merge_needs_ci_step_enabled(oenv):
    r = DriverRunner(oenv)
    iid = approved(oenv, r)
    with pytest.raises(fs.StepRefused, match="'ci' is not enabled"):
        fs.Steps(env={**oenv, fs.FLAG: "merge"}, runner=r, now=NOW).merge(iid, reason="r")


def test_driver_refusal_is_reported_not_ok(oenv):
    r = DriverRunner(oenv)
    it = assigned(oenv)
    out = steps(oenv, r).open(it["id"], worktree="/wt", branch="main", title="t", decision_id="dec-nope", reason="r")
    assert out["status"] == "refused" and out["rc"] == fd.EXIT_REFUSED and out["refused"]
    assert fs.history(it["id"])[-1]["status"] == "refused"


# ------------------------------------------------------------------ PM-only / reason


def test_refused_inside_a_job(oenv):
    r = DriverRunner(oenv)
    it = assigned(oenv)
    with pytest.raises(fs.StepRefused, match="inside a job"):
        fs.Steps(env={**oenv, "CHIP_BGJOB_ID": "j1"}, runner=r, now=NOW).ci(it["id"], reason="r")
    assert r.calls == []


def test_reason_required_and_item_validated(oenv):
    r = DriverRunner(oenv)
    it = assigned(oenv)
    with pytest.raises(fs.StepRefused, match="reason"):
        steps(oenv, r).ci(it["id"], reason="  ")
    with pytest.raises(fs.StepRefused, match="malformed"):
        steps(oenv, r).ci("--actor=pm", reason="r")
    assert r.calls == []


# ------------------------------------------------------------------ tick advance


def test_advance_off_without_tick_flag(oenv):
    r = DriverRunner(oenv)
    approved(oenv, r)
    n = len(r.calls)
    assert fs.advance(env=oenv, runner=r, now=NOW) == {"status": "off"}
    assert len(r.calls) == n


def test_advance_merges_approved_polls_submitted_never_opens_or_reviews(oenv):
    r = DriverRunner(oenv)
    iid = approved(oenv, r)
    fresh = assigned(oenv, ticket="acme/widgets#8")
    r.calls.clear()
    out = fs.advance(env={**oenv, fs.TICK_ENV: "1"}, runner=r, now=NOW)
    assert out["status"] == "ok"
    assert r.steps() == ["ci", "merge"] and dp.show(iid)["state"] == "merged"
    assert dp.show(fresh["id"])["state"] == "assigned"
    assert "open" not in r.steps() and "review" not in r.steps()


def test_pm_tick_calls_advance_when_on(oenv):
    r = DriverRunner(oenv)
    iid = approved(oenv, r)
    e = {**oenv, fs.TICK_ENV: "1", "CHIP_PM_TICK": "1", "CHIP_PICKER": "1"}
    res = pm_tick.tick(NOW, env=e, forge_runner=r)
    assert res["forge"]["status"] == "ok" and dp.show(iid)["state"] == "merged"
    res2 = pm_tick.tick(NOW, env={**oenv, "CHIP_PM_TICK": "1", "CHIP_PICKER": "1"}, forge_runner=r)
    assert "forge" not in res2


# ------------------------------------------------------------------ runner


def test_runner_env_scrubs_forge_config_and_secrets():
    src = {"PATH": "/bin", "DOCKER_HOST": "unix:///x", "HOME": "/root",
           "CHIP_FORGE_DEV_APP_ID": "", "CHIP_FORGE_PM_APP_KEY_FILE": "/k.pem", "CHIP_FORGE_TOKEN": TOK,
           "GITHUB_TOKEN": TOK, "GH_TOKEN": TOK, "CHIP_WEBHOOK_DEV": "https://hook", "OPENROUTER_API_KEY": "k",
           "CHIP_POLICY_OPERATOR_KEY_FILE": "/op", "CHIP_PM_YOLO": "1", "SSH_PRIVATE_KEY": "x"}
    out = fs.runner_env(src)
    assert out == {"PATH": "/bin", "DOCKER_HOST": "unix:///x", "HOME": "/root"}


def test_command_runner_argv_no_shell_and_json_parse():
    seen = {}

    def run(argv, **kw):
        seen.update(argv=argv, **kw)
        return subprocess.CompletedProcess(argv, 0, stdout='Creating x\n{"id": "devpr-aaaaaaaaaaaa", "ci": "success"}\n',
                                           stderr="warn")

    cr = fs.CommandRunner(["docker", "compose", "run", "--rm", "chip-forge"],
                          env={"PATH": "/bin", "CHIP_FORGE_TOKEN": TOK}, run=run)
    res = cr("ci", ["devpr-aaaaaaaaaaaa"])
    assert seen["argv"] == ["docker", "compose", "run", "--rm", "chip-forge", "ci", "devpr-aaaaaaaaaaaa"]
    assert "shell" not in seen and seen["stdin"] == subprocess.DEVNULL
    assert "CHIP_FORGE_TOKEN" not in seen["env"]
    assert res.rc == 0 and res.out == {"id": "devpr-aaaaaaaaaaaa", "ci": "success"}


def test_command_runner_timeout_and_no_json():
    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)

    assert fs.CommandRunner(["x"], run=slow)("ci", []).rc == fs.EXIT_RUNNER

    def junk(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, stdout=f"boom {TOK}", stderr="")

    res = fs.CommandRunner(["x"], run=junk)("ci", [])
    assert res.out == {} and TOK not in res.error and "no JSON" in res.error


def test_cli_status_and_refusal(oenv, monkeypatch, capsys):
    monkeypatch.delenv(fs.FLAG, raising=False)
    assert fs.main(["status"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["enabled"] is False and st["runner_configured"] is False
    assert fs.main(["ci", "devpr-aaaaaaaaaaaa", "--reason", "r"]) == fs.EXIT_REFUSED
    assert "not enabled" in json.loads(capsys.readouterr().out)["refused"]
