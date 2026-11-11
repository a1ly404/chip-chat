"""Daily PM summary of background work (parity P1.4d).

:func:`build` reads one local day of PM activity from the data dir and returns a plain dict:

* ``jobs`` — background jobs that ended that day, with *why* (their source, e.g.
  ``picker:alert:<id>`` or ``routine:<id>``, plus the quoted input title), status, reason and spend.
* ``changes`` — pull-request links found in job outputs (``https://<host>/<owner>/<repo>/pull/<n>``).
* ``refusals`` — jobs refused by the budget or a cap, and picker policy skips (dedupe skips such as
  "already picked today" are not refusals and are left out), counted by reason.
* ``spend`` — total USD from ``bg_jobs_spend.jsonl`` for the day, including the cost of discarded
  reasoning-starved attempts (``retry_cost_usd``), and the number of model hops.
* ``waiting`` — items that need a human decision: queued ``mutate`` jobs (the PM never runs those on
  its own), job outputs carrying a line that starts with :data:`DECISION_MARKER`, and ticks skipped
  because the monthly budget is stopped, plus pending MCP approvals (``ask``-level tool calls queued by
  jobs or turns; see :mod:`chip.mcp_tools`) and pending sudo confirm requests (:mod:`chip.job_policy`).
* ``policy`` / ``sudo_grants`` — job-policy verdict counts for the day and every YOLO sudo grant
  (reason, scope, expiry); denied requests count as refusals.
* ``executor`` — job-policy executor receipts for the day (:mod:`chip.policy_exec`): counts by
  status, granted sudo runs, and failed/refused runs; refused runs also count as refusals.
* ``mcp`` — MCP tool receipts for the day counted by status (ok / pending / refused / error).

:func:`render` turns that into one chat message (Discord-style markdown, capped length).
:func:`render_ping` returns a short one-line-per-decision message, or ``None`` on quiet days.

:func:`post` sends the summary to a generic incoming webhook (JSON ``{"content": ...}``, mentions
disabled) named by ``CHIP_PM_SUMMARY_WEBHOOK``, and the decision ping to
``CHIP_PM_SUMMARY_PING_WEBHOOK`` only when something is waiting. Gated by ``CHIP_PM_SUMMARY=1``
(default off); each day posts at most once unless ``force=True`` (state in
``pm_summary_sent.json``). Day boundaries use ``CHIP_PM_SUMMARY_TZ`` (IANA name, default ``UTC``).
``CHIP_PM_SUMMARY_LINK`` is an optional URL template (``{date}`` is replaced) appended to ping
lines so a reader can jump to the full summary.

Job outputs are model text: they are clipped, rendered inside the summary as data, and the webhook
payload disables all mentions.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chip import bg_jobs, job_policy, mcp_tools, policy_exec
from chip.config import data_dir

FLAG = "CHIP_PM_SUMMARY"
WEBHOOK_ENV = "CHIP_PM_SUMMARY_WEBHOOK"
PING_WEBHOOK_ENV = "CHIP_PM_SUMMARY_PING_WEBHOOK"
TZ_ENV = "CHIP_PM_SUMMARY_TZ"
LINK_ENV = "CHIP_PM_SUMMARY_LINK"
ROUTINE_KIND = "pm-summary"
DECISION_MARKER = "[[decision]]"
MAX_MESSAGE = 1900
MAX_LIST = 8

PR_RE = re.compile(r"https://[A-Za-z0-9.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+")
TITLE_RE = re.compile(r'^title: "(.*)"$', re.MULTILINE)
DEDUPE_SKIPS = frozenset(
    {"duplicate input in batch", "earlier job for this input still queued or running", "already picked today"}
)
# Job reasons that mean "refused by a guard", as opposed to an ordinary failure.
REFUSAL_REASONS = ("step error: BudgetRefused", "spend cap", "hop cap")

Transport = Callable[[str, bytes], int]


class SummaryDisabled(RuntimeError):
    """post() called while CHIP_PM_SUMMARY is not enabled."""


def _env(env: Mapping[str, str] | None) -> dict[str, str]:
    return dict(os.environ if env is None else env)


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return str(_env(env).get(FLAG, "0")).strip().lower() in {"1", "true", "yes", "on"}


def zone(env: Mapping[str, str] | None = None) -> ZoneInfo:
    name = (_env(env).get(TZ_ENV) or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"{TZ_ENV}: unknown time zone {name!r}") from exc


def day_window(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0, 0), tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=tz).astimezone(timezone.utc)
    return start, end


def _parse(ts: Any) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _in(ts: Any, start: datetime, end: datetime) -> bool:
    dt = _parse(ts)
    return dt is not None and start <= dt < end


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _clip(v: Any, n: int) -> str:
    s = " ".join(str(v or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _first_line(v: Any) -> str:
    lines = str(v or "").strip().splitlines()
    return lines[0] if lines else ""


def _outputs(job: Mapping[str, Any]) -> list[str]:
    return [str(h.get("output", "")) for h in (job.get("hops") or []) if isinstance(h, dict)]


def _decisions(job: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for text in _outputs(job):
        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith(DECISION_MARKER):
                what = line[len(DECISION_MARKER):].strip(" :-")
                if what:
                    out.append(what)
    return out


def build(day: date, *, env: Mapping[str, str] | None = None, jobs: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Summary dict for one local day (see module docstring)."""
    e = _env(env)
    tz = zone(e)
    start, end = day_window(day, tz)
    rows = list(bg_jobs.list_all() if jobs is None else jobs)
    ended = [j for j in rows if j.get("status") in {"ok", "failed", "cancelled"} and _in(j.get("ended_at"), start, end)]
    ended.sort(key=lambda j: str(j.get("ended_at")))

    job_rows: list[dict[str, Any]] = []
    changes: list[str] = []
    refusals: Counter[str] = Counter()
    waiting: list[str] = []
    for j in ended:
        title = TITLE_RE.search(str(j.get("prompt", "")))
        job_rows.append(
            {
                "id": j.get("id"),
                "source": j.get("source", ""),
                "why": _clip(title.group(1) if title else _first_line(j.get("prompt")), 120),
                "status": j.get("status"),
                "reason": j.get("reason", ""),
                "spend_usd": round(float(j.get("spend_usd") or 0.0), 6),
            }
        )
        for text in _outputs(j):
            for url in PR_RE.findall(text):
                if url not in changes:
                    changes.append(url)
        reason = str(j.get("reason") or "")
        if j.get("status") == "failed" and reason.startswith(REFUSAL_REASONS):
            refusals[f"job: {reason}"] += 1
        for d in _decisions(j):
            waiting.append(f"{_clip(d, 200)} (job {j.get('id')})")

    for j in rows:
        if j.get("status") == "queued" and j.get("mode") == "mutate":
            waiting.append(f"approve or cancel queued change job {j.get('id')} ({_clip(j.get('source'), 60)})")

    ticks = [t for t in _jsonl(data_dir() / "pm_tick_history.jsonl") if _in(t.get("at"), start, end)]
    stopped = 0
    for t in ticks:
        if t.get("status") == "skipped" and str(t.get("reason", "")).startswith("budget stopped"):
            stopped += 1
        for s in t.get("skipped") or []:
            why = str((s or {}).get("reason", "")) if isinstance(s, dict) else ""
            if why and why not in DEDUPE_SKIPS:
                refusals[f"picker: {why}"] += 1
    if stopped:
        refusals["tick: budget stopped"] += stopped
        waiting.append(f"monthly budget is stopped: PM skipped {stopped} tick(s); raise the cap or wait for the reset")

    waiting += mcp_tools.pending_waiting()
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    mcp_counts = mcp_tools.day_counts(start.strftime(fmt), end.strftime(fmt))
    if mcp_counts.get("refused"):
        refusals["mcp: tool call refused"] += mcp_counts["refused"]

    waiting += job_policy.pending_waiting()
    policy_counts = job_policy.day_counts(start.strftime(fmt), end.strftime(fmt))
    if policy_counts.get("deny"):
        refusals["policy: job request denied"] += policy_counts["deny"]
    executor = policy_exec.day_receipts(start.strftime(fmt), end.strftime(fmt))
    if executor["counts"].get("refused"):
        refusals["executor: run refused"] += executor["counts"]["refused"]
    sudo_grants = [
        {
            "id": g.get("id"),
            "job_id": g.get("job_id"),
            "requested_by": g.get("requested_by"),
            "reason": _clip(g.get("reason"), 120),
            "action": (g.get("scope") or {}).get("action"),
            "target": (g.get("scope") or {}).get("target"),
            "expires_at": g.get("expires_at"),
        }
        for g in job_policy.grants(start.strftime(fmt), end.strftime(fmt))
    ]

    spend_rows = [r for r in _jsonl(data_dir() / "bg_jobs_spend.jsonl") if _in(r.get("ts"), start, end)]
    total = sum(float(r.get("cost_usd") or 0.0) for r in spend_rows)
    retry = sum(float(r.get("retry_cost_usd") or 0.0) for r in spend_rows)
    return {
        "date": day.isoformat(),
        "tz": str(tz.key),
        "ticks": len(ticks),
        "jobs": job_rows,
        "changes": changes,
        "refusals": dict(sorted(refusals.items())),
        "spend": {"usd": round(total, 6), "retry_usd": round(retry, 6), "hops": len(spend_rows)},
        "waiting": waiting,
        "mcp": dict(sorted(mcp_counts.items())),
        "policy": dict(sorted(policy_counts.items())),
        "sudo_grants": sudo_grants,
        "executor": executor,
    }


