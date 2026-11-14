"""Forge driver for the dev-bot PR pipeline: push, open PR, post review, merge pinned to the gated sha.

``chip.dev_pr`` holds the rules and the ledger but never talks to a forge. This module is the
bounded driver that does the forge calls, and it asks ``chip.dev_pr`` before (and records through it
after) every step. It is repo-locked: the item's repo must be on the ``CHIP_DEVPR_REPOS`` allowlist
at the time of the call, and every forge path is built from that repo, never from caller input.

Steps (all fail closed; nothing is pushed or opened if a precheck refuses):

* ``checkout`` (the assigned author, before the job): clone the item repo's base branch into the
  item's fixed worktree ``<data dir>/devpr/<item>/wt`` (path built from the item id, never caller
  input) and remove the clone's remote, so the PM-authorized job that makes the change has the
  code but no credentials and nothing to push to. Refused if the worktree already exists.
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

Auth, two modes (both fail closed, nothing configured = every step refused before any network call):

* **GitHub App mode** (preferred): two distinct apps, one per role. ``open``/``merge``/``ci`` use the
  *dev* app (``CHIP_FORGE_DEV_APP_ID`` / ``_INSTALLATION_ID`` / ``_KEY_FILE``); ``review`` uses the
  *PM* app (``CHIP_FORGE_PM_APP_*``). Each step mints its own short-lived installation token (RS256
  JWT, ``iat`` 60s back, ``exp`` 9 min, ``iss`` = app id -> ``POST /app/installations/{id}/access_tokens``
  scoped to the item repo and the role's permissions) and revokes it (``DELETE /installation/token``)
  when the step ends. In this mode ``review`` is a real forge review (APPROVE / REQUEST_CHANGES on the
  head sha) and author != approver holds at the forge too: the two apps must differ (app id,
  installation, key file), the reviewer app's bot login may not be the PR author, and a PR reused by
  ``open`` must have been opened by the dev app. Once any app var is set, a half-configured app or a
  missing role refuses; there is no fallback to a static token. Tokens are never minted inside a job
  (the key files belong to the forge step only; ``job_policy.job_env`` strips ``CHIP_FORGE_*``).
  Needs ``PyJWT[crypto]`` (``pip install 'chip-chat[forge]'``).
* **Static token** (legacy): ``CHIP_FORGE_TOKEN`` (else ``GITHUB_TOKEN`` / ``GH_TOKEN``). One identity
  for every step, so ``review`` stays a PR comment (see above).

Tokens, JWTs and key material are never printed, logged or put on a command line (git gets the token
via ``GIT_CONFIG_*`` env), and any forge/git output is redacted before it can surface.

Every call appends a row (no token, no bodies) to ``<data dir>/policy/forge_driver.jsonl``.

* ``fixes`` (PM, read-only): the PM summary's "Fixes CC made" forge read (``chip.pm_fixes`` rules)
  for one allowlisted repo and a time window, with a read-only PM app token; no item, no writes.

CLI: ``python -m chip.forge_driver checkout|open|review|merge|ci|fixes`` (JSON out; exit 0 ok, 2 refused/deny,
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
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

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

# GitHub App mode: one app per role; steps map to roles (author = dev app, approver = PM app).
ROLES = ("dev", "pm")
STEP_ROLE = {"checkout": "dev", "open": "dev", "merge": "dev", "ci": "dev", "review": "pm", "fixes": "pm"}
WORKTREES_DIR = "devpr"
APP_PERMS = {
    "dev": {"contents": "write", "pull_requests": "write", "checks": "read", "statuses": "read"},
    "pm": {"pull_requests": "write", "contents": "read", "checks": "read", "statuses": "read"},
}
# ``fixes`` (PM summary "Fixes CC made" read) mints a READ-ONLY PM token. If the app has no
# ``issues`` permission the mint answers 422; it is retried once without it (PRs still listed).
FIXES_PERMS = ({"contents": "read", "pull_requests": "read", "issues": "read"},
               {"contents": "read", "pull_requests": "read"})
FIXES_MAX_DAYS = 8
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/ -]{0,49}$")
# Closing keywords GitHub honors on a merge commit message (auto-closes the linked issue).
_CLOSING = re.compile(
    r"\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s*[:#]?\s*#(\d{1,6})\b", re.IGNORECASE,
)


def closing_refs(pr: Mapping[str, Any]) -> list[int]:
    """Issue numbers a PR's title/body declare with a closing keyword, first-seen order.

    Only keyword+``#N`` pairs count ("Refs #N" never closes). Built from the title and body
    only — never free-form text — so the generated merge commit message is fixed lines.
    """
    seen: dict[int, None] = {}
    for field in ("title", "body"):
        text = pr.get(field)
        if not isinstance(text, str):
            continue
        for m in _CLOSING.finditer(text[:MAX_BODY]):
            seen.setdefault(int(m.group(1)), None)
    return list(seen)
JWT_BACKDATE, JWT_TTL = 60, 540
# A freshly minted installation token can get "Repository not found" (HTTP 404) from the git
# endpoints for a few seconds while the API already accepts it (seen live: the first clone right
# after the mint failed, the same token cloned fine 3 s later). Network git calls retry ONLY that
# answer, with these delays (seconds). Any other git failure fails at once.
GIT_RETRY_DELAYS = (2, 4, 8)
_GIT_NOT_FOUND = re.compile(r"repository '[^']*' not found|Repository not found|returned error: 404|HTTP 404",
                            re.IGNORECASE)  # GitHub caps exp at 10 min; stay under it with clock-skew room


def app_env_names(role: str) -> tuple[str, str, str]:
    r = role.upper()
    return (f"CHIP_FORGE_{r}_APP_ID", f"CHIP_FORGE_{r}_APP_INSTALLATION_ID", f"CHIP_FORGE_{r}_APP_KEY_FILE")

# http(method, url, body_or_None, headers) -> (status, parsed_json_or_None)
Http = Callable[[str, str, Any, Mapping[str, str]], "tuple[int, Any]"]
# git(argv, cwd, env) -> (returncode, combined_output)
Git = Callable[[Sequence[str], str, Mapping[str, str]], "tuple[int, str]"]


class ForgeError(RuntimeError):
    """The forge or git failed (message is already redacted)."""


@dataclass(frozen=True)
class AppCfg:
    role: str
    app_id: str
    installation_id: str
    key_file: str


def app_config(role: str, env: Mapping[str, str] | None = None) -> AppCfg | None:
    """The role's app config, ``None`` when none of its vars are set; refuses a partial or malformed one."""
    e = jp._env(env)
    names = app_env_names(role)
    vals = [str(e.get(n, "") or "").strip() for n in names]
    if not any(vals):
        return None
    if not all(vals):
        raise dp.DevPrRefused(f"{role} app partially configured (need all of {', '.join(names)})")
    if not (vals[0].isdigit() and vals[1].isdigit()):
        raise dp.DevPrRefused(f"{role} app id / installation id must be numeric")
    return AppCfg(role, vals[0], vals[1], vals[2])


