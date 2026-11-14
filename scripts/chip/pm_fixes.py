"""PM daily summary section: "Fixes CC made".

:func:`collect` lists the fixes Chip Chat itself made in one time window (the summary's local day,
i.e. a 24h window), from three sources:

* **Job receipts that were fixes** — ok executor receipts (:mod:`chip.policy_exec`) whose job-policy
  audit row has a ``context`` starting with ``alert:`` (a remediation PM queued for an alert), or a
  ``job_type`` listed in ``CHIP_PM_FIXES_JOB_TYPES`` (comma list, default empty).
* **Dev-bot PRs merged** — ``merged`` rows in the :mod:`chip.dev_pr` ledger.
* **Forge (optional, off by default)** — when ``CHIP_PM_FIXES_REPOS`` lists ``owner/repo`` names,
  closed issues carrying the fix label (``CHIP_PM_FIXES_LABEL``, default ``cc-fix``) and merged PRs
  that carry that label, start with a fix branch prefix (``CHIP_PM_FIXES_BRANCH_PREFIXES``, default
  ``cc-fix/,fix/``) or say ``Fixes|Closes|Resolves #N`` for one of those issues. GitHub REST API at
  ``CHIP_PM_FIXES_API`` (default ``https://api.github.com``). Auth is optional:
  ``CHIP_PM_FIXES_TOKEN_FROM`` may name another env var whose name contains ``TOKEN``; the value is
  only sent as a header and never logged or returned. Forge errors never fail the summary; they show
  up as a short ``errors`` line (status code / exception type only).
* **Forge via the PM app (optional)** — ``CHIP_PM_FIXES_VIA=forge`` reads the repos in
  ``CHIP_PM_FIXES_FORGE_REPOS`` (comma list; empty = every ``CHIP_PM_FIXES_REPOS`` repo) through the
  forge step runner (``chip.forge_steps`` ``CHIP_FORGE_STEP_CMD``) as the read-only driver step
  ``fixes``, so a private repo is read with the PM GitHub App and this process never holds a key or
  token. Other repos keep the direct read above. Refused inside a job; runner errors are error lines.

Read-only: nothing here writes, posts or changes anything.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from chip import dev_pr, job_policy, policy_exec

REPOS_ENV = "CHIP_PM_FIXES_REPOS"
LABEL_ENV = "CHIP_PM_FIXES_LABEL"
PREFIX_ENV = "CHIP_PM_FIXES_BRANCH_PREFIXES"
JOB_TYPES_ENV = "CHIP_PM_FIXES_JOB_TYPES"
API_ENV = "CHIP_PM_FIXES_API"
TOKEN_FROM_ENV = "CHIP_PM_FIXES_TOKEN_FROM"
VIA_ENV = "CHIP_PM_FIXES_VIA"
FORGE_REPOS_ENV = "CHIP_PM_FIXES_FORGE_REPOS"
FORGE_STEP = "fixes"
DEFAULT_LABEL = "cc-fix"
DEFAULT_PREFIXES = ("cc-fix/", "fix/")
DEFAULT_API = "https://api.github.com"
TIMEOUT_S = 10
PER_PAGE = 50
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CLOSES = re.compile(r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#(\d+)\b", re.IGNORECASE)

# http(url, headers) -> (status, parsed JSON or None)
Http = Callable[[str, Mapping[str, str]], tuple[int, Any]]
# runner(step, args) -> object with .rc / .out / .error (chip.forge_steps.StepResult)
Runner = Callable[[str, list], Any]


def _env(env: Mapping[str, str] | None) -> dict[str, str]:
    return dict(os.environ if env is None else env)


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


def _parse(ts: Any) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _within(ts: Any, start: datetime, end: datetime) -> bool:
    dt = _parse(ts)
    return dt is not None and dt.tzinfo is not None and start <= dt < end


def _clip(v: Any, n: int) -> str:
    s = " ".join(str(v or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def repos(env: Mapping[str, str] | None = None) -> list[str]:
    return [r for r in _csv(_env(env).get(REPOS_ENV)) if _REPO.match(r)]


def via_forge(env: Mapping[str, str] | None = None) -> list[str]:
    """Repos read through the forge runner (PM app); ``[]`` unless ``CHIP_PM_FIXES_VIA=forge``."""
    e = _env(env)
    if str(e.get(VIA_ENV) or "").strip().lower() != "forge":
        return []
    names = repos(e)
    only = {r.lower() for r in _csv(e.get(FORGE_REPOS_ENV)) if _REPO.match(r)}
    return [r for r in names if not only or r.lower() in only]


def _token(e: Mapping[str, str]) -> str:
    name = str(e.get(TOKEN_FROM_ENV) or "").strip()
    if not name or "TOKEN" not in name.upper() or name == TOKEN_FROM_ENV:
        return ""
    return str(e.get(name) or "").strip()


def _urllib_http(url: str, headers: Mapping[str, str]) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 - https API from config
            return resp.status, json.loads(resp.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        return exc.code, None


# ------------------------------------------------------------------ local sources


def receipt_fixes(start: datetime, end: datetime, env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Ok executor receipts in the window that were fixes (alert remediations or listed job types)."""
    types = set(_csv(_env(env).get(JOB_TYPES_ENV)))
    audit: dict[str, dict[str, Any]] = {}
    for row in job_policy._jsonl(job_policy.audit_path()):
        jid = str(row.get("job_id") or "")
        if jid and row.get("job_type"):
            audit[jid] = row  # latest audit row for the job wins
    out: list[dict[str, Any]] = []
    for r in job_policy._jsonl(policy_exec.receipts_path()):
        if r.get("status") != "ok" or not _within(r.get("ts"), start, end):
            continue
        a = audit.get(str(r.get("job_id") or ""), {})
        ctx = str(a.get("context") or "")
        if not (ctx.startswith("alert:") or (a.get("job_type") and a.get("job_type") in types)):
            continue
        out.append(
            {
                "kind": "receipt",
                "at": r.get("ts"),
                "job_id": r.get("job_id"),
                "decision_id": r.get("decision_id"),
                "job_type": a.get("job_type"),
                "action": a.get("action"),
                "target": a.get("target"),
                "context": _clip(ctx, 80),
                "sudo": bool(r.get("sudo")),
            }
        )
    return out


