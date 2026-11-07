"""Bounded post-turn worker (engine parity plan slice G).

A persisted, in-process job queue with hard caps and restart recovery. The
engine ships the storage, dedup, caps and receipts; it never runs host
commands itself and never starts an unbounded loop — the plugin (or a relay
wiring later) supplies the ``run`` callable per job kind, and even then each
job runs one wall-clock-bounded call before returning a receipt.

Caps (defaults; env-tunable):

* ``MAX_ACTIVE``      — jobs started per ``run_once`` tick (default 2, cap 8);
* ``JOB_TIMEOUT_S``   — per-job wall clock (default 30 s, cap 120 s);
* ``MAX_QUEUE_DEPTH`` — queued jobs visible to the tick (default 32, cap 256);
* spend gate: before a job runs, the worker calls the injected
  ``spend_guard(job)``; a raised error fails the job as ``failed`` with
  ``error: <class name>`` and runs nothing.

Persistence: one UTF-8 JSON file per job under
``<CHIP_CHAT_DATA_DIR>/workers/<queue>.json`` — atomic-ish via tmp+rename, so
a crash between steps cannot corrupt the ledger; ``recover()`` re-marks jobs
caught ``running`` across a process restart as ``failed: timeout stale``.

Bounded means bounded at the RECEIPT, not a kill switch: Python cannot
terminate a thread, so once a runner overruns its deadline the abandon/reap
cannot undo side effects it already performed. The fence therefore has three
hard edges (and a documented limit):

* the immune state machine: only ``run_once``/``cancel``/``recover`` write job
  state, and they read the PERSISTED job (file reconciliation after a thread
  joins) — a late-finishing runner cannot flip a ``failed: timeout`` receipt
  back to ``done`` and cannot re-enter the queue (terminal states are never
  claimed again; a second tick reaps nothing on the same job);
* the runner sees a fenced COPY of the job envelope (its mutations do not
  reach the worker's state machine; the returned string is the only detail);
* the runner law, mirrored by the tests: jobs are non-mutating or explicitly
  cooperative (poll ``worker.job(id)`` and stop on ``state == cancelled``);
  a caller that captures the Worker and writes files from inside its thread
  is not fenced (documented, not hidden).

So a timed-out job's receipts say ``failed: timeout (wall clock)`` even when
its thread finished late; side effects that a stalled runner already produced
cannot be undone and must be risk-controlled at enqueue time (non-mutating
kinds only, or cooperative cancel).


Dedup: ``enqueue(dedup_key=...)`` returns the existing active job (queued or
running) instead of queueing twice; finished jobs may share a key again.
Cancellation: ``cancel(job_id)`` flips ``queued`` jobs to ``cancelled``;
``running`` jobs are marked cancelling so an injected runner that checks
``job(job_id)["state"]`` between steps can stop early.

Flag: ``CHIP_POST_TURN_WORKER`` (default off in repo). ``worker_enabled()``
gates relay/turn wiring; the module API is injectable for mocked tests.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

WORKER_ENV = "CHIP_POST_TURN_WORKER"

DEFAULT_MAX_ACTIVE = 2
CAP_MAX_ACTIVE = 8
DEFAULT_TIMEOUT_S = 30.0
CAP_TIMEOUT_S = 120.0
DEFAULT_MAX_QUEUE_DEPTH = 32
CAP_MAX_QUEUE_DEPTH = 256

_DEFAULTS = {"max_active": DEFAULT_MAX_ACTIVE, "timeout_s": DEFAULT_TIMEOUT_S, "max_queue_depth": DEFAULT_MAX_QUEUE_DEPTH}
_CAPS = {"max_active": CAP_MAX_ACTIVE, "timeout_s": CAP_TIMEOUT_S, "max_queue_depth": CAP_MAX_QUEUE_DEPTH}

TERMINAL_STATES = frozenset({"done", "cancelled", "failed"})
ACTIVE_STATES = frozenset({"queued", "running"})

ENV_TRUE = {"1", "true", "yes", "on"}


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def worker_enabled(environ: dict[str, str] | None = None) -> bool:
    """True only when ``CHIP_POST_TURN_WORKER`` is an explicit truthy value."""
    env = environ if environ is not None else os.environ
    return str(env.get(WORKER_ENV, "")).strip().lower() in ENV_TRUE


def _num(env: dict[str, str], name: str, default: float, high: float) -> float:
    raw = str(env.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return min(value, high)


def max_active(environ: dict[str, str] | None = None) -> int:
    env = environ if environ is not None else os.environ
    return int(_num(env, "CHIP_WORKER_MAX_ACTIVE", _DEFAULTS["max_active"], _CAPS["max_active"]))


_max_active = max_active  # internal alias: run_once's kwarg shadows the name locally


def job_timeout_s(environ: dict[str, str] | None = None) -> float:
    env = environ if environ is not None else os.environ
    return _num(env, "CHIP_WORKER_TIMEOUT_S", _DEFAULTS["timeout_s"], _CAPS["timeout_s"])


def max_queue_depth(environ: dict[str, str] | None = None) -> int:
    env = environ if environ is not None else os.environ
    return int(_num(env, "CHIP_WORKER_MAX_QUEUE_DEPTH", _DEFAULTS["max_queue_depth"], _CAPS["max_queue_depth"]))


class Worker:
    """One bounded queue. Callers inject ``runner(job) -> detail string``."""

    def __init__(
        self,
        queue: str,
        *,
        data_dir: Path | str | None = None,
        runner: Callable[[dict[str, Any]], str] | None = None,
        spend_guard: Callable[[dict[str, Any]], None] | None = None,
        environ: dict[str, str] | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        if not str(queue).strip() or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in queue):
            raise ValueError(f"invalid queue name: {queue!r}")
        self.queue = queue
        self.environ = environ if environ is not None else dict(os.environ)
        self.runner = runner
        self.spend_guard = spend_guard
        self._now = now
        if data_dir is None:
            from chip.config import data_dir as _dd  # local import: avoid config cost in tests

            self.root = _dd() / "workers"
        else:
            self.root = Path(data_dir) / "workers"
        self.dir = self.root / queue
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ persistence

    def _path(self, job_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in job_id)
        return self.dir / f"{safe}.json"

    def _save(self, job: dict[str, Any]) -> None:
        path = self._path(job["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, default=str, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def job(self, job_id: str) -> dict[str, Any] | None:
        try:
            doc = json.loads(self._path(job_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return doc if isinstance(doc, dict) else None

    def list_jobs(self, states: set[str] | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in self.dir.glob("*.json"):
            if path.suffix == ".tmp":
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(doc, dict) and (states is None or doc.get("state") in states):
                out.append(doc)
        out.sort(key=lambda d: str(d.get("enqueue_ts", "")))
        return out

    # ------------------------------------------------------------ lifecycle

    def enqueue(
        self,
        kind: str,
        args: dict[str, Any] | None = None,
        *,
        dedup_key: str | None = None,
        timeout_s: float | None = None,
    ) -> tuple[str, str]:
        """(job_id, state). Active jobs with the same dedup key are not duplicated."""
        kind = str(kind or "").strip()
        if not kind or not str(kind).replace("_", "a").replace("-", "a").isalnum():
            raise ValueError(f"invalid job kind: {kind!r}")
        depth_cap = max_queue_depth(self.environ)
        depth = len(self.list_jobs(states=set(ACTIVE_STATES)))
        if depth >= depth_cap:
            raise OverflowError(f"queue depth cap {depth_cap} reached ({depth} active); drain before enqueue")
        if dedup_key:
            existing = [job for job in self.list_jobs(states=set(ACTIVE_STATES)) if job.get("dedup_key") == dedup_key]
            if existing:
                job = existing[0]
                return str(job["id"]), str(job["state"])
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        job: dict[str, Any] = {
            "schema": 1,
            "id": job_id,
            "kind": kind,
            "args": dict(args or {}),
            "dedup_key": dedup_key or None,
            "state": "queued",
            "timeout_s": min(float(timeout_s if timeout_s else job_timeout_s(self.environ)), CAP_TIMEOUT_S),
            "enqueue_ts": _ts(),
            "enqueued_mono": self._now(),
            "history": [],
        }
        self._save(job)
        return job_id, job["state"]

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self.job(job_id)
        if not job:
            return None
        state = str(job.get("state"))
        if state in ACTIVE_STATES:
            job["state"] = "cancelled"
            job["cancelled_ts"] = _ts()
            job["history"].append({"ts": _ts(), "state": "cancelled"})
            self._save(job)
        return job

    def recover(self) -> list[str]:
        """Fail jobs caught 'running' from a previous process (restart recovery)."""
        reaped: list[str] = []
        for job in self.list_jobs(states={"running"}):
            started = float(job.get("started_mono") or 0.0)
            if self._now() - started > float(job.get("timeout_s") or job_timeout_s(self.environ)):
                job["state"] = "failed"
                job["error"] = "timeout stale (process restart)"
                job["finished_ts"] = _ts()
                job["history"].append({"ts": _ts(), "state": "failed", "error": job["error"]})
                self._save(job)
                reaped.append(str(job["id"]))
        return reaped

    def _claim(self) -> dict[str, Any] | None:
        for job in self.list_jobs(states={"queued"}):
            job["state"] = "running"
            job["started_mono"] = self._now()
            job["started_ts"] = _ts()
            self._save(job)
            return job
        return None

    # ------------------------------------------------------------ execution

    def run_once(self, *, max_active: int | None = None) -> dict[str, Any]:
        """At most ``max_active`` jobs this tick; every job ends in a terminal state.

        Each runner call executes in a daemon thread joined with a strict
        wall-clock deadline, so a stalled runner still lands in ``failed:
        timeout`` within the per-job cap — the tick itself cannot hang.
        """
        env = self.environ
        budget = int(min(max_active if max_active is not None else _max_active(env), CAP_MAX_ACTIVE))
        out: dict[str, Any] = {"ran": 0, "done": [], "failed": [], "cancelled": 0}
        ran = 0
        while ran < budget:
            job = self._claim()
            if job is None:
                break
            ran += 1
            job_id = str(job["id"])
            # Spend gate first: nothing runs when the guard refuses.
            if self.spend_guard is not None:
                try:
                    self.spend_guard(job)
                except Exception as exc:  # noqa: BLE001 — a refused job is a failed receipt, not a crash
                    job["state"] = "failed"
                    job["error"] = f"spend gate: {exc.__class__.__name__}"
                    job["finished_ts"] = _ts()
                    job["history"].append({"ts": _ts(), "state": "failed"})
                    out["failed"].append(job_id)
                    self._save(job)
                    continue
                if str(job["state"]) == "cancelled":  # cancelled during the spend check
                    job["history"].append({"ts": _ts(), "state": "cancelled"})
                    self._save(job)
                    out["cancelled"] += 1
                    continue
            deadline = float(job.get("timeout_s") or job_timeout_s(env))
            finished: dict[str, Any] = {}
            # Fence: the runner gets a COPY of the envelope. Mutations inside
            # the runner thread cannot reach the worker's state machine —
            # recorded state upholds only the persisted/claimed writes the
            # tick itself makes. A runner that captures the Worker itself can
            # still write files (documented limit, not fenced).
            view = dict(job)

            def _run(view: dict[str, Any], finished: dict[str, Any]) -> None:
                try:
                    finished["detail"] = str(self.runner(view) if self.runner else "no runner: noop")
                except Exception as exc:  # noqa: BLE001 — a failed job is a receipt
                    finished["error"] = exc.__class__.__name__
                    finished["detail"] = f"error: {exc.__class__.__name__}"

            thread = threading.Thread(target=_run, args=(view, finished), daemon=True, name=f"chip-worker-{job_id}")
            started_mono = self._now()
            thread.start()
            thread.join(timeout=deadline)
            # Reconcile a file-level cancel (a runner may call cancel() mid-run):
            # the persisted terminal state wins over a stale in-memory one.
            fresh = self.job(job_id)
            if thread.is_alive():
                job["state"] = "failed"
                job["error"] = "timeout (wall clock)"
                out["failed"].append(job_id)
                job["detail"] = "timeout (stalled runner abandoned: daemon thread; late completion ignored)"
            elif fresh and fresh.get("state") == "cancelled":
                job["state"] = "cancelled"
                out["cancelled"] += 1
                if "detail" in finished:
                    job["detail"] = f"cancelled: {finished['detail'][:400]}"
            elif finished.get("error"):
                job["state"] = "failed"
                job["error"] = finished["error"]
                out["failed"].append(job_id)
            else:
                job["state"] = "done"
                out["done"].append(job_id)
            if "detail" in finished and "detail" not in job:
                job["detail"] = finished["detail"][:600]
            job["finished_ts"] = _ts()
            job["history"].append({"ts": _ts(), "state": job["state"]})
            self._save(job)
        out["ran"] = ran
        return out