def app_configs(env: Mapping[str, str] | None = None) -> dict[str, AppCfg | None]:
    """Both roles' configs; when both are set they must be distinct identities (author != approver)."""
    cfgs = {r: app_config(r, env) for r in ROLES}
    dev, pm = cfgs["dev"], cfgs["pm"]
    if dev and pm and (dev.app_id == pm.app_id or dev.installation_id == pm.installation_id
                       or os.path.realpath(dev.key_file) == os.path.realpath(pm.key_file)):
        raise dp.DevPrRefused("dev and PM apps must be distinct (app id, installation and key file)")
    return cfgs


def app_jwt(cfg: AppCfg, now_ts: float) -> str:
    """RS256 app JWT (iat backdated 60s, exp 9 min, iss = app id). Never logged; key bytes never surface."""
    try:
        import jwt  # PyJWT[crypto], optional extra "forge"
    except ImportError:
        raise dp.DevPrRefused("GitHub App auth needs PyJWT[crypto] (pip install 'chip-chat[forge]')") from None
    try:
        key = Path(cfg.key_file).read_bytes()
    except OSError:
        raise dp.DevPrRefused(f"{cfg.role} app key file is not readable") from None
    now = int(now_ts)
    try:
        return jwt.encode({"iat": now - JWT_BACKDATE, "exp": now + JWT_TTL, "iss": cfg.app_id}, key,
                          algorithm="RS256")
    except Exception:  # noqa: BLE001 - never echo key parsing errors (they can quote key bytes)
        raise dp.DevPrRefused(f"{cfg.role} app key is not a usable RSA private key") from None


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


