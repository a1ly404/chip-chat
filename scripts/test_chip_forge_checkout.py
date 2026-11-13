"""chip.forge_driver / chip.forge_steps ``checkout``: clone the item repo's base into the item's fixed
worktree under the data dir, with the remote removed, so the PM-authorized change job edits and commits
there with no credential and nothing to push to.

Offline (fake git that "clones" by creating the dir). Covers: happy path (fixed path from the item id,
shallow single-branch clone of the allowlisted repo, token only in the clone's env, remote removed, base
sha returned and logged), author-only, state gate, existing worktree refused, bad base refused, no auth
= refused before any dir is made, PM orchestrator step (enable flag, summary keys, state gate), and the
full PM-driven flow checkout -> open (that worktree) -> ci -> review -> merge.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from chip import dev_pr as dp, forge_driver as fd, forge_steps as fs, job_policy as jp
from test_chip_dev_pr import AUTHOR, REPO, REVIEWER, assigned, env, job_receipt  # noqa: F401
from test_chip_forge_driver import MERGED, SHA, TOK, FakeForge
from test_chip_policy_exec import NOW, setup  # noqa: F401

BASE_SHA = "d" * 40


class CloneGit:
    """Fake git: ``clone`` makes the target dir; ``rev-parse`` answers; ``push`` ok; records everything."""

    def __init__(self, head=SHA, base=BASE_SHA):
        self.head, self.base, self.calls = head, base, []

    def __call__(self, argv, cwd, env):
        argv = list(argv)
        self.calls.append((argv, dict(env), cwd))
        if "clone" in argv:
            Path(argv[-1]).mkdir(parents=True)
            (Path(argv[-1]) / ".git").mkdir()
            return 0, ""
        if argv[-2:] == ["remote", "remove"] or argv[-3:-1] == ["remote", "remove"]:
            return 0, ""
        if argv[-2:] == ["rev-parse", "HEAD"]:
            return 0, (self.base if "-C" in argv else self.head) + "\n"
        if "push" in argv:
            return 0, "ok"
        return 1, "unknown"


@pytest.fixture
def fenv(env):  # noqa: F811
    return {**env, "CHIP_FORGE_TOKEN": TOK}


def drv(fenv, git=None, forge=None):
    return fd.Driver(env=fenv, http=forge or FakeForge(), git=git or CloneGit(), now=NOW)


def test_checkout_happy_path(fenv):
    it = assigned(fenv)
    git = CloneGit()
    out = drv(fenv, git).checkout(it["id"], actor=AUTHOR)
    wt = jp.data_dir() / "devpr" / it["id"] / "wt"
    assert out == {"id": it["id"], "worktree": str(wt), "base": "main", "base_sha": BASE_SHA, "state": "assigned"}
    assert wt.is_dir() and fd.worktree_path(it["id"]) == wt
    clone, clone_env, _ = git.calls[0]
    assert clone[-2:] == [f"https://github.com/{REPO}.git", str(wt)]
    assert {"--depth", "1", "--single-branch", "--no-tags"} <= set(clone) and clone[clone.index("--branch") + 1] == "main"
    assert not any(TOK in a for a, _, _ in git.calls for a in a)
    assert "GIT_CONFIG_VALUE_0" in clone_env and TOK not in clone_env["GIT_CONFIG_VALUE_0"]
    rm, rm_env, _ = git.calls[1]
    assert rm == ["git", "-C", str(wt), "remote", "remove", "origin"] and "GIT_CONFIG_VALUE_0" not in rm_env
    rows = jp._jsonl(jp.policy_dir() / fd.FILE_NAME)
    assert rows[-1]["step"] == "checkout" and rows[-1]["base_sha"] == BASE_SHA and TOK not in str(rows)
    assert dp.show(it["id"])["state"] == "assigned"  # checkout does not move the ledger


def test_checkout_author_only_and_state_gate(fenv):
    it = assigned(fenv)
    with pytest.raises(dp.DevPrRefused, match="assigned author"):
        drv(fenv).checkout(it["id"], actor=REVIEWER)
    dec = job_receipt(fenv)
    drv(fenv).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/x", title="t", body="", decision_id=dec)
    with pytest.raises(dp.DevPrRefused, match="state 'submitted'"):
        drv(fenv).checkout(it["id"], actor=AUTHOR)


def test_checkout_existing_worktree_refused(fenv):
    it = assigned(fenv)
    drv(fenv).checkout(it["id"], actor=AUTHOR)
    git = CloneGit()
    with pytest.raises(dp.DevPrRefused, match="already exists"):
        drv(fenv, git).checkout(it["id"], actor=AUTHOR)
    assert git.calls == []


@pytest.mark.parametrize("base", ["", "-x", "a..b", "main;rm"])
def test_checkout_bad_base_refused(fenv, base):
    it = assigned(fenv)
    git = CloneGit()
    with pytest.raises(dp.DevPrRefused, match="base"):
        drv(fenv, git).checkout(it["id"], actor=AUTHOR, base=base)
    assert git.calls == [] and not (jp.data_dir() / "devpr").exists()


def test_checkout_without_auth_refused_before_any_dir(env):  # noqa: F811
    it = assigned(env)
    git = CloneGit()
    with pytest.raises(dp.DevPrRefused, match="no forge token"):
        fd.Driver(env=env, http=FakeForge(), git=git, now=NOW).checkout(it["id"], actor=AUTHOR)
    assert git.calls == [] and not (jp.data_dir() / "devpr").exists()


def test_worktree_path_rejects_bad_item():
    with pytest.raises(dp.DevPrRefused):
        fd.worktree_path("../../etc")


class Runner:
    def __init__(self, env):
        self.denv = {**env, "CHIP_FORGE_TOKEN": TOK}
        self.forge, self.git, self.calls = FakeForge(), CloneGit(), []

    def __call__(self, step, args):
        self.calls.append(step)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fd.main([step, *args], driver=fd.Driver(env=self.denv, http=self.forge, git=self.git, now=NOW))
        return fs.StepResult(rc, fs.parse_output(buf.getvalue()))


def test_pm_checkout_step_and_full_flow(env):  # noqa: F811
    oenv = {**env, fs.FLAG: "1"}
    r = Runner(env)
    it = assigned(oenv)
    s = fs.Steps(env=oenv, runner=r, now=NOW)
    co = s.checkout(it["id"], reason="ticket 7: give the dev job a worktree")
    assert co["status"] == "ok" and co["base_sha"] == BASE_SHA and co["worktree"].endswith(f"/devpr/{it['id']}/wt")
    dec = job_receipt(oenv)
    op = s.open(it["id"], worktree=co["worktree"], branch="dev/alpha/fix-7", title="fix", decision_id=dec,
                reason="job ok, open the PR")
    assert op["status"] == "ok" and op["state"] == "submitted"
    assert r.git.calls[-1][2] == co["worktree"]  # the push ran from the checked-out worktree
    assert s.review(it["id"], verdict="approve", reason="lgtm")["state"] == "approved"
    m = s.merge(it["id"], reason="approved and green")
    assert m["merged"] is True and m["merged_sha"] == MERGED
    assert r.calls == ["checkout", "open", "ci", "review", "ci", "merge"]
    assert [x["step"] for x in fs.history(it["id"])] == ["checkout", "open", "ci", "review", "ci", "merge"]
    with pytest.raises(fs.StepRefused, match="state 'merged'"):
        s.checkout(it["id"], reason="again")


def test_pm_checkout_needs_flag_and_subset(env):  # noqa: F811
    it = assigned(env)
    r = Runner(env)
    with pytest.raises(fs.StepRefused, match="not enabled"):
        fs.Steps(env={**env, fs.FLAG: "open,ci"}, runner=r, now=NOW).checkout(it["id"], reason="x")
    assert r.calls == []
    assert "checkout" in fs.allowed_steps({fs.FLAG: "checkout"}) and "checkout" in fs.allowed_steps({fs.FLAG: "1"})