def _bullets(items: list[str], limit: int = MAX_LIST) -> list[str]:
    out = [f"- {x}" for x in items[:limit]]
    if len(items) > limit:
        out.append(f"- +{len(items) - limit} more")
    return out


def render(summary: Mapping[str, Any]) -> str:
    """One chat message for the day (length-capped)."""
    jobs = summary.get("jobs") or []
    ok = sum(1 for j in jobs if j.get("status") == "ok")
    sp = summary.get("spend") or {}
    lines = [
        f"**PM daily summary {summary.get('date')}** ({summary.get('tz')})",
        f"ticks {summary.get('ticks', 0)} · jobs {len(jobs)} ({ok} ok) · spend ${float(sp.get('usd', 0)):.4f}"
        + (f" (retries ${float(sp.get('retry_usd', 0)):.4f})" if sp.get("retry_usd") else "")
        + f" over {sp.get('hops', 0)} model hops"
        + (
            " · MCP " + ", ".join(f"{k} {v}" for k, v in (summary.get("mcp") or {}).items())
            if summary.get("mcp")
            else ""
        ),
        "",
        "**Jobs PM ran and why**",
    ]
    job_lines = [
        f"`{j.get('id')}` {j.get('status')}"
        + (f" ({j.get('reason')})" if j.get("reason") else "")
        + f" · {_clip(j.get('source'), 60)} · \"{j.get('why')}\""
        for j in jobs
    ]
    lines += _bullets(job_lines) or ["- none"]
    lines += ["", "**Changes**"] + (_bullets(list(summary.get("changes") or [])) or ["- none"])
    ref = [f"{k} ×{v}" for k, v in (summary.get("refusals") or {}).items()]
    lines += ["", "**Refusals**"] + (_bullets(ref) or ["- none"])
    grants = [
        f"`{g.get('job_id')}` {g.get('requested_by')}: {g.get('action')} on {g.get('target') or '-'}"
        f" (until {g.get('expires_at')}) — {g.get('reason')}"
        for g in (summary.get("sudo_grants") or [])
    ]
    if grants:
        lines += ["", "**Sudo grants (YOLO)**"] + _bullets(grants)
    ex = summary.get("executor") or {}
    if ex.get("counts"):
        head = ", ".join(f"{k} {v}" for k, v in ex["counts"].items())
        if ex.get("sudo_runs"):
            head += f" · sudo runs {ex['sudo_runs']}"
        lines += ["", f"**Executor runs** ({head})"] + _bullets(list(ex.get("problems") or []))
    lines += ["", "**Waiting on you**"] + (_bullets(list(summary.get("waiting") or [])) or ["- nothing"])
    text = "\n".join(lines)
    return text if len(text) <= MAX_MESSAGE else text[: MAX_MESSAGE - 12].rstrip() + "\n…(clipped)"


