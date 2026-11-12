"""chip.forge_driver: push / open PR / review comment / merge pinned to the gated sha, all via chip.dev_pr.

Fake forge + fake git only (no network). Covers: happy path end to end, missing token, repo off the
allowlist, protected branch, bad receipt refused before push, PR head != pushed sha, reviewer is
author, review after head moved, merge with CI red/pending/none, merge inside a job, merge by a dev
bot, merge pinned to the gated sha, token never in output / ledger / git argv.
"""

from __future__ import annotations

import json

import pytest

from chip import dev_pr as dp, forge_driver as fd, job_policy as jp
from test_chip_dev_pr import AUTHOR, REVIEWER, REPO, assigned, env, job_receipt  # noqa: F401
from test_chip_policy_exec import NOW, setup  # noqa: F401

TOK = "ghp_" + "S3CRET" * 6
SHA = "a" * 40
MOVED = "b" * 40
MERGED = "c" * 40


class FakeForge:
    def __init__(self, repo=REPO, head=SHA, runs=None, statuses=None, merge_ok=True):
        self.repo, self.head, self.calls, self.merge_ok = repo, head, [], merge_ok
        self.runs = [{"status": "completed", "conclusion": "success"}] if runs is None else runs
        self.statuses = statuses or []
        self.prs: dict[int, dict] = {}

    def pr_obj(self, n):
        return {"number": n, "state": self.prs.get(n, {}).get("state", "open"), "head": {"sha": self.head},
                "base": {"repo": {"full_name": self.repo}}, "html_url": f"https://forge/{n}"}

    def __call__(self, method, url, body, headers):
        assert url.startswith(fd.API + "/repos/" + REPO + "/"), url
        assert headers["Authorization"] == f"Bearer {TOK}"
        path = url[len(fd.API):]
        self.calls.append((method, path, body))
        if method == "GET" and "/pulls?state=open" in path:
            return 200, [self.pr_obj(n) for n in self.prs]
        if method == "POST" and path.endswith("/pulls"):
            self.prs[41] = {"state": "open"}
            return 201, self.pr_obj(41)
        if method == "GET" and "/pulls/" in path:
            return 200, self.pr_obj(int(path.rsplit("/", 1)[1]))
        if path.endswith("/check-runs?per_page=100"):
            return 200, {"check_runs": self.runs}
        if path.endswith("/status"):
            st = "success" if all(s.get("state") == "success" for s in self.statuses) else "pending"
            return 200, {"state": st, "statuses": self.statuses}
        if method == "POST" and "/comments" in path:
            return 201, {"id": 9001}
        if method == "PUT" and path.endswith("/merge"):
            if body["sha"] != self.head:
                return 409, {"message": "Head branch was modified"}
            return (200, {"merged": True, "sha": MERGED}) if self.merge_ok else (405, {"message": "not mergeable"})
        return 404, {"message": "nope"}


class FakeGit:
    def __init__(self, head=SHA, fail_push=False):
        self.head, self.fail_push, self.calls = head, fail_push, []

    def __call__(self, argv, cwd, env):
        self.calls.append((list(argv), dict(env)))
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return 0, self.head + "\n"
        if "push" in argv:
            return (1, f"fatal: auth {TOK} denied") if self.fail_push else (0, "ok")
        return 1, "unknown"


@pytest.fixture
def fenv(env):  # noqa: F811
    return {**env, "CHIP_FORGE_TOKEN": TOK}


def drv(fenv, forge=None, git=None):
    return fd.Driver(env=fenv, http=forge or FakeForge(), git=git or FakeGit(), now=NOW)


def opened(fenv, forge, git=None):
    it = assigned(fenv)
    dec = job_receipt(fenv)
    out = drv(fenv, forge, git).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/alpha/fix-7",
                                     title="fix", body="b", decision_id=dec)
    return it["id"], out


def reviewed(fenv, forge):
    iid, _ = opened(fenv, forge)
    drv(fenv, forge).review(iid, actor=REVIEWER, verdict="approve", note="lgtm")
    return iid


