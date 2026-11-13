"""chip.forge_driver: network git calls (checkout clone, open push) retry ONLY the "Repository not
found" / HTTP 404 answer a freshly minted installation token can get for a few seconds; any other
git failure fails at once, and the token never reaches argv, output or logs.

Offline: a fake git that fails N times with a given message, and a recording sleep.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chip import dev_pr as dp, forge_driver as fd, job_policy as jp
from test_chip_dev_pr import AUTHOR, assigned, env, job_receipt  # noqa: F401
from test_chip_forge_driver import SHA, TOK, FakeForge
from test_chip_policy_exec import NOW, setup  # noqa: F401

NOT_FOUND = "remote: Repository not found.\nfatal: repository 'https://github.com/x/y.git/' not found\n"


class FlakyGit:
    def __init__(self, fails: int, msg: str = NOT_FOUND):
        self.fails, self.msg, self.calls = fails, msg, []

    def __call__(self, argv, cwd, env):
        argv = list(argv)
        self.calls.append(argv)
        net = "clone" in argv or "push" in argv
        if net and self.fails > 0:
            self.fails -= 1
            return 128, self.msg + f" {TOK}"
        if "clone" in argv:
            Path(argv[-1]).mkdir(parents=True)
            return 0, ""
        if argv[-2:] == ["rev-parse", "HEAD"]:
            return 0, SHA + "\n"
        return 0, ""

    def net_calls(self):
        return [a for a in self.calls if "clone" in a or "push" in a]


@pytest.fixture
def fenv(env):  # noqa: F811
    return {**env, "CHIP_FORGE_TOKEN": TOK}


def drv(fenv, git, sleeps):
    return fd.Driver(env=fenv, http=FakeForge(), git=git, now=NOW, sleep=sleeps.append)


@pytest.mark.parametrize("fails", [1, 2, 3])
def test_checkout_retries_not_found_then_succeeds(fenv, fails):
    it, git, sleeps = assigned(fenv), FlakyGit(fails), []
    out = drv(fenv, git, sleeps).checkout(it["id"], actor=AUTHOR)
    assert out["base_sha"] == SHA and len(git.net_calls()) == fails + 1
    assert sleeps == list(fd.GIT_RETRY_DELAYS[:fails])


def test_checkout_gives_up_after_the_retries(fenv):
    it, git, sleeps = assigned(fenv), FlakyGit(len(fd.GIT_RETRY_DELAYS) + 1), []
    with pytest.raises(fd.ForgeError) as exc:
        drv(fenv, git, sleeps).checkout(it["id"], actor=AUTHOR)
    assert "not found" in str(exc.value) and TOK not in str(exc.value) and "***" in str(exc.value)
    assert sleeps == list(fd.GIT_RETRY_DELAYS) and len(git.net_calls()) == len(fd.GIT_RETRY_DELAYS) + 1


@pytest.mark.parametrize("msg", ["fatal: Authentication failed", "error: RPC failed; HTTP 500",
                                 "! [rejected] main -> main (non-fast-forward)"])
def test_other_git_failures_are_not_retried(fenv, msg):
    it, git, sleeps = assigned(fenv), FlakyGit(1, msg), []
    with pytest.raises(fd.ForgeError):
        drv(fenv, git, sleeps).checkout(it["id"], actor=AUTHOR)
    assert sleeps == [] and len(git.net_calls()) == 1


def test_open_push_retries_not_found(fenv):
    it, git, sleeps = assigned(fenv), FlakyGit(1, "error: RPC failed; HTTP 404 curl 22 The requested URL returned error: 404"), []
    dec = job_receipt(fenv)
    out = drv(fenv, git, sleeps).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/x", title="t", body="",
                                      decision_id=dec)
    assert out["state"] == "submitted" and sleeps == [fd.GIT_RETRY_DELAYS[0]]
    pushes = [a for a in git.calls if "push" in a]
    assert len(pushes) == 2 and pushes[0] == pushes[1] and not any(TOK in x for a in git.calls for x in a)
    assert dp.show(it["id"])["state"] == "submitted"
    assert TOK not in (jp.policy_dir() / fd.FILE_NAME).read_text()


def test_local_git_calls_never_retry(fenv):
    git, sleeps = FlakyGit(0), []
    d = drv(fenv, git, sleeps)
    calls = []
    d.git = lambda argv, cwd, env: (calls.append(argv), (128, NOT_FOUND))[1]
    with pytest.raises(fd.ForgeError):
        d._run_git(["git", "rev-parse", "HEAD"], "/wt")
    assert len(calls) == 1 and sleeps == []
