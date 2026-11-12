"""Forge driver for the dev-bot PR pipeline: push, open PR, post review, merge pinned to the gated sha.

``chip.dev_pr`` holds the rules and the ledger but never talks to a forge. This module is the
bounded driver that does the forge calls, and it asks ``chip.dev_pr`` before (and records through it
after) every step. It is repo-locked: the item's repo must be on the ``CHIP_DEVPR_REPOS`` allowlist
at the time of the call, and every forge path is built from that repo, never from caller input.

Steps (all fail closed; nothing is pushed or opened if a precheck refuses):

* ``open``   (the assigned author): precheck the cited executor receipt, push the worktree HEAD to a
  new branch (never ``main``/``master``/the base, never forced), open (or reuse) the PR, check the
  forge's PR head equals the pushed sha, then ``dev_pr.submit``.
* ``review`` (the assigned reviewer): read the PR's CURRENT head from the forge, ``dev_pr.review``
  against it (a moved head is refused), then post the review as a PR comment. A comment, not a
  forge "approval": forges refuse self-approval when both bots share one account; the binding
  approval is the reviewer row in the dev_pr ledger.
* ``merge``  (PM only, never inside a job): read the CURRENT head and CI state from the forge,
  ``dev_pr.merge_gate``; only on ``allow`` merge with the forge's expected-head-sha pin set to the
  gated sha, then ``dev_pr.record_merged``.

Token: ``CHIP_FORGE_TOKEN`` (else ``GITHUB_TOKEN`` / ``GH_TOKEN``) from env. It is never printed,
logged or put on a command line (git gets it via ``GIT_CONFIG_*`` env), and any forge/git output is
redacted before it can surface. Missing token = refuse before any network call.

Every call appends a row (no token, no bodies) to ``<data dir>/policy/forge_driver.jsonl``.

CLI: ``python -m chip.forge_driver open|review|merge|ci`` (JSON out; exit 0 ok, 2 refused/deny,
5 forge error, 64 usage).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from chip import dev_pr as dp
from chip import job_policy as jp

API = "https://api.github.com"
GIT_HOST = "https://github.com"
TOKEN_ENVS = ("CHIP_FORGE_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")
FILE_NAME = "forge_driver.jsonl"
PROTECTED = frozenset({"main", "master", "trunk", "develop", "release"})
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
CI_GOOD = frozenset({"success", "skipped", "neutral"})
CI_BAD = frozenset({"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"})
MAX_BODY = 4000
EXIT_OK, EXIT_REFUSED, EXIT_FORGE, EXIT_USAGE = 0, 2, 5, 64

# http(method, url, body_or_None, headers) -> (status, parsed_json_or_None)
Http = Callable[[str, str, Any, Mapping[str, str]], "tuple[int, Any]"]
# git(argv, cwd, env) -> (returncode, combined_output)
Git = Callable[[Sequence[str], str, Mapping[str, str]], "tuple[int, str]"]


class ForgeError(RuntimeError):
    """The forge or git failed (message is already redacted)."""


def token(env: Mapping[str, str] | None = None) -> str:
    e = jp._env(env)
    for name in TOKEN_ENVS:
        v = str(e.get(name, "") or "").strip()
        if v:
            return v
    return ""


def _secrets(tok: str) -> list[str]:
    if not tok:
        return []
    return [tok, base64.b64encode(f"x-access-token:{tok}".encode()).decode()]


def redact(text: str, tok: str) -> str:
    out = str(text or "")
    for s in _secrets(tok):
        out = out.replace(s, "***")
    return out[:500]


def _log(row: Mapping[str, Any], now: datetime | None) -> None:
    jp._append(jp.policy_dir() / FILE_NAME, {**row, "ts": jp._iso(jp._now(now))})


def _urllib_http(method: str, url: str, body: Any, headers: Mapping[str, str]) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed https API host
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read() or b"null")
        except ValueError:
            parsed = None
        return exc.code, parsed


def _subprocess_git(argv: Sequence[str], cwd: str, env: Mapping[str, str]) -> tuple[int, str]:
    p = subprocess.run(list(argv), cwd=cwd, env=dict(env), capture_output=True, text=True, timeout=180)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


class Driver:
    def __init__(self, *, env: Mapping[str, str] | None = None, http: Http | None = None,
                 git: Git | None = None, now: datetime | None = None) -> None:
        self.env = env
        self.http = http or _urllib_http
        self.git = git or _subprocess_git
        self.now = now
        self.tok = token(env)

    # -------------------------------------------------------------- plumbing
    def _require(self, it: Mapping[str, Any]) -> str:
        dp._common(self.env)
        if not self.tok:
            raise dp.DevPrRefused("no forge token in env (" + "/".join(TOKEN_ENVS) + ")")
        repo = str(it.get("repo") or "").lower()
        if repo not in dp.allowed_repos(self.env):
            raise dp.DevPrRefused(f"repo {repo!r} not in {dp.REPOS_ENV} allowlist")
        return repo

    def _api(self, method: str, path: str, body: Any = None) -> Any:
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self.tok}",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "chip-forge-driver"}
        try:
            status, out = self.http(method, API + path, body, headers)
        except Exception as exc:  # noqa: BLE001 - surface a redacted error only
            raise ForgeError(redact(f"{method} {path.split('?')[0]}: {exc}", self.tok)) from None
        if status >= 400:
            msg = out.get("message", "") if isinstance(out, dict) else ""
            raise ForgeError(redact(f"{method} {path.split('?')[0]} -> {status} {msg}", self.tok))
        return out

    def _pr(self, repo: str, n: int) -> dict[str, Any]:
        pr = self._api("GET", f"/repos/{repo}/pulls/{int(n)}") or {}
        if str(((pr.get("base") or {}).get("repo") or {}).get("full_name", "")).lower() != repo:
            raise dp.DevPrRefused("forge PR does not belong to the item repo")
        return pr

    def ci_state(self, repo: str, sha: str) -> str:
        """``success`` only if every check run and commit status on ``sha`` is green and there is at least one."""
        sha = dp._sha(sha, "sha")
        runs = (self._api("GET", f"/repos/{repo}/commits/{sha}/check-runs?per_page=100") or {}).get("check_runs") or []
        st = self._api("GET", f"/repos/{repo}/commits/{sha}/status") or {}
        statuses = st.get("statuses") or []
        if not runs and not statuses:
            return "none"
        for r in runs:
            if r.get("status") != "completed":
                return "pending"
            if r.get("conclusion") in CI_BAD or r.get("conclusion") not in CI_GOOD:
                return "failure"
        if statuses and st.get("state") != "success":
            return str(st.get("state") or "pending")
        return "success"

    def _git_env(self) -> dict[str, str]:
        auth = base64.b64encode(f"x-access-token:{self.tok}".encode()).decode()
        base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        base.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1",
                     "GIT_CONFIG_KEY_0": f"http.{GIT_HOST}/.extraheader",
                     "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {auth}"})
        return base

    def _run_git(self, argv: Sequence[str], cwd: str, env: Mapping[str, str] | None = None) -> str:
        rc, out = self.git(argv, cwd, env if env is not None else {k: v for k, v in os.environ.items()
                                                                     if not k.startswith("GIT_")})
        if rc != 0:
            raise ForgeError(redact(f"git {argv[1] if len(argv) > 1 else ''} failed: {out}", self.tok))
        return out

    # -------------------------------------------------------------- steps
    def open(self, item_id: str, *, actor: str, worktree: str, branch: str, title: str, body: str,
             decision_id: str, base: str = "main") -> dict[str, Any]:
        it = dp._item(item_id)
        repo = self._require(it)
        if str(actor or "").lower() != it["author"]:
            raise dp.DevPrRefused("only the assigned author may open the PR")
        if it["state"] not in ("assigned", "changes_requested"):
            raise dp.DevPrRefused(f"cannot open in state {it['state']!r}")
        br = str(branch or "").strip()
        if not _BRANCH.match(br) or ".." in br or br.endswith((".lock", "/")) or br.lower() in PROTECTED \
                or br.lower() == str(base).lower():
            raise dp.DevPrRefused("branch name refused (protected, base, or malformed)")
        if not _BRANCH.match(str(base or "")):
            raise dp.DevPrRefused("malformed base branch")
        if not str(title or "").strip():
            raise dp.DevPrRefused("title required")
        dp._receipt_ok(str(decision_id or "").strip(), it["author"])  # before anything leaves the box
        sha = dp._sha(self._run_git(["git", "rev-parse", "HEAD"], worktree).strip().splitlines()[-1], "worktree HEAD")
        self._run_git(["git", "-c", "credential.helper=", "push", "--no-verify", f"{GIT_HOST}/{repo}.git",
                       f"{sha}:refs/heads/{br}"], worktree, self._git_env())
        owner = repo.split("/")[0]
        existing = self._api("GET", f"/repos/{repo}/pulls?state=open&head={owner}:{br}") or []
        if existing:
            pr = existing[0]
        else:
            pr = self._api("POST", f"/repos/{repo}/pulls", {"title": str(title)[:200], "head": br,
                                                            "base": base, "body": str(body or "")[:MAX_BODY]}) or {}
        n = int(pr.get("number") or 0)
        pr = self._pr(repo, n)
        if str((pr.get("head") or {}).get("sha", "")).lower() != sha:
            raise dp.DevPrRefused("forge PR head does not match the pushed sha")
        out = dp.submit(item_id, actor=it["author"], pr=n, head_sha=sha, decision_id=decision_id,
                        env=self.env, now=self.now)
        _log({"id": item_id, "step": "open", "actor": it["author"], "repo": repo, "pr": n, "branch": br,
              "head_sha": sha}, self.now)
        return {"id": item_id, "pr": n, "head_sha": sha, "url": pr.get("html_url", ""), "state": out["state"]}

    def review(self, item_id: str, *, actor: str, verdict: str, note: str = "") -> dict[str, Any]:
        it = dp._item(item_id)
        repo = self._require(it)
        if not it.get("pr"):
            raise dp.DevPrRefused("item has no PR")
        head = str((self._pr(repo, it["pr"]).get("head") or {}).get("sha", "")).lower()
        out = dp.review(item_id, actor=actor, verdict=verdict, head_sha=head, note=note, env=self.env, now=self.now)
        word = "APPROVE" if verdict == "approve" else "CHANGES REQUESTED"
        text = (f"**[dev-pr review] {word}** by `{out['reviewer']}` on `{head}` (item `{item_id}`, "
                f"author `{out['author']}`).\n\n{str(note)[:dp.MAX_NOTE]}")
        c = self._api("POST", f"/repos/{repo}/issues/{int(it['pr'])}/comments", {"body": text}) or {}
        _log({"id": item_id, "step": "review", "actor": out["reviewer"], "repo": repo, "pr": it["pr"],
              "verdict": verdict, "head_sha": head, "comment_id": c.get("id")}, self.now)
        return {"id": item_id, "verdict": verdict, "head_sha": head, "comment_id": c.get("id"), "state": out["state"]}

    def merge(self, item_id: str, *, actor: str, method: str = "squash") -> dict[str, Any]:
        dp._pm_only(actor, "merge", self.env)
        it = dp._item(item_id)
        repo = self._require(it)
        if method not in ("squash", "merge", "rebase"):
            raise dp.DevPrRefused("merge method must be squash, merge or rebase")
        if not it.get("pr"):
            raise dp.DevPrRefused("item has no PR")
        pr = self._pr(repo, it["pr"])
        if pr.get("state") != "open":
            raise dp.DevPrRefused(f"PR is {pr.get('state')!r}, not open")
        head = str((pr.get("head") or {}).get("sha", "")).lower()
        ci = self.ci_state(repo, head)
        g = dp.merge_gate(item_id, actor=actor, head_sha=head, ci=ci, env=self.env, now=self.now)
        if g["verdict"] != "allow":
            _log({"id": item_id, "step": "merge", "actor": actor, "repo": repo, "pr": it["pr"], "verdict": "deny",
                  "reasons": g["reasons"]}, self.now)
            return {**g, "merged": False}
        res = self._api("PUT", f"/repos/{repo}/pulls/{int(it['pr'])}/merge",
                        {"sha": g["head_sha"], "merge_method": method}) or {}
        if not res.get("merged"):
            raise ForgeError("forge did not merge")
        merged = dp._sha(str(res.get("sha", "")), "merged sha")
        dp.record_merged(item_id, actor=actor, gate_id=g["gate_id"], merged_sha=merged, env=self.env, now=self.now)
        _log({"id": item_id, "step": "merge", "actor": actor, "repo": repo, "pr": it["pr"], "verdict": "allow",
              "gate_id": g["gate_id"], "head_sha": g["head_sha"], "merged_sha": merged}, self.now)
        return {**g, "merged": True, "merged_sha": merged}


# ------------------------------------------------------------------ CLI


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m chip.forge_driver", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("open")
    o.add_argument("item")
    o.add_argument("--actor", required=True)
    o.add_argument("--worktree", required=True)
    o.add_argument("--branch", required=True)
    o.add_argument("--title", required=True)
    o.add_argument("--body", default="")
    o.add_argument("--decision-id", required=True)
    o.add_argument("--base", default="main")
    r = sub.add_parser("review")
    r.add_argument("item")
    r.add_argument("--actor", required=True)
    r.add_argument("--verdict", required=True, choices=("approve", "changes"))
    r.add_argument("--note", default="")
    m = sub.add_parser("merge")
    m.add_argument("item")
    m.add_argument("--actor", required=True)
    m.add_argument("--method", default="squash", choices=("squash", "merge", "rebase"))
    c = sub.add_parser("ci")
    c.add_argument("item")
    return p


def main(argv: Sequence[str] | None = None, *, driver: Driver | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else EXIT_USAGE
    d = driver or Driver()
    try:
        if args.cmd == "open":
            out: Any = d.open(args.item, actor=args.actor, worktree=args.worktree, branch=args.branch,
                              title=args.title, body=args.body, decision_id=args.decision_id, base=args.base)
        elif args.cmd == "review":
            out = d.review(args.item, actor=args.actor, verdict=args.verdict, note=args.note)
        elif args.cmd == "merge":
            out = d.merge(args.item, actor=args.actor, method=args.method)
        else:
            it = dp._item(args.item)
            repo = d._require(it)
            head = str((d._pr(repo, it["pr"]).get("head") or {}).get("sha", "")) if it.get("pr") else ""
            out = {"id": args.item, "head_sha": head, "ci": d.ci_state(repo, head) if head else "none"}
    except dp.DevPrRefused as exc:
        print(json.dumps({"refused": redact(str(exc), d.tok)}))
        return EXIT_REFUSED
    except ForgeError as exc:
        print(json.dumps({"forge_error": redact(str(exc), d.tok)}))
        return EXIT_FORGE
    print(json.dumps(out, ensure_ascii=False))
    if isinstance(out, dict) and out.get("verdict") == "deny":
        return EXIT_REFUSED
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