def render_ping(summary: Mapping[str, Any], env: Mapping[str, str] | None = None) -> str | None:
    """Short decision ping (one line per waiting item) or None when nothing is waiting."""
    waiting = list(summary.get("waiting") or [])
    if not waiting:
        return None
    link = (_env(env).get(LINK_ENV) or "").strip().replace("{date}", str(summary.get("date")))
    suffix = f" — {link}" if link else ""
    lines = [f"PM {summary.get('date')}: {len(waiting)} decision(s) waiting"]
    lines += [f"- {_clip(w, 200)}{suffix}" for w in waiting[:MAX_LIST]]
    if len(waiting) > MAX_LIST:
        lines.append(f"- +{len(waiting) - MAX_LIST} more")
    return "\n".join(lines)[:MAX_MESSAGE]


def _http_post(url: str, body: bytes) -> int:
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json", "User-Agent": "chip-pm-summary"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - operator-configured webhook
        return int(getattr(resp, "status", 200))


def _payload(text: str) -> bytes:
    return json.dumps({"content": text, "allowed_mentions": {"parse": []}}).encode("utf-8")


def sent_path() -> Path:
    return data_dir() / "pm_summary_sent.json"


def _sent() -> dict[str, Any]:
    p = sent_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def post(
    day: date,
    *,
    env: Mapping[str, str] | None = None,
    transport: Transport | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build + post the day's summary (and decision ping if needed). Once per day unless ``force``."""
    e = _env(env)
    if not enabled(e):
        raise SummaryDisabled(f"{FLAG} is not enabled; refusing to post the PM summary")
    url = (e.get(WEBHOOK_ENV) or "").strip()
    if not url:
        raise ValueError(f"{WEBHOOK_ENV} is not set")
    key = day.isoformat()
    sent = _sent()
    if key in sent and not force:
        return {"status": "already-sent", "date": key, "sent_at": sent[key].get("at")}
    send = transport or _http_post
    summary = build(day, env=e)
    out: dict[str, Any] = {"status": "ok", "date": key, "waiting": len(summary["waiting"])}
    out["summary_http"] = send(url, _payload(render(summary)))
    ping = render_ping(summary, e)
    ping_url = (e.get(PING_WEBHOOK_ENV) or "").strip()
    if ping and ping_url:
        out["ping_http"] = send(ping_url, _payload(ping))
    else:
        out["ping_http"] = None
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sent[key] = {"at": stamp, "waiting": out["waiting"], "pinged": out["ping_http"] is not None}
    p = sent_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(sent, sort_keys=True), encoding="utf-8")
    tmp.replace(p)
    return out


