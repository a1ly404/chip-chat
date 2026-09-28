"""Morning standup collectors for GymLog (+ optional Homelab) — RAZ-330.

Read-only probes; secrets from env only. No razzorstack mutations.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

USER_AGENT = "chip-chat-standup-collectors/1.0"
LINEAR_API_URL = "https://api.linear.app/graphql"
DEFAULT_GYM_ORIGIN = "https://gym.razzormail.com"
DEFAULT_GITHUB_REPO = "a1ly404/chip-chat"
DEFAULT_GHA_WORKFLOW = "gym-app-ci.yml"


@dataclass(frozen=True)
class CollectorResult:
    name: str
    ok: bool
    summary: str
    detail: str = ""


@dataclass
class GymlogCollectorBundle:
    gha: CollectorResult
    site: CollectorResult
    linear: CollectorResult
    live_e2e: CollectorResult
    collectors_live: bool = False
    verified_notes: list[str] = field(default_factory=list)


HttpGet = Callable[[str, dict[str, str], float], tuple[int, str]]


def _default_http_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(65536).decode("utf-8", errors="replace")
            return int(resp.status), body
    except urllib.error.HTTPError as exc:
        body = exc.read(65536).decode("utf-8", errors="replace") if exc.fp else ""
        return int(exc.code), body


def _env(environ: dict[str, str] | None, key: str, default: str = "") -> str:
    env = environ if environ is not None else os.environ
    return (env.get(key) or default).strip()


def collect_gha_workflow(
    environ: dict[str, str] | None = None,
    *,
    http_get: HttpGet | None = None,
) -> CollectorResult:
    """Last gym-app CI workflow run on the configured repo."""
    fetch = http_get or _default_http_get
    token = _env(environ, "GITHUB_TOKEN") or _env(environ, "CHIP_STANDUP_GITHUB_TOKEN")
    repo = _env(environ, "CHIP_STANDUP_GITHUB_REPO", DEFAULT_GITHUB_REPO)
    workflow = _env(environ, "CHIP_STANDUP_GHA_WORKFLOW", DEFAULT_GHA_WORKFLOW)
    if not token:
        return CollectorResult("gha", False, "no token", "GITHUB_TOKEN unset")
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?per_page=1"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        status, body = fetch(url, headers, 15.0)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return CollectorResult("gha", False, "unreachable", str(exc)[:200])
    if status != 200:
        return CollectorResult("gha", False, f"http {status}", body[:120])
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return CollectorResult("gha", False, "bad json", body[:80])
    runs = data.get("workflow_runs") if isinstance(data, dict) else None
    if not isinstance(runs, list) or not runs:
        return CollectorResult("gha", False, "no runs", "empty workflow_runs")
    run = runs[0]
    conclusion = str(run.get("conclusion") or run.get("status") or "unknown")
    name = str(run.get("name") or workflow)
    html = str(run.get("html_url") or "")
    ok = conclusion in ("success", "skipped")
    return CollectorResult(
        "gha",
        ok,
        f"{name} {conclusion}",
        html[:200],
    )


def collect_gym_site(
    environ: dict[str, str] | None = None,
    *,
    http_get: HttpGet | None = None,
) -> CollectorResult:
    """HEAD/GET gym origin; follow redirects (303 acceptable)."""
    fetch = http_get or _default_http_get
    origin = _env(environ, "GYMLOG_ORIGIN", DEFAULT_GYM_ORIGIN).rstrip("/")
    url = origin + "/"
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,*/*"}
    try:
        status, _body = fetch(url, headers, 15.0)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return CollectorResult("site", False, "unreachable", str(exc)[:200])
    ok = 200 <= status < 400
    return CollectorResult("site", ok, f"http {status}", origin)


def _linear_gql(
    api_key: str,
    query: str,
    variables: dict[str, Any],
    *,
    http_get: HttpGet | None = None,
) -> dict[str, Any]:
    fetch = http_get or _default_http_get
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        LINEAR_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": api_key,
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    data = json.loads(body)
    if not isinstance(data, dict):
        return {}
    if data.get("errors"):
        raise RuntimeError(str(data["errors"])[:300])
    inner = data.get("data")
    return inner if isinstance(inner, dict) else {}


def collect_linear_gymlog_queue(
    environ: dict[str, str] | None = None,
    *,
    http_get: HttpGet | None = None,
) -> CollectorResult:
    """Open GymLog feedback + agent-created queue count (read-only)."""
    api_key = _env(environ, "LINEAR_API_KEY")
    if not api_key:
        return CollectorResult("linear", False, "no key", "LINEAR_API_KEY unset")
    team_id = _env(environ, "LINEAR_TEAM_ID")
    project = _env(environ, "CHIP_STANDUP_LINEAR_PROJECT", "GymLog")
    query = """
    query($first: Int) {
      issues(first: $first) {
        nodes { id identifier state { type name } labels { nodes { name } } }
      }
    }
    """
    variables: dict[str, Any] = {"first": 50}
    try:
        data = _linear_gql(api_key, query, variables, http_get=http_get)
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        return CollectorResult("linear", False, "api fail", str(exc)[:200])
    nodes = (data.get("issues") or {}).get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list):
        return CollectorResult("linear", False, "bad shape", str(data)[:120])
    feedback = 0
    autodev = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        labels = node.get("labels") or {}
        names = {
            str(x.get("name") or "").lower()
            for x in (labels.get("nodes") or [])
            if isinstance(x, dict)
        }
        if "feedback" in names:
            feedback += 1
        if "agent-created" in names or "autodev" in names:
            autodev += 1
    summary = f"open feedback={feedback} autodev-ish={autodev} (project filter: {project or 'team'})"
    ok = True
    return CollectorResult("linear", ok, summary, f"scanned {len(nodes)} issues")


def collect_live_e2e_issues(
    environ: dict[str, str] | None = None,
    *,
    http_get: HttpGet | None = None,
) -> CollectorResult:
    """Open GitHub issues labeled live-e2e on the standup repo."""
    fetch = http_get or _default_http_get
    token = _env(environ, "GITHUB_TOKEN") or _env(environ, "CHIP_STANDUP_GITHUB_TOKEN")
    repo = _env(environ, "CHIP_STANDUP_GITHUB_REPO", DEFAULT_GITHUB_REPO)
    if not token:
        return CollectorResult("live_e2e", False, "no token", "GITHUB_TOKEN unset")
    q = urllib.parse.quote(f"repo:{repo} label:live-e2e state:open")
    url = f"https://api.github.com/search/issues?q={q}&per_page=5"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        status, body = fetch(url, headers, 15.0)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return CollectorResult("live_e2e", False, "unreachable", str(exc)[:200])
    if status != 200:
        return CollectorResult("live_e2e", False, f"http {status}", body[:120])
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return CollectorResult("live_e2e", False, "bad json", body[:80])
    total = int(data.get("total_count", 0)) if isinstance(data, dict) else 0
    ok = True
    return CollectorResult("live_e2e", ok, f"open {total}", repo)


def run_gymlog_collectors(
    environ: dict[str, str] | None = None,
    *,
    http_get: HttpGet | None = None,
) -> GymlogCollectorBundle:
    gha = collect_gha_workflow(environ, http_get=http_get)
    site = collect_gym_site(environ, http_get=http_get)
    linear = collect_linear_gymlog_queue(environ, http_get=http_get)
    live_e2e = collect_live_e2e_issues(environ, http_get=http_get)
    live = all(r.ok for r in (gha, site, linear, live_e2e))
    notes = [f"{r.name}:{'ok' if r.ok else 'FAIL'} {r.summary}" for r in (gha, site, linear, live_e2e)]
    return GymlogCollectorBundle(
        gha=gha,
        site=site,
        linear=linear,
        live_e2e=live_e2e,
        collectors_live=live,
        verified_notes=notes,
    )


def gymlog_fields_from_bundle(
    bundle: GymlogCollectorBundle,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map collector outputs to standup_format.py GymLog keys."""
    failures = [r.name for r in (bundle.gha, bundle.site, bundle.linear, bundle.live_e2e) if not r.ok]
    gate = "PASS" if bundle.collectors_live else f"FAIL ({', '.join(failures) or 'collectors'})"
    deploy = bundle.gha.summary if bundle.gha.ok else f"FAIL — {bundle.gha.summary}"
    autodev_qa = bundle.linear.summary if bundle.linear.ok else f"FAIL — {bundle.linear.summary}"
    local_exec = _env(environ, "CHIP_STANDUP_LOCAL_EXEC", "chip-lane (no RAZ-167 executor)")
    alerts = bundle.live_e2e.summary if bundle.live_e2e.ok else f"FAIL — {bundle.live_e2e.summary}"
    discord_digest = "Chip Chat morning digest (no Discord standup post — RAZ-329)"
    if not bundle.collectors_live:
        blockers = "Collector failure: " + "; ".join(
            f"{r.name}={r.summary}" for r in (bundle.gha, bundle.site, bundle.linear, bundle.live_e2e) if not r.ok
        )
    else:
        blockers = "none"
    return {
        "gate": gate,
        "deploy": deploy,
        "autodev_qa": autodev_qa,
        "local_exec": local_exec,
        "alerts": alerts,
        "discord_digest": discord_digest,
        "blockers": blockers,
    }


def load_homelab_fields(
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Optional Homelab section: JSON file or env JSON blob."""
    env = environ if environ is not None else os.environ
    raw_path = (env.get("CHIP_HOMELAB_STANDUP_JSON") or "").strip()
    if raw_path and os.path.isfile(os.path.expanduser(raw_path)):
        try:
            data = json.load(open(os.path.expanduser(raw_path), encoding="utf-8"))
            if isinstance(data, dict):
                return {k: str(data.get(k, "unknown")) for k in data}
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "window_compliance": "unknown",
        "remux": "unknown",
        "nancy": "unknown",
        "execute": "FROZEN",
        "heartbeat": "unknown",
        "host_slept": "unknown",
        "clean_gate": "unknown",
        "blockers": "Homelab collectors optional — supply CHIP_HOMELAB_STANDUP_JSON",
    }
