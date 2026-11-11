"""Background agent jobs with hop, spend, daily and concurrency caps (parity P1.4).

A background job is a task prompt run as a bounded turn loop by an injected *step*
function. Jobs live under ``CHIP_CHAT_DATA_DIR/bg_jobs/<id>.json`` (one JSON record each,
including a per-hop receipt list). This module never calls a model or a host by itself:
the default step is :func:`mock_step` (offline, zero spend).

Caps (all enforced here, before work starts or before each hop):

* **per-job hop cap** ``max_hops`` — loop stops with ``failed: hop cap`` if the step never
  reports ``done``. Default ``CHIP_JOBS_MAX_HOPS`` (8); hard ceiling 50.
* **per-job spend cap** ``max_spend_usd`` — checked before every hop (a hop never starts
  once the job has reached its ceiling) and after every hop (overshoot fails the job with
  ``failed: spend cap``). Default ``CHIP_JOBS_MAX_SPEND_USD`` (0.25); hard ceiling 5.00.
* **daily caps** (UTC day) — ``CHIP_JOBS_DAILY_MAX`` jobs started (default 50) and
  ``CHIP_JOBS_DAILY_SPEND_USD`` (default 1.00). The daily spend check *reserves* each
  running job's full ``max_spend_usd`` so parallel jobs cannot jointly overshoot.
  A job that would break a daily cap stays ``queued`` (``deferred`` in the tick result).
* **concurrency** — ``read`` jobs run in parallel up to ``CHIP_JOBS_READ_CONCURRENCY``
  (default 3); ``mutate`` jobs are serialized: at most one mutating job runs at a time.

``run_pending()`` is gated by ``CHIP_JOBS=1`` (default off). Enqueue/list/show/cancel are
plain data operations and work with the flag off, like ``chip routine add``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from chip.config import data_dir

SCHEMA_VERSION = 1
FLAG = "CHIP_JOBS"
MODES = frozenset({"read", "mutate"})
STATUSES = frozenset({"queued", "running", "ok", "failed", "cancelled"})
TERMINAL = frozenset({"ok", "failed", "cancelled"})
HARD_MAX_HOPS = 50
HARD_MAX_SPEND_USD = 5.0
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.IGNORECASE)

# step(job, hop_index, prior_hops) -> {"output": str, "spend_usd": float, "done": bool}
Step = Callable[[dict[str, Any], int, list[dict[str, Any]]], dict[str, Any]]


class JobsDisabled(RuntimeError):
    """Raised when run_pending() is called while CHIP_JOBS is not enabled."""


def _env(env: dict[str, str] | None) -> dict[str, str]:
    return dict(os.environ) if env is None else env


def enabled(env: dict[str, str] | None = None) -> bool:
    return str(_env(env).get(FLAG, "0")).strip().lower() in {"1", "true", "yes", "on"}


def _num(env: dict[str, str], name: str, default: float, lo: float, hi: float) -> float:
    raw = str(env.get(name, "")).strip()
    if not raw:
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    return min(max(val, lo), hi)


def caps(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Effective caps from env (bad values fall back to defaults; clamped to hard ceilings)."""
    e = _env(env)
    return {
        "max_hops": int(_num(e, "CHIP_JOBS_MAX_HOPS", 8, 1, HARD_MAX_HOPS)),
        "max_spend_usd": _num(e, "CHIP_JOBS_MAX_SPEND_USD", 0.25, 0.0, HARD_MAX_SPEND_USD),
        "daily_max_jobs": int(_num(e, "CHIP_JOBS_DAILY_MAX", 50, 0, 10_000)),
        "daily_spend_usd": _num(e, "CHIP_JOBS_DAILY_SPEND_USD", 1.0, 0.0, 1_000.0),
        "read_concurrency": int(_num(e, "CHIP_JOBS_READ_CONCURRENCY", 3, 1, 32)),
    }


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return _utc(dt).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------ storage

_io_lock = threading.Lock()


def jobs_dir() -> Path:
    return data_dir() / "bg_jobs"


def _path(job_id: str) -> Path:
    if not _ID.match(job_id or ""):
        raise ValueError(f"invalid job id: {job_id!r}")
    return jobs_dir() / f"{job_id}.json"


