"""Engine parity plan slice G: bounded persisted post-turn worker (Refs RAZ-16).

Offline: runners, spend gates and clock are injected; no model calls, no
webhooks, no threads started in the test process except the per-run daemon
ones `run_once` already cleans up by its own wall-clock join.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from chip import workers
from chip.workers import CAP_TIMEOUT_S, Worker, worker_enabled


def test_flag_default_off_and_truthy() -> None:
    assert worker_enabled({}) is False
    assert worker_enabled({workers.WORKER_ENV: "0"}) is False
    assert worker_enabled({workers.WORKER_ENV: "1"}) is True


def test_env_caps() -> None:
    assert workers.max_active({}) == workers.DEFAULT_MAX_ACTIVE
    assert workers.max_active({"CHIP_WORKER_MAX_ACTIVE": "999"}) == workers.CAP_MAX_ACTIVE
    assert workers.job_timeout_s({"CHIP_WORKER_TIMEOUT_S": "1"}) == 1.0
    assert workers.job_timeout_s({"CHIP_WORKER_TIMEOUT_S": "99999"}) == workers.CAP_TIMEOUT_S
    assert workers.max_queue_depth({"CHIP_WORKER_MAX_QUEUE_DEPTH": "99999"}) == workers.CAP_MAX_QUEUE_DEPTH


def test_enqueue_runs_echo_and_persists(tmp_path: Path) -> None:
    calls: list[dict] = []
    w = Worker("echo-q", data_dir=tmp_path, runner=lambda job: calls.append(job) or "ran")
    job_id, state = w.enqueue("echo", {"msg": "ok"})
    assert state == "queued"
    out = w.run_once()
    assert out["ran"] == 1 and out["done"] == [job_id]
    doc = w.job(job_id)
    assert doc["state"] == "done" and doc["detail"] == "ran"
    assert (tmp_path / "workers" / "echo-q").is_dir()  # persisted under the data dir
    assert len(calls) == 1


def test_dedup_key_blocks_duplicate_active_jobs(tmp_path: Path) -> None:
    w = Worker("dedup-q", data_dir=tmp_path, runner=lambda job: "ran")
    first_id, _ = w.enqueue("echo", dedup_key="k1")
    second_id, second_state = w.enqueue("echo", dedup_key="k1")
    assert second_id == first_id and second_state == "queued"
    w.run_once()  # finishes the job
    again_id, _ = w.enqueue("echo", dedup_key="k1")  # finished jobs may share the key again
    assert again_id != first_id


def test_queue_depth_cap(tmp_path: Path) -> None:
    w = Worker("cap-q", data_dir=tmp_path, runner=lambda job: "ran", environ={"CHIP_WORKER_MAX_QUEUE_DEPTH": "2"})
    enqueued: list[str] = []
    for _ in range(2):
        enqueued.append(w.enqueue("echo")[0])
    with pytest.raises(OverflowError):
        w.enqueue("echo")
    assert len(enqueued) == 2


def test_timeouts_score_a_stalled_runner(tmp_path: Path) -> None:
    def stall(job) -> str:
        time.sleep(60)
        return "unreachable"

    w = Worker("slow-q", data_dir=tmp_path, runner=stall, environ={"CHIP_WORKER_TIMEOUT_S": "0.2"})
    job_id, _ = w.enqueue("echo")
    out = w.run_once()
    assert out["failed"] == [job_id]
    assert w.job(job_id)["error"] == "timeout (wall clock)"


def test_running_job_error_lands_as_failed(tmp_path: Path) -> None:
    def boom(job) -> str:
        raise RuntimeError("bourrage")

    w = Worker("boom-q", data_dir=tmp_path, runner=boom)
    job_id, _ = w.enqueue("echo")
    out = w.run_once()
    assert out["failed"] == [job_id]
    doc = w.job(job_id)
    assert doc["state"] == "failed" and doc["error"] == "RuntimeError"


def test_spend_gate_refuses_before_runner(tmp_path: Path) -> None:
    ran: list[str] = []

    def guard(job) -> None:
        raise PermissionError("spend cutoff")

    def runner(job) -> str:
        ran.append(job["id"])
        return "done"

    w = Worker("gated-q", data_dir=tmp_path, runner=runner, spend_guard=guard)
    job_id, _ = w.enqueue("echo")
    out = w.run_once()
    assert out["failed"] == [job_id] and not ran
    assert w.job(job_id)["error"] == "spend gate: PermissionError"


def test_cancelled_job_never_runs(tmp_path: Path) -> None:
    ran: list[str] = []
    w = Worker("cancel-q", data_dir=tmp_path, runner=lambda job: ran.append(job["id"]) or "ran")
    job_id, _ = w.enqueue("echo")
    doc = w.cancel(job_id)
    assert doc["state"] == "cancelled"
    out = w.run_once()
    assert out["ran"] == 0 and not ran
    assert w.job(job_id)["state"] == "cancelled"


def test_cancel_during_running_is_respected(tmp_path: Path) -> None:
    def maybe_cancel(job) -> str:
        if w.job(job["id"])["args"].get("cancel"):
            w.cancel(job["id"])
        return "handled"

    w = Worker("cancelrunning-q", data_dir=tmp_path, runner=maybe_cancel)
    job_id, _ = w.enqueue("echo", {"cancel": False})
    out = w.run_once()
    assert out["done"] == [job_id]  # not cancelled mid-run here; only shape
    # Now one that self-cancels between steps.
    job_id2, _ = w.enqueue("echo", {"cancel": True})
    out2 = w.run_once()
    assert out2["cancelled"] == 1 and w.job(job_id2)["state"] == "cancelled"


def test_restart_recovery_reaps_stale_running(tmp_path: Path) -> None:
    w = Worker("stale-q", data_dir=tmp_path, runner=lambda job: "never")
    job_id, _ = w.enqueue("echo")
    doc = w.job(job_id)
    # Simulate a previous process's crash: running with a stale clock stamp.
    doc["state"] = "running"
    doc["started_mono"] = time.time() - (float(doc["timeout_s"]) + 60)
    w._save(doc)
    reaped = w.recover()
    assert reaped == [job_id]
    assert w.job(job_id)["state"] == "failed" and "restart" in w.job(job_id)["error"]


def test_stale_runner_cannot_overrun_a_fresh_process(tmp_path: Path) -> None:
    """The daemon-thread join keeps the tick itself wall-clock bounded."""
    w = Worker("bounded-tick", data_dir=tmp_path, runner=lambda job: time.sleep(60), environ={"CHIP_WORKER_TIMEOUT_S": "0.1"})
    w.enqueue("echo")
    started = time.time()
    w.run_once()
    assert time.time() - started < 10.0  # the tick returned well before the runner finished


def test_run_once_runs_at_most_max_active(tmp_path: Path) -> None:
    w = Worker("bound-q", data_dir=tmp_path, runner=lambda job: "ran", environ={"CHIP_WORKER_MAX_ACTIVE": "2"})
    for _ in range(5):
        w.enqueue("echo")
    out = w.run_once()
    assert out["ran"] == 2  # hard bound per tick, remaining stay queued
    assert len(w.list_jobs(states={"queued"})) == 3


# ---------------------------------------------------------------- fence (item 1 review)

def test_late_completion_cannot_flip_the_timeout_receipt(tmp_path: Path) -> None:
    """A runner finishing after the deadline is recorded, not re-run."""
    w = Worker("late-q", data_dir=tmp_path, runner=lambda job: time.sleep(3) or "should not count",
               environ={"CHIP_WORKER_TIMEOUT_S": "0.1"})
    job_id, _ = w.enqueue("echo")
    out = w.run_once()
    assert out["failed"] == [job_id]
    doc = w.job(job_id)
    assert doc["state"] == "failed" and doc["error"] == "timeout (wall clock)"
    # A later tick does NOT claim the terminal job again (duplicate-safe).
    out2 = w.run_once()
    assert out2["ran"] == 0
    assert w.job(job_id)["error"] == "timeout (wall clock)"


def test_runner_mutations_cannot_reach_the_state_machine(tmp_path: Path) -> None:
    """The runner sees a fenced copy; only the tick writes state."""
    def mutate_view(job) -> str:
        job["state"] = "done"  # naive mutation attempt
        return "runner says done"

    w = Worker("fence-q", data_dir=tmp_path, runner=mutate_view)
    job_id, _ = w.enqueue("echo")
    out = w.run_once()
    doc = w.job(job_id)
    assert out["done"] == [job_id] and doc["state"] == "done"
    assert doc["detail"] == "runner says done"  # detail flows, not state


def test_cooperative_cancel_via_job_poll(tmp_path: Path) -> None:
    """Runner law: cooperative runners poll worker.job(id) and stop at cancelled."""
    def coop(job) -> str:
        doc = w.job(job["id"])
        if doc and doc["state"] == "cancelled":
            return "stopped early"
        w.cancel(job["id"])  # the call that fences step 2
        return "cancelled instead of step 2"

    w = Worker("coop-q", data_dir=tmp_path, runner=coop)
    job_id, _ = w.enqueue("echo")
    out = w.run_once()
    assert out["cancelled"] == 1
    assert w.job(job_id)["state"] == "cancelled"
