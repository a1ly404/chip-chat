"""Policy executor hardening (parity D slice 3).

* ``chip policy receipts|receipt`` CLI and executor receipts in the PM daily summary.
* Process-group kill on the time cap: a real child that spawns a grandchild is killed as a group,
  and background stragglers are swept when the job exits.
* Hash-bound scripts run from a private copy whose hash is verified on the copy (closes the
  hash-then-exec window); an edit landing after the recheck is refused and nothing runs.
Keys are generated at runtime; sudo is never invoked.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from chip import cli, job_policy as jp, pm_summary, policy_exec as px

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
POSIX = os.name == "posix"


@pytest.fixture(autouse=True)
def _clean():
    jp.clear_hooks()
    px.clear_notifiers()
    yield
    jp.clear_hooks()
    px.clear_notifiers()


def _write_script(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    op_dir = tmp_path / "operator"
    op_dir.mkdir()
    key = jp.OperatorKey.generate()
    key_file = op_dir / "op.key"
    key_file.write_text(key.seed_hex() + "\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    show = work / "show.sh"
    show_sha = _write_script(show, 'echo "self=$0"\n')
    policy = {
        "job_types": {
            "diagnostics": {"allow_commands": ["ls"], "allow_targets": ["/srv/app"], "max_seconds": 300},
            "scripted": {"allow_commands": [], "scripts": {str(show): show_sha}, "max_seconds": 30},
        }
    }
    pol_file = op_dir / "policy.json"
    pol_file.write_text(json.dumps(policy), encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        jp.FLAG: "1",
        px.EXEC_FLAG: "1",
        jp.YOLO_ENV: "0",
        jp.PUBKEY_ENV: key.public.hex(),
        jp.KEY_FILE_ENV: str(key_file),
        jp.POLICY_FILE_ENV: str(pol_file),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in ("CHIP_BGJOB_ID", "CHIP_JOB_ACTOR"):
        monkeypatch.delenv(k, raising=False)
    jp.pin_policy(key, env=env, now=NOW)
    return {"env": env, "show": show, "show_sha": show_sha, "work": work}


def req(**over):
    base = {
        "job_id": "j1", "requested_by": "dev:alpha", "job_type": "diagnostics", "action": "list",
        "command": ["ls", "/srv/app"], "target": "/srv/app", "context": "repo-x", "sudo": False,
        "reason": "look", "max_seconds": 60, "max_spend_usd": 0.0,
    }
    base.update(over)
    return base


def script_req(setup, **over):
    return req(job_type="scripted", command=[str(setup["show"])], script_path=str(setup["show"]),
               script_sha256=setup["show_sha"], target="", max_seconds=10, **over)


class Capture:
    """Runner that inspects the argv it is handed (the private copy) without running it."""

    def __init__(self, want_sha: str):
        self.want_sha = want_sha
        self.seen: list[dict] = []

    def __call__(self, argv, env, timeout_s):
        p = Path(argv[0])
        info = {"argv": list(argv), "exists": p.exists()}
        if p.exists():
            info["sha"] = hashlib.sha256(p.read_bytes()).hexdigest()
            info["dir_mode"] = stat.S_IMODE(p.parent.stat().st_mode)
            info["file_mode"] = stat.S_IMODE(p.stat().st_mode)
        self.seen.append(info)
        return {"exit": 0, "stdout": "ok", "stderr": "", "timed_out": False}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover
        return True
    try:  # a zombie awaiting reaping is dead for our purposes
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def _wait_dead(pid: int, secs: float = 3.0) -> bool:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _read_pid(path: Path, secs: float = 3.0) -> int:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(0.02)
    raise AssertionError("grandchild pid never written")


# ------------------------------------------------------------------ process-group kill


@pytest.mark.skipif(not POSIX, reason="process groups are POSIX-only")
def test_time_cap_kills_grandchild(tmp_path: Path):
    pidfile = tmp_path / "gc.pid"
    # child shell spawns a grandchild sleeper, records its pid, then waits forever
    script = f'sleep 30 & echo $! > "{pidfile}"; wait'
    started = time.monotonic()
    res = px.subprocess_runner(["sh", "-c", script], {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}, 1)
    took = time.monotonic() - started
    assert res["timed_out"] is True and res["exit"] == 124
    assert took < 6, took  # did not wait on the grandchild holding the pipes
    gc = _read_pid(pidfile)
    assert _wait_dead(gc), f"grandchild {gc} survived the time cap"


@pytest.mark.skipif(not POSIX, reason="process groups are POSIX-only")
def test_background_straggler_swept_after_exit(tmp_path: Path):
    pidfile = tmp_path / "bg.pid"
    script = f'sleep 30 >/dev/null 2>&1 & echo $! > "{pidfile}"; echo done'
    res = px.subprocess_runner(["sh", "-c", script], {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}, 10)
    assert res["exit"] == 0 and not res["timed_out"] and "done" in res["stdout"]
    assert _wait_dead(_read_pid(pidfile)), "background straggler outlived the job"


@pytest.mark.skipif(not POSIX, reason="process groups are POSIX-only")
def test_runner_child_gets_own_session(tmp_path: Path):
    res = px.subprocess_runner(
        [sys.executable, "-c", "import os; print(os.getsid(0), os.getpgid(0), os.getpid())"],
        {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}, 10,
    )
    sid, pgid, pid = (int(x) for x in res["stdout"].split())
    assert sid == pid == pgid and sid != os.getsid(0)


def test_runner_missing_binary_is_127():
    res = px.subprocess_runner(["/nonexistent/chip-no-such-bin"], {"PATH": "/usr/bin:/bin"}, 5)
    assert res["exit"] == 127 and not res["timed_out"]


# ------------------------------------------------------------------ private script copy


def test_script_runs_from_private_copy(setup):
    r = script_req(setup)
    a = px.authorize(r, actor="pm", env=setup["env"], now=NOW)
    assert a.verdict == "allow", a.reason
    cap = Capture(setup["show_sha"])
    rec = px.execute(a.ticket, r, env=setup["env"], now=NOW, runner=cap)
    assert rec["status"] == "ok" and rec["private_copy"] is True
    seen = cap.seen[0]
    copy = Path(seen["argv"][0])
    assert copy != setup["show"] and seen["exists"] and seen["sha"] == setup["show_sha"]
    if POSIX:
        assert seen["dir_mode"] == 0o700 and seen["file_mode"] == 0o500
    assert not copy.exists() and not copy.parent.exists()  # cleaned up after the run


@pytest.mark.skipif(not POSIX, reason="needs /bin/sh")
def test_real_run_reports_copy_path_not_original(setup):
    r = script_req(setup)
    a = px.authorize(r, actor="pm", env=setup["env"], now=NOW)
    rec = px.execute(a.ticket, r, env=setup["env"], now=NOW)
    assert rec["status"] == "ok", rec
    assert "self=" in rec["stdout_tail"] and str(setup["show"]) not in rec["stdout_tail"]
    assert "chip-exec-" in rec["stdout_tail"]


def test_edit_after_recheck_refused_on_copy(setup, monkeypatch: pytest.MonkeyPatch):
    """An edit landing between the recheck hash and exec must not run (the old hash-then-exec window)."""
    r = script_req(setup)
    a = px.authorize(r, actor="pm", env=setup["env"], now=NOW)
    real = jp.recheck

    def racing_recheck(*args, **kw):
        out = real(*args, **kw)  # passes on the original bytes
        setup["show"].write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")  # attacker swaps after
        return out

    monkeypatch.setattr(jp, "recheck", racing_recheck)
    cap = Capture(setup["show_sha"])
    rec = px.execute(a.ticket, r, env=setup["env"], now=NOW, runner=cap)
    assert rec["status"] == "refused" and "private copy" in rec["reason"]
    assert cap.seen == []  # nothing ran
    # the decision was claimed: a retry is a replay
    again = px.execute(a.ticket, r, env=setup["env"], now=NOW, runner=cap)
    assert again["status"] == "refused" and "single use" in again["reason"]


def test_private_copy_helper_mismatch_cleans_up(tmp_path: Path):
    s = tmp_path / "x.sh"
    good = _write_script(s, "echo hi\n")
    copy, d = px.private_script_copy(str(s), good)
    assert copy.read_bytes() == s.read_bytes()
    import shutil

    shutil.rmtree(d)
    before = set(Path(os.environ.get("TMPDIR", "/tmp")).glob("chip-exec-*")) if POSIX else set()
    with pytest.raises(px.ExecRefused):
        px.private_script_copy(str(s), "0" * 64)
    with pytest.raises(px.ExecRefused):
        px.private_script_copy(str(s), "")
    with pytest.raises(px.ExecRefused):
        px.private_script_copy(str(tmp_path / "missing.sh"), good)
    if POSIX:
        assert set(Path(os.environ.get("TMPDIR", "/tmp")).glob("chip-exec-*")) <= before


def test_non_script_jobs_do_not_copy(setup):
    a = px.authorize(req(), actor="pm", env=setup["env"], now=NOW)
    seen = []
    rec = px.execute(a.ticket, req(), env=setup["env"], now=NOW,
                     runner=lambda argv, env, t: seen.append(argv) or {"exit": 0, "stdout": "", "stderr": ""})
    assert rec["status"] == "ok" and rec["private_copy"] is False and seen[0] == ["ls", "/srv/app"]


# ------------------------------------------------------------------ receipts CLI + summary


def _ok_runner(argv, env, t):
    return {"exit": 0, "stdout": "fine", "stderr": "", "timed_out": False}


def test_receipts_filters_and_lookup(setup):
    a1 = px.authorize(req(job_id="ja"), actor="pm", env=setup["env"], now=NOW)
    px.execute(a1.ticket, req(job_id="ja"), env=setup["env"], now=NOW, runner=_ok_runner)
    px.execute(a1.ticket, req(job_id="ja"), env=setup["env"], now=NOW, runner=_ok_runner)  # replay → refused
    a2 = px.authorize(req(job_id="jb"), actor="pm", env=setup["env"], now=NOW)
    px.execute(a2.ticket, req(job_id="jb"), env=setup["env"], now=NOW,
               runner=lambda *x: {"exit": 3, "stdout": "", "stderr": "bad"})
    assert [r["status"] for r in px.receipts()] == ["ok", "refused", "failed"]
    assert [r["job_id"] for r in px.receipts(status="failed")] == ["jb"]
    assert len(px.receipts(job_id="ja")) == 2 and len(px.receipts(limit=1)) == 1
    assert [r["status"] for r in px.receipt(a1.ticket.id)] == ["ok", "refused"]
    assert px.receipt("dec-nope") == []
    day = px.day_receipts("2026-11-10T00:00:00Z", "2026-11-11T00:00:00Z")
    assert day["counts"] == {"failed": 1, "ok": 1, "refused": 1} and len(day["problems"]) == 2
    assert px.day_receipts("2026-11-11T00:00:00Z", "2026-11-12T00:00:00Z")["counts"] == {}


def test_cli_receipts_and_receipt(setup, capsys):
    a = px.authorize(req(), actor="pm", env=setup["env"], now=NOW)
    px.execute(a.ticket, req(), env=setup["env"], now=NOW, runner=_ok_runner)
    capsys.readouterr()
    assert cli.main(["policy", "receipts", "--limit", "5"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and rows[-1]["decision_id"] == a.ticket.id
    assert "/srv/app" not in json.dumps(rows)  # receipts carry hashes, not raw argv
    assert cli.main(["policy", "receipts", "--status", "failed"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert cli.main(["policy", "receipt", a.ticket.id]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "ok"
    assert cli.main(["policy", "receipt", "dec-missing"]) == 3


def test_pm_summary_includes_executor_receipts(setup):
    a = px.authorize(req(), actor="pm", env=setup["env"], now=NOW)
    px.execute(a.ticket, req(), env=setup["env"], now=NOW, runner=_ok_runner)
    px.execute(a.ticket, req(), env=setup["env"], now=NOW, runner=_ok_runner)  # refused replay
    env = {"CHIP_PM_SUMMARY_TZ": "UTC"}
    s = pm_summary.build(date(2026, 11, 10), env=env, jobs=[])
    assert s["executor"]["counts"] == {"ok": 1, "refused": 1}
    assert s["refusals"].get("executor: run refused") == 1
    text = pm_summary.render(s)
    assert "**Executor runs** (ok 1, refused 1)" in text and "single use" in text
    quiet = pm_summary.build(date(2026, 11, 9), env=env, jobs=[])
    assert quiet["executor"]["counts"] == {} and "Executor runs" not in pm_summary.render(quiet)