def devpr_fixes(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Dev-bot PRs the dev_pr ledger recorded as merged in the window."""
    out: list[dict[str, Any]] = []
    for it in dev_pr.items(state="merged"):
        at = next((h.get("ts") for h in reversed(it.get("history") or []) if h.get("event") == "merged"), None)
        if not _within(at, start, end):
            continue
        repo, pr = it.get("repo"), it.get("pr")
        url = f"https://github.com/{repo}/pull/{pr}" if repo and pr else ""
        out.append(
            {
                "kind": "devpr",
                "at": at,
                "id": it.get("id"),
                "ticket": it.get("ticket"),
                "repo": repo,
                "url": url,
                "author": it.get("author"),
                "reviewer": it.get("approved_by") or it.get("reviewer"),
                "merged_sha": str(it.get("merged_sha") or "")[:12],
            }
        )
    return out


# ------------------------------------------------------------------ forge source


def forge_fixes(start: datetime, end: datetime, env: Mapping[str, str] | None = None,
                http: Http | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """(fix issues closed + fix PRs merged in the window, error lines) for CHIP_PM_FIXES_REPOS."""
    e = _env(env)
    via = via_forge(e)
    names = [r for r in repos(e) if r not in via]
    if not names:
        return [], []
    get = http or _urllib_http
    label = str(e.get(LABEL_ENV) or DEFAULT_LABEL).strip() or DEFAULT_LABEL
    prefixes = tuple(_csv(e.get(PREFIX_ENV))) if e.get(PREFIX_ENV) is not None else DEFAULT_PREFIXES
    api = str(e.get(API_ENV) or DEFAULT_API).rstrip("/")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "chip-pm-summary"}
    tok = _token(e)
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    since = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    items: list[dict[str, Any]] = []
    errors: list[str] = []

    def _get(repo: str, path: str, query: Mapping[str, str]) -> list[dict[str, Any]] | None:
        url = f"{api}/repos/{repo}/{path}?{urllib.parse.urlencode(query)}"
        try:
            status, body = get(url, headers)
        except Exception as exc:  # network errors must not fail the summary
            errors.append(f"{repo} {path}: {type(exc).__name__}")
            return None
        if status != 200 or not isinstance(body, list):
            errors.append(f"{repo} {path}: HTTP {status}")
            return None
        return [x for x in body if isinstance(x, dict)]

    for repo in names:
        fix_issues: set[int] = set()
        issues = _get(repo, "issues", {"state": "closed", "labels": label, "since": since, "per_page": str(PER_PAGE)})
        for i in issues or []:
            if "pull_request" in i:  # the issues API also lists PRs
                continue
            n = i.get("number")
            if isinstance(n, int):
                fix_issues.add(n)
            if _within(i.get("closed_at"), start, end):
                items.append({"kind": "issue", "at": i.get("closed_at"), "repo": repo, "number": n,
                              "url": str(i.get("html_url") or ""), "title": _clip(i.get("title"), 90)})
        pulls = _get(repo, "pulls", {"state": "closed", "sort": "updated", "direction": "desc", "per_page": str(PER_PAGE)})
        for p in pulls or []:
            if not _within(p.get("merged_at"), start, end):
                continue
            ref = str((p.get("head") or {}).get("ref") or "")
            labels = {str((lb or {}).get("name") or "") for lb in p.get("labels") or [] if isinstance(lb, dict)}
            text = f"{p.get('title') or ''}\n{p.get('body') or ''}"
            closes = {int(m) for m in _CLOSES.findall(text)}
            why = ("label" if label in labels else "branch" if prefixes and ref.startswith(prefixes)
                   else "closes" if closes & fix_issues else "")
            if not why:
                continue
            items.append({"kind": "pr", "at": p.get("merged_at"), "repo": repo, "number": p.get("number"),
                          "url": str(p.get("html_url") or ""), "title": _clip(p.get("title"), 90),
                          "sha": str(p.get("merge_commit_sha") or "")[:7], "why": why})
    return items, errors


_ITEM_KEYS = ("kind", "at", "repo", "number", "url", "title", "sha", "why")


def forge_runner_fixes(start: datetime, end: datetime, env: Mapping[str, str] | None = None,
                       runner: Runner | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """(items, error lines) for the ``CHIP_PM_FIXES_VIA=forge`` repos, via the forge step ``fixes``."""
    e = _env(env)
    names = via_forge(e)
    if not names:
        return [], []
    if job_policy.in_job_marker(e):
        return [], [f"{r} via forge: refused inside a job" for r in names]
    if runner is None:
        from chip import forge_steps  # local: keeps the summary import light

        try:
            runner = forge_steps.default_runner(e)
        except forge_steps.StepRefused:
            return [], [f"{r} via forge: no runner ({forge_steps.CMD_ENV})" for r in names]
    label = str(e.get(LABEL_ENV) or DEFAULT_LABEL).strip() or DEFAULT_LABEL
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    since, until = start.astimezone(timezone.utc).strftime(fmt), end.astimezone(timezone.utc).strftime(fmt)
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for repo in names:
        args = ["--repo", repo, "--since", since, "--until", until, "--label", label]
        if e.get(PREFIX_ENV) is not None:
            args += ["--prefixes", ",".join(_csv(e.get(PREFIX_ENV)))]
        try:
            res = runner(FORGE_STEP, args)
        except Exception as exc:  # noqa: BLE001 - a runner failure must not fail the summary
            errors.append(f"{repo} via forge: {type(exc).__name__}")
            continue
        out = getattr(res, "out", None) or {}
        rc = getattr(res, "rc", None)
        if rc != 0 or not isinstance(out, dict) or "refused" in out or "forge_error" in out:
            why = (out.get("refused") or out.get("forge_error")) if isinstance(out, dict) else ""
            errors.append(f"{repo} via forge: rc {rc}" + (f" {_clip(why, 60)}" if why else ""))
            continue
        for x in out.get("errors") or []:
            errors.append(f"via forge: {_clip(x, 60)}")
        for i in out.get("items") or []:
            if not isinstance(i, dict) or i.get("kind") not in ("issue", "pr") or str(i.get("repo")) != repo:
                continue
            if not _within(i.get("at"), start, end):
                continue
            row = {k: i.get(k) for k in _ITEM_KEYS if k in i}
            row["title"] = _clip(row.get("title"), 90)
            row["via"] = "forge"
            items.append(row)
    return items, errors


# ------------------------------------------------------------------ collect / render


def collect(start: datetime, end: datetime, *, env: Mapping[str, str] | None = None,
            http: Http | None = None, runner: Runner | None = None) -> dict[str, Any]:
    """All CC fixes in ``[start, end)`` (aware datetimes): ``{"items": [...], "errors": [...]}``."""
    items = receipt_fixes(start, end, env) + devpr_fixes(start, end)
    forge, errors = forge_fixes(start, end, env, http)
    vf, verr = forge_runner_fixes(start, end, env, runner)
    forge, errors = forge + vf, errors + verr
    seen = {i.get("url") for i in items if i.get("url")}
    for f in forge:
        if f.get("url") and f["url"] in seen:
            continue
        seen.add(f.get("url"))
        items.append(f)
    items.sort(key=lambda i: str(i.get("at") or ""))
    return {"items": items, "errors": errors}


def lines(fixes: Mapping[str, Any]) -> list[str]:
    """One short line per fix (summary text), plus one line for forge errors if any."""
    out: list[str] = []
    for i in fixes.get("items") or []:
        k = i.get("kind")
        if k == "pr":
            out.append(f"PR merged {i.get('url')} `{i.get('sha')}` — {i.get('title')}")
        elif k == "issue":
            out.append(f"cc-fix closed {i.get('url')} — {i.get('title')}")
        elif k == "devpr":
            out.append(f"dev-bot PR merged {i.get('url') or i.get('id')} ({i.get('author')} → {i.get('reviewer')})"
                       f" `{i.get('merged_sha')}` · {i.get('ticket')}")
        elif k == "receipt":
            out.append(f"job `{i.get('job_id')}` {i.get('job_type')}: {i.get('action')} on {i.get('target') or '-'}"
                       f" ok (receipt {i.get('decision_id')}) · {i.get('context')}")
    if fixes.get("errors"):
        out.append("forge unavailable: " + "; ".join(_clip(x, 60) for x in fixes["errors"][:3]))
    return out


__all__ = ["collect", "devpr_fixes", "forge_fixes", "forge_runner_fixes", "lines", "receipt_fixes", "repos",
           "via_forge"]
