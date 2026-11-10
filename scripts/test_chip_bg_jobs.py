"""Parity P1.4: background agent jobs with hop/spend/daily/concurrency caps (mock-only)."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chip import bg_jobs, routines

T0 = datetime(2026, 3, 8, 9, 30, tzinfo=timezone.utc)
ON = {"CHIP_JOBS": "1"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    for k in ("CHIP_JOBS", "CHIP_ROUTINES", "CHIP_JOBS_MAX_HOPS", "CHIP_JOBS_MAX_SPEND_USD", "CHIP_JOBS_DAILY_MAX",
              "CHIP_JOBS_DAILY_SPEND_USD", "CHIP_JOBS_READ_CONCURRENCY"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def _env(**kw: str) -> dict[str, str]:
    return {**ON, **kw}


def test_enqueue_persists_and_defaults(_data_dir: Path) -> None:
    doc = bg_jobs.enqueue("summarise the queue", now=T0, job_id="j1", env=ON)
    assert (_data_dir / "bg_jobs" / "j1.json").is_file()
    assert doc["status"] == "queued" and doc["mode"] == "read"
    assert doc["max_hops"] == 8 and doc["max_spend_usd"] == 0.25
    assert bg_jobs.load("j1")["prompt"] == "summarise the queue"
    with pytest.raises(FileExistsError):
        bg_jobs.enqueue("again", now=T0, job_id="j1", env=ON)


@pytest.mark.parametrize(
    "kw",
    [
        {"prompt": ""},
        {"mode": "write"},
        {"max_hops": 0},
        {"max_hops": 9},  # above configured ceiling (8)
        {"max_spend_usd": 0.26},
        {"max_spend_usd": -1},
        {"job_id": "../escape"},
    ],
)
def test_enqueue_refusals(kw: dict) -> None:
    args = {"prompt": "p", **kw}
    prompt = args.pop("prompt")
    with pytest.raises(ValueError):
        bg_jobs.enqueue(prompt, now=T0, env=ON, **args)


def test_caps_env_clamped_and_bad_values_fall_back() -> None:
    c = bg_jobs.caps({"CHIP_JOBS_MAX_HOPS": "999", "CHIP_JOBS_MAX_SPEND_USD": "nope", "CHIP_JOBS_READ_CONCURRENCY": "0"})
    assert c["max_hops"] == bg_jobs.HARD_MAX_HOPS
    assert c["max_spend_usd"] == 0.25
    assert c["read_concurrency"] == 1


def test_run_refused_when_flag_off() -> None:
    bg_jobs.enqueue("p", now=T0, job_id="j1", env={})
    with pytest.raises(bg_jobs.JobsDisabled):
        bg_jobs.run_pending(T0, env={})
    assert bg_jobs.load("j1")["status"] == "queued"


def test_mock_job_runs_with_receipts() -> None:
    bg_jobs.enqueue("p", now=T0, job_id="j1", params={"mock_hops": 3}, env=ON)
    res = bg_jobs.run_pending(T0, env=ON)
    job = res["ran"][0]
    assert job["status"] == "ok" and job["reason"] == ""
    assert [h["hop"] for h in job["hops"]] == [0, 1, 2]
    assert job["hops"][-1]["output"] == "mock: hop 3/3 for job j1"
    assert bg_jobs.load("j1")["status"] == "ok"
    # Nothing left to run.
    assert bg_jobs.run_pending(T0, env=ON)["ran"] == []


def test_hop_cap_fails_job() -> None:
    bg_jobs.enqueue("p", now=T0, job_id="j1", max_hops=2, env=ON)
    calls: list[int] = []

    def never_done(job: dict, hop: int, prior: list) -> dict:
        calls.append(hop)
        return {"output": "still going", "done": False}

    job = bg_jobs.run_pending(T0, never_done, env=ON)["ran"][0]
    assert calls == [0, 1]
    assert (job["status"], job["reason"]) == ("failed", "hop cap")


def test_spend_cap_checked_before_each_hop() -> None:
    bg_jobs.enqueue("p", now=T0, job_id="j1", max_spend_usd=0.10, env=ON)
    calls: list[int] = []

    def spender(job: dict, hop: int, prior: list) -> dict:
        calls.append(hop)
        return {"output": "x", "spend_usd": 0.05, "done": False}

    job = bg_jobs.run_pending(T0, spender, env=ON)["ran"][0]
    # Two hops reach exactly the ceiling; a third hop never starts.
    assert calls == [0, 1]
    assert (job["status"], job["reason"]) == ("failed", "spend cap")
    assert job["spend_usd"] == pytest.approx(0.10)


def test_spend_overshoot_fails_job() -> None:
    bg_jobs.enqueue("p", now=T0, job_id="j1", max_spend_usd=0.10, env=ON)
    job = bg_jobs.run_pending(T0, lambda j, h, p: {"spend_usd": 0.5, "done": True}, env=ON)["ran"][0]
    assert (job["status"], job["reason"]) == ("failed", "spend cap")


def test_step_error_fails_job_not_tick() -> None:
    bg_jobs.enqueue("p", now=T0, job_id="a", env=ON)
    bg_jobs.enqueue("p", now=T0 + timedelta(seconds=1), job_id="b", env=ON)

    def step(job: dict, hop: int, prior: list) -> dict:
        if job["id"] == "a":
            raise RuntimeError("boom")
        return {"done": True}

    ran = {j["id"]: j for j in bg_jobs.run_pending(T0, step, env=ON)["ran"]}
    assert ran["a"]["status"] == "failed" and ran["a"]["reason"] == "step error: RuntimeError"
    assert "boom" in ran["a"]["hops"][0]["error"]
    assert ran["b"]["status"] == "ok"


def test_daily_job_cap_defers_and_resets_next_day() -> None:
    env = _env(CHIP_JOBS_DAILY_MAX="2")
    for i in range(3):
        bg_jobs.enqueue("p", now=T0 + timedelta(seconds=i), job_id=f"j{i}", env=env)
    res = bg_jobs.run_pending(T0, env=env)
    assert [j["id"] for j in res["ran"]] == ["j0", "j1"]
    assert res["deferred"] == [{"id": "j2", "reason": "daily job cap"}]
    assert bg_jobs.load("j2")["status"] == "queued"
    res = bg_jobs.run_pending(T0 + timedelta(days=1), env=env)
    assert [j["id"] for j in res["ran"]] == ["j2"]


def test_daily_spend_cap_reserves_per_job_ceiling() -> None:
    env = _env(CHIP_JOBS_DAILY_SPEND_USD="0.50")
    for i in range(3):
        bg_jobs.enqueue("p", now=T0 + timedelta(seconds=i), job_id=f"j{i}", max_spend_usd=0.25, env=env)
    res = bg_jobs.run_pending(T0, env=env)
    # Reservations: two jobs x 0.25 fill the 0.50 day even though the mock spends 0.
    assert [j["id"] for j in res["ran"]] == ["j0", "j1"]
    assert res["deferred"] == [{"id": "j2", "reason": "daily spend cap"}]
    # Actual spend (0) is what counts once finished, so j2 can start on the next tick.
    assert [j["id"] for j in bg_jobs.run_pending(T0, env=env)["ran"]] == ["j2"]


def test_select_mutate_serialized_and_read_slots() -> None:
    env = _env(CHIP_JOBS_READ_CONCURRENCY="2")
    rows = [
        {"id": "m0", "mode": "mutate", "status": "running", "max_spend_usd": 0.0, "started_at": "2026-03-08T09:00:00Z"},
        {"id": "r0", "mode": "read", "status": "running", "max_spend_usd": 0.0, "started_at": "2026-03-08T09:00:00Z"},
        {"id": "m1", "mode": "mutate", "status": "queued", "max_spend_usd": 0.0},
        {"id": "r1", "mode": "read", "status": "queued", "max_spend_usd": 0.0},
        {"id": "r2", "mode": "read", "status": "queued", "max_spend_usd": 0.0},
    ]
    picked = bg_jobs.select(T0, rows, env=env)
    assert [j["id"] for j in picked["start"]] == ["r1"]
    assert {d["id"]: d["reason"] for d in picked["deferred"]} == {
        "m1": "mutate serialized: another mutating job is running",
        "r2": "read concurrency cap",
    }
    # With no mutate running, only the first queued mutate is picked.
    rows2 = [{"id": f"m{i}", "mode": "mutate", "status": "queued", "max_spend_usd": 0.0} for i in range(3)]
    picked2 = bg_jobs.select(T0, rows2, env=env)
    assert [j["id"] for j in picked2["start"]] == ["m0"]
    assert [d["id"] for d in picked2["deferred"]] == ["m1", "m2"]


def test_reads_run_in_parallel_up_to_cap_and_mutates_never_overlap() -> None:
    env = _env(CHIP_JOBS_READ_CONCURRENCY="3")
    for i in range(5):
        bg_jobs.enqueue("p", now=T0 + timedelta(seconds=i), job_id=f"r{i}", env=env)
    for i in range(2):
        bg_jobs.enqueue("p", now=T0 + timedelta(seconds=10 + i), job_id=f"m{i}", mode="mutate", env=env)
    lock = threading.Lock()
    active = {"read": 0, "mutate": 0}
    peak = {"read": 0, "mutate": 0}

    def step(job: dict, hop: int, prior: list) -> dict:
        with lock:
            active[job["mode"]] += 1
            peak[job["mode"]] = max(peak[job["mode"]], active[job["mode"]])
        time.sleep(0.05)
        with lock:
            active[job["mode"]] -= 1
        return {"done": True}

    first = bg_jobs.run_pending(T0, step, env=env)
    assert sorted(j["id"] for j in first["ran"]) == ["m0", "r0", "r1", "r2"]
    assert peak["read"] == 3 and peak["mutate"] == 1
    reasons = {d["id"]: d["reason"] for d in first["deferred"]}
    assert reasons["m1"].startswith("mutate serialized") and reasons["r3"] == "read concurrency cap"
    second = bg_jobs.run_pending(T0, step, env=env)
    assert sorted(j["id"] for j in second["ran"]) == ["m1", "r3", "r4"]
    assert peak["mutate"] == 1


def test_cancel_only_queued() -> None:
    bg_jobs.enqueue("p", now=T0, job_id="j1", env=ON)
    assert bg_jobs.cancel("j1", now=T0)["status"] == "cancelled"
    with pytest.raises(ValueError):
        bg_jobs.cancel("j1", now=T0)
    assert bg_jobs.run_pending(T0, env=ON)["ran"] == []


def test_recover_marks_stale_running_failed() -> None:
    bg_jobs.enqueue("p", now=T0, job_id="j1", env=ON)
    doc = bg_jobs.load("j1")
    doc.update(status="running", started_at="2026-03-08T09:30:00Z")
    bg_jobs._save(doc)
    assert bg_jobs.recover(T0 + timedelta(minutes=10)) == []
    assert bg_jobs.recover(T0 + timedelta(hours=2)) == ["j1"]
    assert (bg_jobs.load("j1")["status"], bg_jobs.load("j1")["reason"]) == ("failed", "interrupted")


def test_routine_tick_dispatches_through_jobs() -> None:
    env = {"CHIP_ROUTINES": "1", **ON}
    routines.add("digest", schedule="every:1h", kind="summary", prompt="daily digest", now=T0)
    now = T0 + timedelta(hours=1)
    rows = routines.tick(now, bg_jobs.routine_runner(env=env, clock=lambda: now), env=env)
    assert rows[0]["status"] == "ok" and rows[0]["summary"].startswith("job ")
    jobs = bg_jobs.list_all()
    assert len(jobs) == 1 and jobs[0]["source"] == "routine:digest" and jobs[0]["status"] == "ok"
    assert jobs[0]["prompt"] == "daily digest"


def test_routine_via_jobs_deferred_by_cap_is_failed_run() -> None:
    env = {"CHIP_ROUTINES": "1", **ON, "CHIP_JOBS_DAILY_MAX": "0"}
    routines.add("digest", schedule="every:1h", kind="summary", now=T0)
    now = T0 + timedelta(hours=1)
    rows = routines.tick(now, bg_jobs.routine_runner(env=env, clock=lambda: now), env=env)
    assert rows[0]["status"] == "failed" and "daily job cap" in rows[0]["summary"]
    assert bg_jobs.list_all()[0]["status"] == "cancelled"


def _cli(args: list[str], env_extra: dict[str, str], data: Path) -> subprocess.CompletedProcess:
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("CHIP_JOBS") and k != "CHIP_ROUTINES"}
    env.update({"CHIP_CHAT_DATA_DIR": str(data), **env_extra})
    root = Path(__file__).resolve().parent
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "chip", *args], capture_output=True, text=True, env=env, timeout=60)


def test_cli_transcript(_data_dir: Path) -> None:
    r = _cli(["bgjob", "enqueue", "--prompt", "look around", "--job-id", "c1", "--mock"], {}, _data_dir)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["status"] == "queued"
    r = _cli(["bgjob", "enqueue", "--prompt", "x", "--max-hops", "99", "--mock"], {}, _data_dir)
    assert r.returncode == 2 and "max_hops" in r.stderr
    r = _cli(["bgjob", "run", "--mock"], {}, _data_dir)
    assert r.returncode == 3 and "REFUSED" in r.stderr
    r = _cli(["bgjob", "run", "--mock"], {"CHIP_JOBS": "1"}, _data_dir)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["ran"][0]["status"] == "ok"
    r = _cli(["bgjob", "show", "c1", "--mock"], {}, _data_dir)
    assert json.loads(r.stdout)["hops"][0]["output"].startswith("mock:")
    r = _cli(["bgjob", "list", "--status", "ok", "--mock"], {}, _data_dir)
    assert [j["id"] for j in json.loads(r.stdout)] == ["c1"]
    r = _cli(["bgjob", "caps", "--mock"], {"CHIP_JOBS": "1"}, _data_dir)
    out = json.loads(r.stdout)
    assert out["enabled"] is True and out["today"]["started"] == 1
    r = _cli(["bgjob", "cancel", "c1", "--mock"], {}, _data_dir)
    assert r.returncode == 2


def test_cli_routine_run_due_via_jobs(_data_dir: Path) -> None:
    r = _cli(["routine", "add", "tiny", "--schedule", "every:1m", "--kind", "digest", "--mock"], {}, _data_dir)
    assert r.returncode == 0, r.stderr
    path = _data_dir / "routines" / "tiny.json"
    doc = json.loads(path.read_text())
    doc["next_run"] = "2000-01-01T00:00:00Z"
    path.write_text(json.dumps(doc))
    r = _cli(["routine", "run-due", "--via-jobs", "--mock"], {"CHIP_ROUTINES": "1"}, _data_dir)
    assert r.returncode == 3 and "CHIP_JOBS" in r.stderr
    r = _cli(["routine", "run-due", "--via-jobs", "--mock"], {"CHIP_ROUTINES": "1", "CHIP_JOBS": "1"}, _data_dir)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)[0]["status"] == "ok"
    assert json.loads(_cli(["bgjob", "list", "--mock"], {}, _data_dir).stdout)[0]["source"] == "routine:tiny"