def test_happy_path_end_to_end(fenv):
    forge, git = FakeForge(), FakeGit()
    iid, out = opened(fenv, forge, git)
    assert out["pr"] == 41 and out["head_sha"] == SHA and out["state"] == "submitted"
    push = [a for a, _ in git.calls if "push" in a][0]
    assert push[-2] == f"https://github.com/{REPO}.git" and push[-1] == f"{SHA}:refs/heads/dev/alpha/fix-7"
    assert "--force" not in push and "-f" not in push
    r = drv(fenv, forge).review(iid, actor=REVIEWER, verdict="approve", note="lgtm")
    assert r["comment_id"] == 9001 and r["state"] == "approved"
    body = [b for m, p, b in forge.calls if m == "POST" and "/comments" in p][0]["body"]
    assert REVIEWER in body and SHA in body and "APPROVE" in body
    m = drv(fenv, forge).merge(iid, actor=jp.PM)
    assert m["merged"] and m["merged_sha"] == MERGED
    put = [b for mm, p, b in forge.calls if mm == "PUT"][0]
    assert put == {"sha": SHA, "merge_method": "squash"}
    assert dp.show(iid)["state"] == "merged" and dp.show(iid)["merged_sha"] == MERGED


def test_token_never_leaks(fenv, capsys):
    forge, git = FakeForge(), FakeGit(fail_push=True)
    it = assigned(fenv)
    dec = job_receipt(fenv)
    with pytest.raises(fd.ForgeError) as exc:
        drv(fenv, forge, git).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/x", title="t", body="",
                                   decision_id=dec)
    assert TOK not in str(exc.value) and "***" in str(exc.value)
    for argv, _env in git.calls:
        assert not any(TOK in a for a in argv)
    push_env = [e for a, e in git.calls if "push" in a][0]
    assert push_env["GIT_TERMINAL_PROMPT"] == "0" and TOK not in push_env["GIT_CONFIG_VALUE_0"]
    assert TOK not in (jp.policy_dir() / fd.FILE_NAME).read_text() if (jp.policy_dir() / fd.FILE_NAME).exists() else True
    assert TOK not in dp.ledger_path().read_text()
    rc = fd.main(["open", it["id"], "--actor", AUTHOR, "--worktree", "/wt", "--branch", "dev/x", "--title", "t",
                  "--decision-id", dec], driver=drv(fenv, forge, git))
    assert rc == fd.EXIT_FORGE and TOK not in capsys.readouterr().out


def test_redact_basic_auth_form():
    import base64
    b = base64.b64encode(f"x-access-token:{TOK}".encode()).decode()
    assert TOK not in fd.redact(f"x {TOK} {b}", TOK) and b not in fd.redact(b, TOK)


def test_missing_token_refused_before_network(env):  # noqa: F811
    forge, git = FakeForge(), FakeGit()
    it = assigned(env)
    dec = job_receipt(env)
    e = {k: v for k, v in env.items() if k not in fd.TOKEN_ENVS}
    with pytest.raises(dp.DevPrRefused, match="no forge token"):
        fd.Driver(env=e, http=forge, git=git, now=NOW).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/x",
                                                           title="t", body="", decision_id=dec)
    assert forge.calls == [] and git.calls == []


def test_repo_removed_from_allowlist_refused(fenv, monkeypatch):
    forge, git = FakeForge(), FakeGit()
    it = assigned(fenv)
    dec = job_receipt(fenv)
    e = {**fenv, dp.REPOS_ENV: "other/repo"}
    monkeypatch.setenv(dp.REPOS_ENV, "other/repo")
    with pytest.raises(dp.DevPrRefused, match="allowlist"):
        fd.Driver(env=e, http=forge, git=git, now=NOW).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/x",
                                                           title="t", body="", decision_id=dec)
    assert forge.calls == [] and git.calls == []


@pytest.mark.parametrize("branch", ["main", "master", "dev/../main", "-x", "dev/x.lock", "", "a b"])
def test_bad_branch_refused(fenv, branch):
    forge, git = FakeForge(), FakeGit()
    it = assigned(fenv)
    dec = job_receipt(fenv)
    with pytest.raises(dp.DevPrRefused, match="branch"):
        drv(fenv, forge, git).open(it["id"], actor=AUTHOR, worktree="/wt", branch=branch, title="t", body="",
                                   decision_id=dec)
    assert git.calls == []


def test_non_author_cannot_open(fenv):
    forge, git = FakeForge(), FakeGit()
    it = assigned(fenv)
    dec = job_receipt(fenv)
    with pytest.raises(dp.DevPrRefused, match="author"):
        drv(fenv, forge, git).open(it["id"], actor=REVIEWER, worktree="/wt", branch="dev/x", title="t", body="",
                                   decision_id=dec)
    assert git.calls == []