def _save(doc: dict[str, Any]) -> None:
    path = _path(doc["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".json.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load(job_id: str) -> dict[str, Any]:
    path = _path(job_id)
    if not path.is_file():
        raise FileNotFoundError(f"no job: {job_id}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if int(doc.get("schema", 0)) != SCHEMA_VERSION:
        raise ValueError("job schema mismatch")
    return doc


def list_all(status: str | None = None) -> list[dict[str, Any]]:
    root = jobs_dir()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(root.glob("*.json")):
        try:
            doc = load(p.stem)
        except (ValueError, json.JSONDecodeError, FileNotFoundError):
            continue
        if status is None or doc.get("status") == status:
            out.append(doc)
    out.sort(key=lambda d: (d.get("created_at", ""), d["id"]))
    return out


def enqueue(
    prompt: str,
    *,
    now: datetime,
    mode: str = "read",
    max_hops: int | None = None,
    max_spend_usd: float | None = None,
    source: str = "cli",
    job_id: str | None = None,
    params: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Persist a queued job. Refuses (ValueError) caps above the configured ceilings."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt must be non-empty")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {sorted(MODES)}")
    c = caps(env)
    hops = c["max_hops"] if max_hops is None else int(max_hops)
    if hops < 1 or hops > c["max_hops"]:
        raise ValueError(f"max_hops must be 1..{c['max_hops']} (configured ceiling)")
    spend = c["max_spend_usd"] if max_spend_usd is None else float(max_spend_usd)
    if spend < 0 or spend > c["max_spend_usd"]:
        raise ValueError(f"max_spend_usd must be 0..{c['max_spend_usd']} (configured ceiling)")
    jid = job_id or uuid.uuid4().hex[:12]
    path = _path(jid)
    if path.exists():
        raise FileExistsError(f"job exists: {jid}")
    doc = {
        "schema": SCHEMA_VERSION,
        "id": jid,
        "kind": "agent",
        "prompt": prompt[:8000],
        "mode": mode,
        "max_hops": hops,
        "max_spend_usd": spend,
        "source": str(source)[:200],
        "params": dict(params or {}),
        "status": "queued",
        "reason": "",
        "hops": [],
        "spend_usd": 0.0,
        "created_at": _iso(now),
        "started_at": None,
        "ended_at": None,
    }
    with _io_lock:
        _save(doc)
    return doc


def cancel(job_id: str, *, now: datetime) -> dict[str, Any]:
    doc = load(job_id)
    if doc["status"] != "queued":
        raise ValueError(f"only queued jobs can be cancelled (status={doc['status']})")
    doc.update(status="cancelled", reason="cancelled", ended_at=_iso(now))
    with _io_lock:
        _save(doc)
    return doc


# ------------------------------------------------------------------ caps / selection


def _day(iso: str | None) -> str:
    return (iso or "")[:10]


def daily_usage(now: datetime, jobs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Jobs started and spend (actual + reserved for running jobs) for now's UTC day."""
    today = _iso(now)[:10]
    rows = list_all() if jobs is None else jobs
    started = [j for j in rows if _day(j.get("started_at")) == today]
    spent = sum(float(j.get("spend_usd") or 0.0) for j in started if j.get("status") != "running")
    reserved = sum(float(j.get("max_spend_usd") or 0.0) for j in rows if j.get("status") == "running")
    return {"day": today, "started": len(started), "spent_usd": round(spent, 6), "reserved_usd": round(reserved, 6)}


def select(
    now: datetime, jobs: list[dict[str, Any]] | None = None, *, env: dict[str, str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Pure-ish selection: which queued jobs may start now, FIFO, under every cap.

    Returns ``{"start": [...], "deferred": [{"id", "reason"}...]}``. Read jobs fill free
    read slots; at most one mutating job is selected and only if none is running.
    """
    c = caps(env)
    rows = list_all() if jobs is None else jobs
    usage = daily_usage(now, rows)
    running = [j for j in rows if j.get("status") == "running"]
    read_slots = c["read_concurrency"] - sum(1 for j in running if j["mode"] == "read")
    mutate_busy = any(j["mode"] == "mutate" for j in running)
    started = usage["started"]
    committed = usage["spent_usd"] + usage["reserved_usd"]
    start: list[dict[str, Any]] = []
    deferred: list[dict[str, str]] = []
    for job in (j for j in rows if j.get("status") == "queued"):
        if job["mode"] == "mutate" and mutate_busy:
            deferred.append({"id": job["id"], "reason": "mutate serialized: another mutating job is running"})
            continue
        if job["mode"] == "read" and read_slots <= 0:
            deferred.append({"id": job["id"], "reason": "read concurrency cap"})
            continue
        if started >= c["daily_max_jobs"]:
            deferred.append({"id": job["id"], "reason": "daily job cap"})
            continue
        if committed + float(job["max_spend_usd"]) > c["daily_spend_usd"] + 1e-9:
            deferred.append({"id": job["id"], "reason": "daily spend cap"})
            continue
        start.append(job)
        started += 1
        committed += float(job["max_spend_usd"])
        if job["mode"] == "mutate":
            mutate_busy = True
        else:
            read_slots -= 1
    return {"start": start, "deferred": deferred}


# ------------------------------------------------------------------ execution


MAX_RECEIPTS_PER_HOP = 10
_RECEIPT_KEYS = ("server", "tool", "status", "reason", "approval_id", "is_error", "excerpt", "args_sha256", "ms")


def _clean_receipts(raw: Any) -> list[dict[str, Any]]:
    """Tool receipts a step reported for one hop: known keys only, bounded size."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for r in raw[:MAX_RECEIPTS_PER_HOP]:
        if not isinstance(r, dict):
            continue
        row = {k: r[k] for k in _RECEIPT_KEYS if k in r}
        for k, v in list(row.items()):
            if isinstance(v, str):
                row[k] = v[:1500]
        out.append(row)
    return out


def run_job(job: dict[str, Any], step: Step, *, clock: Callable[[], datetime]) -> dict[str, Any]:
    """Run one claimed job's bounded loop; persists and returns the final record."""
    hops: list[dict[str, Any]] = list(job.get("hops") or [])
    spend = float(job.get("spend_usd") or 0.0)
    status, reason = "failed", "hop cap"
    for i in range(len(hops), int(job["max_hops"])):
        if spend >= float(job["max_spend_usd"]) and float(job["max_spend_usd"]) > 0:
            status, reason = "failed", "spend cap"
            break
        try:
            res = step(dict(job), i, [dict(h) for h in hops]) or {}
            hop_spend = max(0.0, float(res.get("spend_usd", 0.0) or 0.0))
            out = str(res.get("output", ""))[:2000]
            done = bool(res.get("done"))
            receipts = _clean_receipts(res.get("receipts"))
        except Exception as exc:  # step errors fail the job, never raise
            hops.append({"hop": i, "at": _iso(clock()), "output": "", "spend_usd": 0.0, "error": f"{type(exc).__name__}: {exc}"[:500]})
            status, reason = "failed", f"step error: {type(exc).__name__}"
            break
        spend = round(spend + hop_spend, 9)
        hop_rec: dict[str, Any] = {"hop": i, "at": _iso(clock()), "output": out, "spend_usd": hop_spend}
        if receipts:
            hop_rec["receipts"] = receipts
        hops.append(hop_rec)
        if spend > float(job["max_spend_usd"]) + 1e-9:
            status, reason = "failed", "spend cap"
            break
        if done:
            status, reason = "ok", ""
            break
    doc = dict(job)
    doc.update(hops=hops, spend_usd=spend, status=status, reason=reason, ended_at=_iso(clock()))
    with _io_lock:
        _save(doc)
    return doc


def run_pending(
    now: datetime,
    step: Step | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    env: dict[str, str] | None = None,
    exec_runner: Callable[..., dict[str, Any]] | None = None,
    notifier: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Start every job ``select()`` allows: reads in parallel (capped), mutates one at a time.

    With ``CHIP_JOB_POLICY=1`` every mutate job is routed through the job policy first
    (``policy_exec.gate_job``): ``allow`` attaches a fresh decision ticket, ``ask`` leaves the job
    queued and posts an operator confirm request, ``deny`` fails it. A mutate job with
    ``params.exec`` and a ticket runs its ``params.policy_request`` argv through the policy executor.

    Returns ``{"ran": [final job records], "deferred": [{"id", "reason"}]}``.
    """
    if not enabled(env):
        raise JobsDisabled(f"{FLAG} is not enabled; refusing to run background jobs")
    now = _utc(now)
    step = step or mock_step
    clock = clock or (lambda: now)
    c = caps(env)
    picked = select(now, env=env)
    claimed: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    from chip import job_policy

    policy_on = job_policy.enabled(env)
    for job in picked["start"]:
        job = dict(job)
        job.pop("policy", None)  # a ticket is only ever minted here, never carried in by the record
        if job["mode"] == "mutate" and policy_on:
            from chip import policy_exec

            gate = policy_exec.gate_job(job, env=env, now=clock(), notifier=notifier)
            if gate["verdict"] == "ask":
                job["policy"] = {k: gate[k] for k in ("verdict", "request_id", "notify") if k in gate}
                with _io_lock:
                    _save(job)
                picked["deferred"].append({"id": job["id"], "reason": f"waiting on operator confirm {gate.get('request_id', '')}"})
                continue
            if gate["verdict"] != "allow":
                job.update(status="failed", reason=f"policy deny: {gate['reason']}"[:300], ended_at=_iso(clock()),
                           policy={"verdict": "deny"})
                with _io_lock:
                    _save(job)
                refused.append(job)
                continue
            job["policy"] = {"verdict": "allow", "ticket": gate["ticket"]}
        job.update(status="running", started_at=_iso(clock()))
        with _io_lock:
            _save(job)
        claimed.append(job)
    reads = [j for j in claimed if j["mode"] == "read"]
    mutates = [j for j in claimed if j["mode"] == "mutate"]
    ran: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, c["read_concurrency"])) as pool:
        futures = [pool.submit(run_job, j, step, clock=clock) for j in reads]
        for j in mutates:  # serialized: select() yields at most one
            jstep = step
            if (j.get("policy") or {}).get("ticket") and (j.get("params") or {}).get("exec"):
                from chip import policy_exec

                jstep = policy_exec.exec_step(env=env, runner=exec_runner, clock=clock)
            ran.append(run_job(j, jstep, clock=clock))
        ran.extend(f.result() for f in futures)
    ran.extend(refused)
    ran.sort(key=lambda d: (d.get("created_at", ""), d["id"]))
    return {"ran": ran, "deferred": picked["deferred"]}


def recover(now: datetime, *, stale_after_s: float = 3600.0) -> list[str]:
    """Mark ``running`` jobs older than ``stale_after_s`` as failed (process died mid-run)."""
    now = _utc(now)
    out: list[str] = []
    for job in list_all(status="running"):
        started = datetime.fromisoformat(str(job.get("started_at")).replace("Z", "+00:00"))
        if (now - started).total_seconds() >= stale_after_s:
            job.update(status="failed", reason="interrupted", ended_at=_iso(now))
            with _io_lock:
                _save(job)
            out.append(job["id"])
    return out


def mock_step(job: dict[str, Any], hop: int, prior: list[dict[str, Any]]) -> dict[str, Any]:
    """Offline step: finishes after ``params.mock_hops`` hops (default 1), spends ``params.mock_spend``."""
    params = job.get("params") or {}
    need = int(params.get("mock_hops", 1))
    return {
        "output": f"mock: hop {hop + 1}/{need} for job {job['id']}",
        "spend_usd": float(params.get("mock_spend", 0.0)),
        "done": hop + 1 >= need,
    }


# ------------------------------------------------------------------ routines bridge


def routine_runner(
    *, step: Step | None = None, env: dict[str, str] | None = None, clock: Callable[[], datetime] | None = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Adapter so ``routines.tick`` dispatches each due routine as a read-mode background job.

    The job is enqueued with ``source=routine:<id>`` and run immediately under the same caps.
    A job deferred by a cap is cancelled and reported as a ``failed`` routine run.
    """

    def _run(routine: dict[str, Any]) -> dict[str, Any]:
        now = (clock or (lambda: datetime.now(timezone.utc)))()
        prompt = routine.get("prompt") or f"routine {routine['id']} ({routine.get('kind', '')})"
        job = enqueue(prompt, now=now, mode="read", source=f"routine:{routine['id']}", env=env)
        res = run_pending(now, step, clock=clock, env=env)
        mine = next((j for j in res["ran"] if j["id"] == job["id"]), None)
        if mine is None:
            why = next((d["reason"] for d in res["deferred"] if d["id"] == job["id"]), "not started")
            cancel(job["id"], now=now)
            return {"status": "failed", "summary": f"job {job['id']} deferred: {why}", "spend_usd": 0.0}
        last = (mine["hops"] or [{}])[-1].get("output", "")
        summary = f"job {mine['id']} {mine['status']}" + (f" ({mine['reason']})" if mine["reason"] else "") + f": {last}"
        return {"status": "ok" if mine["status"] == "ok" else "failed", "summary": summary, "spend_usd": mine["spend_usd"]}

    return _run