def routine_day(routine: Mapping[str, Any], now: datetime, env: Mapping[str, str] | None = None) -> date:
    """Day a ``pm-summary`` routine reports on: ``today`` if its prompt says so, else yesterday (local)."""
    local = now.astimezone(zone(env)).date()
    return local if str(routine.get("prompt", "")).strip().lower() == "today" else local - timedelta(days=1)


def routine_dispatch(
    fallback: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    env: Mapping[str, str] | None = None,
    transport: Transport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Routine runner: ``kind=pm-summary`` routines post the summary; others go to ``fallback``."""

    def _run(routine: dict[str, Any]) -> dict[str, Any]:
        if routine.get("kind") != ROUTINE_KIND:
            return fallback(routine)
        now = (clock or (lambda: datetime.now(timezone.utc)))()
        day = routine_day(routine, now, env)
        try:
            res = post(day, env=env, transport=transport, now=now)
        except (SummaryDisabled, ValueError, OSError) as exc:
            return {"status": "failed", "summary": f"pm-summary {day}: {type(exc).__name__}: {exc}"[:2000], "spend_usd": 0.0}
        return {
            "status": "ok",
            "summary": f"pm-summary {day}: {res['status']} waiting={res.get('waiting', 0)} pinged={bool(res.get('ping_http'))}",
            "spend_usd": 0.0,
        }

    return _run


__all__ = [
    "routine_dispatch",
    "DECISION_MARKER",
    "FLAG",
    "LINK_ENV",
    "PING_WEBHOOK_ENV",
    "ROUTINE_KIND",
    "SummaryDisabled",
    "TZ_ENV",
    "WEBHOOK_ENV",
    "build",
    "day_window",
    "enabled",
    "post",
    "render",
    "render_ping",
    "routine_day",
    "sent_path",
    "zone",
]