def test_bad_receipt_refused_before_push(fenv):
    forge, git = FakeForge(), FakeGit()
    it = assigned(fenv)
    dec = job_receipt(fenv, requested_by=REVIEWER)
    with pytest.raises(dp.DevPrRefused, match="not requested by the assigned author"):
        drv(fenv, forge, git).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/x", title="t", body="",
                                   decision_id=dec)
    assert git.calls == [] and forge.calls == []


def test_pr_head_mismatch_refuses_submit(fenv):
    forge, git = FakeForge(head=MOVED), FakeGit(head=SHA)
    it = assigned(fenv)
    dec = job_receipt(fenv)
    with pytest.raises(dp.DevPrRefused, match="does not match the pushed sha"):
        drv(fenv, forge, git).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/x", title="t", body="",
                                   decision_id=dec)
    assert dp.show(it["id"])["state"] == "assigned"


def test_author_cannot_review_and_no_comment(fenv):
    forge = FakeForge()
    iid, _ = opened(fenv, forge)
    with pytest.raises(dp.DevPrRefused, match="author cannot review"):
        drv(fenv, forge).review(iid, actor=AUTHOR, verdict="approve")
    assert not [c for c in forge.calls if c[0] == "POST" and "/comments" in c[1]]


def test_review_after_head_moved_refused(fenv):
    forge = FakeForge()
    iid, _ = opened(fenv, forge)
    forge.head = MOVED
    with pytest.raises(dp.DevPrRefused, match="does not match the submitted head"):
        drv(fenv, forge).review(iid, actor=REVIEWER, verdict="approve")


@pytest.mark.parametrize("runs,statuses,want", [
    ([{"status": "completed", "conclusion": "failure"}], [], "failure"),
    ([{"status": "in_progress", "conclusion": None}], [], "pending"),
    ([], [], "none"),
    ([{"status": "completed", "conclusion": "success"}], [{"state": "pending"}], "pending"),
])
def test_merge_denied_unless_ci_green(fenv, runs, statuses, want):
    forge = FakeForge()
    iid = reviewed(fenv, forge)
    forge.runs, forge.statuses = runs, statuses
    out = drv(fenv, forge).merge(iid, actor=jp.PM)
    assert out["verdict"] == "deny" and not out["merged"] and any(want in r for r in out["reasons"])
    assert not [c for c in forge.calls if c[0] == "PUT"]


def test_merge_denied_when_head_moved_after_approval(fenv):
    forge = FakeForge()
    iid = reviewed(fenv, forge)
    forge.head = MOVED
    out = drv(fenv, forge).merge(iid, actor=jp.PM)
    assert out["verdict"] == "deny" and not [c for c in forge.calls if c[0] == "PUT"]


def test_merge_only_pm_and_not_in_job(fenv, monkeypatch):
    forge = FakeForge()
    iid = reviewed(fenv, forge)
    with pytest.raises(dp.DevPrRefused, match="only the PM"):
        drv(fenv, forge).merge(iid, actor=AUTHOR)
    monkeypatch.setenv("CHIP_BGJOB_ID", "bg-1")
    with pytest.raises(dp.DevPrRefused, match="inside a job"):
        drv(fenv, forge).merge(iid, actor=jp.PM)
    assert not [c for c in forge.calls if c[0] == "PUT"]


def test_merge_forge_refusal_records_nothing(fenv):
    forge = FakeForge(merge_ok=False)
    iid = reviewed(fenv, forge)
    with pytest.raises(fd.ForgeError):
        drv(fenv, forge).merge(iid, actor=jp.PM)
    assert dp.show(iid)["state"] == "merge_ok" and not dp.show(iid)["merged_sha"]


def test_closed_pr_not_merged(fenv):
    forge = FakeForge()
    iid = reviewed(fenv, forge)
    forge.prs[41]["state"] = "closed"
    with pytest.raises(dp.DevPrRefused, match="not open"):
        drv(fenv, forge).merge(iid, actor=jp.PM)


def test_cli_ci_and_usage(fenv, capsys):
    forge = FakeForge()
    iid, _ = opened(fenv, forge)
    assert fd.main(["ci", iid], driver=drv(fenv, forge)) == 0
    assert json.loads(capsys.readouterr().out)["ci"] == "success"
    assert fd.main(["bogus"]) == fd.EXIT_USAGE