def redact(text: str, tok: "str | Sequence[str]") -> str:
    out = str(text or "")
    toks = [tok] if isinstance(tok, str) else list(tok)
    for t in toks:
        for s in _secrets(t):
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


def worktree_path(item_id: str) -> Path:
    """The item's fixed worktree: ``<data dir>/devpr/<item>/wt`` (item id validated)."""
    if not dp._ITEM.match(str(item_id or "")):
        raise dp.DevPrRefused("malformed item id")
    return jp.data_dir() / WORKTREES_DIR / item_id / "wt"


class Driver:
    def __init__(self, *, env: Mapping[str, str] | None = None, http: Http | None = None,
                 git: Git | None = None, now: datetime | None = None,
                 clock: Callable[[], float] | None = None,
                 sleep: Callable[[float], None] | None = None) -> None:
        self.env = env
        self.http = http or _urllib_http
        self.git = git or _subprocess_git
        self.now = now
        self.clock = clock or time.time
        self.sleep = sleep or time.sleep
        self.static_tok = token(env)
        self.tok = self.static_tok
        self.auth = "token"
        self.login = ""  # app mode: the bot login of the app the current step runs as
        self._seen: list[str] = [self.static_tok] if self.static_tok else []
        self.revoked: list[bool] = []

    # -------------------------------------------------------------- auth
    def _redact(self, text: str) -> str:
        return redact(text, self._seen)

    def _auth(self, role: str) -> AppCfg | None:
        """The app config this role's step must run as, ``None`` for the static token; refuses otherwise."""
        cfgs = app_configs(self.env)
        if any(cfgs.values()):
            cfg = cfgs.get(role)
            if cfg is None:
                raise dp.DevPrRefused(f"GitHub App mode: the {role} app is not configured "
                                      f"({', '.join(app_env_names(role))}); no static-token fallback")
            return cfg
        if not self.static_tok:
            raise dp.DevPrRefused("no forge token in env (" + "/".join(TOKEN_ENVS)
                                  + ") and no GitHub App config (CHIP_FORGE_DEV_APP_* / CHIP_FORGE_PM_APP_*)")
        return None

    def _raw(self, method: str, path: str, body: Any, bearer: str) -> tuple[int, Any]:
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {bearer}",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "chip-forge-driver"}
        try:
            return self.http(method, API + path, body, headers)
        except Exception as exc:  # noqa: BLE001 - surface a redacted error only
            raise ForgeError(self._redact(f"{method} {path.split('?')[0]}: {exc}")) from None

    def _mint(self, cfg: AppCfg, repo: str, perms: Mapping[str, str] | None = None) -> None:
        if jp.in_job_marker(self.env):
            raise dp.DevPrRefused("GitHub App tokens are never minted inside a job (forge step only)")
        j = app_jwt(cfg, self.clock())
        self._seen.append(j)
        status, app = self._raw("GET", "/app", None, j)
        slug = str((app or {}).get("slug", "")) if isinstance(app, dict) else ""
        if status >= 400 or not slug or str((app or {}).get("id", "")) != cfg.app_id:
            raise ForgeError(self._redact(f"{cfg.role} app identity check -> {status}"))
        status, out = self._raw("POST", f"/app/installations/{cfg.installation_id}/access_tokens",
                                {"repositories": [repo.split("/", 1)[1]],
                                 "permissions": dict(perms if perms is not None else APP_PERMS[cfg.role])}, j)
        tok = str((out or {}).get("token", "")) if isinstance(out, dict) else ""
        if status >= 400 or not tok:
            msg = out.get("message", "") if isinstance(out, dict) else ""
            raise ForgeError(self._redact(f"{cfg.role} app token mint -> {status} {msg}"))
        self._seen.append(tok)
        self.tok, self.auth, self.login = tok, f"app:{cfg.role}", f"{slug}[bot]".lower()

    def _revoke(self) -> None:
        ok = False
        try:
            status, _ = self._raw("DELETE", "/installation/token", None, self.tok)
            ok = status in (204, 200)
        except ForgeError:
            ok = False
        self.revoked.append(ok)

    @contextmanager
    def _session(self, role: str, repo: str, perms: Mapping[str, str] | None = None) -> Iterator[None]:
        """Run one step as ``role``: mint a per-step installation token (app mode) and revoke it after."""
        cfg = self._auth(role)
        if cfg is None:
            yield
            return
        self._mint(cfg, repo, perms)
        try:
            yield
        finally:
            self._revoke()
            self.tok, self.auth, self.login = self.static_tok, "token", ""

    # -------------------------------------------------------------- plumbing
    def _require(self, it: Mapping[str, Any], role: str = "dev") -> str:
        dp._common(self.env)
        self._auth(role)
        repo = str(it.get("repo") or "").lower()
        if repo not in dp.allowed_repos(self.env):
            raise dp.DevPrRefused(f"repo {repo!r} not in {dp.REPOS_ENV} allowlist")
        if "/" not in repo:
            raise dp.DevPrRefused("malformed repo")
        return repo

    def _api(self, method: str, path: str, body: Any = None) -> Any:
        status, out = self._raw(method, path, body, self.tok)
        if status >= 400:
            msg = out.get("message", "") if isinstance(out, dict) else ""
            raise ForgeError(self._redact(f"{method} {path.split('?')[0]} -> {status} {msg}"))
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
        base = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "CHIP_FORGE_"))}
        base.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1",
                     "GIT_CONFIG_KEY_0": f"http.{GIT_HOST}/.extraheader",
                     "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {auth}"})
        return base

    def _run_git(self, argv: Sequence[str], cwd: str, env: Mapping[str, str] | None = None,
                 *, retry_not_found: bool = False) -> str:
        env = env if env is not None else {k: v for k, v in os.environ.items()
                                           if not k.startswith(("GIT_", "CHIP_FORGE_"))}
        rc, out = self.git(argv, cwd, env)
        if retry_not_found:
            for delay in GIT_RETRY_DELAYS:
                if rc == 0 or not _GIT_NOT_FOUND.search(out or ""):
                    break
                self.sleep(delay)
                rc, out = self.git(argv, cwd, env)
        if rc != 0:
            raise ForgeError(self._redact(f"git {argv[1] if len(argv) > 1 else ''} failed: {out}"))
        return out

    # -------------------------------------------------------------- steps
    def checkout(self, item_id: str, *, actor: str, base: str = "main") -> dict[str, Any]:
        """Clone the item repo's ``base`` into the item's fixed worktree for the change job (no remote left)."""
        it = dp._item(item_id)
        repo = self._require(it, STEP_ROLE["checkout"])
        if str(actor or "").lower() != it["author"]:
            raise dp.DevPrRefused("only the assigned author may check out the worktree")
        if it["state"] not in ("assigned", "changes_requested"):
            raise dp.DevPrRefused(f"cannot check out in state {it['state']!r}")
        if not _BRANCH.match(str(base or "")) or ".." in str(base):
            raise dp.DevPrRefused("malformed base branch")
        wt = worktree_path(item_id)
        if wt.exists() or wt.is_symlink():
            raise dp.DevPrRefused("worktree already exists for this item (reuse it)")
        wt.parent.mkdir(parents=True, exist_ok=True)
        with self._session(STEP_ROLE["checkout"], repo):
            self._run_git(["git", "-c", "credential.helper=", "clone", "-q", "--no-tags", "--single-branch",
                           "--depth", "1", "--branch", str(base), f"{GIT_HOST}/{repo}.git", str(wt)],
                          str(wt.parent), self._git_env(), retry_not_found=True)
        self._run_git(["git", "-C", str(wt), "remote", "remove", "origin"], str(wt.parent))
        sha = dp._sha(self._run_git(["git", "-C", str(wt), "rev-parse", "HEAD"], str(wt.parent))
                      .strip().splitlines()[-1], "base sha")
        _log({"id": item_id, "step": "checkout", "actor": it["author"], "repo": repo, "base": str(base),
              "base_sha": sha, "worktree": str(wt), "auth": self.auth}, self.now)
        return {"id": item_id, "worktree": str(wt), "base": str(base), "base_sha": sha, "state": it["state"]}

    def open(self, item_id: str, *, actor: str, worktree: str, branch: str, title: str, body: str,
             decision_id: str, base: str = "main") -> dict[str, Any]:
        it = dp._item(item_id)
        repo = self._require(it, STEP_ROLE["open"])
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
        with self._session(STEP_ROLE["open"], repo):
            return self._open(item_id, it, repo, worktree, sha, br, title, body, decision_id, base)

    def _open(self, item_id: str, it: Mapping[str, Any], repo: str, worktree: str, sha: str, br: str,
              title: str, body: str, decision_id: str, base: str) -> dict[str, Any]:
        self._run_git(["git", "-c", "credential.helper=", "push", "--no-verify", f"{GIT_HOST}/{repo}.git",
                       f"{sha}:refs/heads/{br}"], worktree, self._git_env(), retry_not_found=True)
        owner = repo.split("/")[0]
        existing = self._api("GET", f"/repos/{repo}/pulls?state=open&head={owner}:{br}") or []
        if existing:
            pr = existing[0]
        else:
            pr = self._api("POST", f"/repos/{repo}/pulls", {"title": str(title)[:200], "head": br,
                                                            "base": base, "body": str(body or "")[:MAX_BODY]}) or {}
        n = int(pr.get("number") or 0)
        pr = self._pr(repo, n)
        if self.login and str((pr.get("user") or {}).get("login", "")).lower() != self.login:
            raise dp.DevPrRefused("forge PR was not opened by the dev app")
        if str((pr.get("head") or {}).get("sha", "")).lower() != sha:
            raise dp.DevPrRefused("forge PR head does not match the pushed sha")
        out = dp.submit(item_id, actor=it["author"], pr=n, head_sha=sha, decision_id=decision_id,
                        env=self.env, now=self.now)
        _log({"id": item_id, "step": "open", "actor": it["author"], "repo": repo, "pr": n, "branch": br,
              "head_sha": sha, "auth": self.auth}, self.now)
        return {"id": item_id, "pr": n, "head_sha": sha, "url": pr.get("html_url", ""), "state": out["state"]}

    def review(self, item_id: str, *, actor: str, verdict: str, note: str = "") -> dict[str, Any]:
        it = dp._item(item_id)
        repo = self._require(it, STEP_ROLE["review"])
        if not it.get("pr"):
            raise dp.DevPrRefused("item has no PR")
        if verdict not in ("approve", "changes"):
            raise dp.DevPrRefused("verdict must be approve or changes")
        with self._session(STEP_ROLE["review"], repo):
            pr = self._pr(repo, it["pr"])
            head = str((pr.get("head") or {}).get("sha", "")).lower()
            if self.login and str((pr.get("user") or {}).get("login", "")).lower() == self.login:
                raise dp.DevPrRefused("the reviewing app opened this PR (author != approver)")
            out = dp.review(item_id, actor=actor, verdict=verdict, head_sha=head, note=note, env=self.env, now=self.now)
            word = "APPROVE" if verdict == "approve" else "CHANGES REQUESTED"
            text = (f"**[dev-pr review] {word}** by `{out['reviewer']}` on `{head}` (item `{item_id}`, "
                    f"author `{out['author']}`).\n\n{str(note)[:dp.MAX_NOTE]}")
            if self.auth.startswith("app:"):
                # Distinct app identity: a real forge review bound to the head sha.
                event = "APPROVE" if verdict == "approve" else "REQUEST_CHANGES"
                rv = self._api("POST", f"/repos/{repo}/pulls/{int(it['pr'])}/reviews",
                               {"commit_id": head, "event": event, "body": text}) or {}
                rid, kind = rv.get("id"), "review"
            else:
                c = self._api("POST", f"/repos/{repo}/issues/{int(it['pr'])}/comments", {"body": text}) or {}
                rid, kind = c.get("id"), "comment"
            _log({"id": item_id, "step": "review", "actor": out["reviewer"], "repo": repo, "pr": it["pr"],
                  "verdict": verdict, "head_sha": head, f"{kind}_id": rid, "auth": self.auth}, self.now)
        return {"id": item_id, "verdict": verdict, "head_sha": head, f"{kind}_id": rid, "kind": kind,
                "state": out["state"]}

    def fixes(self, repo: str, *, since: str, until: str, label: str = "",
              prefixes: str | None = None) -> dict[str, Any]:
        """Read-only "Fixes CC made" for one allowlisted repo, as the PM app (``chip.pm_fixes`` rules).

        No item, nothing written to the forge. The repo must be on ``CHIP_DEVPR_REPOS``; the token is a
        read-only PM installation token scoped to that repo and revoked when the read ends.
        """
        from chip import pm_fixes  # local: pm_fixes is summary-side and must not import the driver

        dp._common(self.env)
        repo = str(repo or "").strip().lower()
        if "/" not in repo or not dp._REPO.match(repo):
            raise dp.DevPrRefused("malformed repo")
        if repo not in dp.allowed_repos(self.env):
            raise dp.DevPrRefused(f"repo {repo!r} not in {dp.REPOS_ENV} allowlist")
        if not (_ISO.match(str(since)) and _ISO.match(str(until))):
            raise dp.DevPrRefused("since/until must be ISO-8601 timestamps with a zone")
        start = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
        if not (start < end) or (end - start).total_seconds() > FIXES_MAX_DAYS * 86400:
            raise dp.DevPrRefused(f"window must be 0 < until - since <= {FIXES_MAX_DAYS} days")
        if label and not _LABEL.match(label):
            raise dp.DevPrRefused("malformed label")
        fenv = {pm_fixes.REPOS_ENV: repo, pm_fixes.API_ENV: API}
        if label:
            fenv[pm_fixes.LABEL_ENV] = label
        if prefixes is not None:
            pre = [x.strip() for x in str(prefixes).split(",") if x.strip()]
            if any(not _BRANCH.match(x) for x in pre):
                raise dp.DevPrRefused("malformed branch prefix")
            fenv[pm_fixes.PREFIX_ENV] = ",".join(pre)

        def _get(url: str, _headers: Mapping[str, str]) -> tuple[int, Any]:
            if not url.startswith(API + "/repos/" + repo + "/"):
                raise ForgeError("fixes read outside the repo")
            return self._raw("GET", url[len(API):], None, self.tok)

        last: Exception | None = None
        for perms in FIXES_PERMS:
            try:
                with self._session(STEP_ROLE["fixes"], repo, perms):
                    items, errors = pm_fixes.forge_fixes(start, end, fenv, _get)
                    auth = self.auth
                break
            except ForgeError as exc:
                last = exc
                if "mint -> 422" not in str(exc):
                    raise
        else:
            raise last if last else ForgeError("fixes: no token")
        errors = [self._redact(e) for e in errors]
        _log({"step": "fixes", "repo": repo, "since": since, "until": until, "items": len(items),
              "errors": len(errors), "auth": auth}, self.now)
        return {"repo": repo, "items": items, "errors": errors, "auth": auth}

    def merge(self, item_id: str, *, actor: str, method: str = "squash") -> dict[str, Any]:
        dp._pm_only(actor, "merge", self.env)
        it = dp._item(item_id)
        repo = self._require(it, STEP_ROLE["merge"])
        if method not in ("squash", "merge", "rebase"):
            raise dp.DevPrRefused("merge method must be squash, merge or rebase")
        if not it.get("pr"):
            raise dp.DevPrRefused("item has no PR")
        with self._session(STEP_ROLE["merge"], repo):
            return self._merge(item_id, it, repo, actor, method)

    def _merge(self, item_id: str, it: Mapping[str, Any], repo: str, actor: str, method: str) -> dict[str, Any]:
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
        payload: dict[str, Any] = {"sha": g["head_sha"], "merge_method": method}
        closes = closing_refs(pr)
        if closes:
            # A squash/merge commit message that carries the closing keywords is what GitHub
            # uses to close linked issues on merge — the PR body alone is dropped by the
            # forge's default squash message ("PR title"). Pass both explicitly (#refs close).
            payload["commit_title"] = str(pr.get("title") or "")[:250]
            payload["commit_message"] = "\n".join(f"Fixes #{n}" for n in closes)
        res = self._api("PUT", f"/repos/{repo}/pulls/{int(it['pr'])}/merge", payload) or {}
        if not res.get("merged"):
            raise ForgeError("forge did not merge")
        merged = dp._sha(str(res.get("sha", "")), "merged sha")
        dp.record_merged(item_id, actor=actor, gate_id=g["gate_id"], merged_sha=merged, env=self.env, now=self.now)
        _log({"id": item_id, "step": "merge", "actor": actor, "repo": repo, "pr": it["pr"], "verdict": "allow",
              "gate_id": g["gate_id"], "head_sha": g["head_sha"], "merged_sha": merged,
              **({"closes": closes} if closes else {}), "auth": self.auth}, self.now)
        out = {**g, "merged": True, "merged_sha": merged}
        if closes:
            out["closes"] = closes
        return out


# ------------------------------------------------------------------ CLI


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m chip.forge_driver", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("checkout")
    k.add_argument("item")
    k.add_argument("--actor", required=True)
    k.add_argument("--base", default="main")
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
    f = sub.add_parser("fixes")
    f.add_argument("--repo", required=True)
    f.add_argument("--since", required=True)
    f.add_argument("--until", required=True)
    f.add_argument("--label", default="")
    f.add_argument("--prefixes", default=None)
    return p


def main(argv: Sequence[str] | None = None, *, driver: Driver | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else EXIT_USAGE
    d = driver or Driver()
    try:
        if args.cmd == "checkout":
            out: Any = d.checkout(args.item, actor=args.actor, base=args.base)
        elif args.cmd == "open":
            out = d.open(args.item, actor=args.actor, worktree=args.worktree, branch=args.branch,
                              title=args.title, body=args.body, decision_id=args.decision_id, base=args.base)
        elif args.cmd == "review":
            out = d.review(args.item, actor=args.actor, verdict=args.verdict, note=args.note)
        elif args.cmd == "merge":
            out = d.merge(args.item, actor=args.actor, method=args.method)
        elif args.cmd == "fixes":
            out = d.fixes(args.repo, since=args.since, until=args.until, label=args.label, prefixes=args.prefixes)
        else:
            it = dp._item(args.item)
            repo = d._require(it, STEP_ROLE["ci"])
            with d._session(STEP_ROLE["ci"], repo):
                head = str((d._pr(repo, it["pr"]).get("head") or {}).get("sha", "")) if it.get("pr") else ""
                out = {"id": args.item, "head_sha": head, "ci": d.ci_state(repo, head) if head else "none"}
    except dp.DevPrRefused as exc:
        print(json.dumps({"refused": d._redact(str(exc))}))
        return EXIT_REFUSED
    except ForgeError as exc:
        print(json.dumps({"forge_error": d._redact(str(exc))}))
        return EXIT_FORGE
    print(json.dumps(out, ensure_ascii=False))
    if isinstance(out, dict) and out.get("verdict") == "deny":
        return EXIT_REFUSED
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
