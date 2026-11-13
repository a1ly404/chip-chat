"""Operator ``/run`` chat command (``chip.run_command``): parse, gates, attack cases, policy receipt."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chip import host_exec, job_policy as jp, policy_exec as px, run_command as rc

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
OP = "1001"


@pytest.fixture(autouse=True)
def _clean():
    jp.clear_hooks()
    px.clear_notifiers()
    yield
    jp.clear_hooks()
    px.clear_notifiers()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    op_dir = tmp_path / "operator"
    op_dir.mkdir()
    key = jp.OperatorKey.generate()
    key_file = op_dir / "op.key"
    key_file.write_text(key.seed_hex() + "\n", encoding="utf-8")
    policy = {"job_types": {"diagnostics": {"allow_commands": ["ls"], "allow_targets": ["/srv/app"],
                                            "max_seconds": 60}}}
    pol_file = op_dir / "policy.json"
    pol_file.write_text(json.dumps(policy), encoding="utf-8")
    e = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        jp.FLAG: "1", px.EXEC_FLAG: "1", jp.YOLO_ENV: "0",
        jp.PUBKEY_ENV: key.public.hex(), jp.KEY_FILE_ENV: str(key_file), jp.POLICY_FILE_ENV: str(pol_file),
        host_exec.HOST_EXEC_ENV: "1",
    }
    for k, v in e.items():
        monkeypatch.setenv(k, v)
    for k in ("CHIP_BGJOB_ID", "CHIP_JOB_ACTOR"):
        monkeypatch.delenv(k, raising=False)
    jp.pin_policy(key, env=e, now=NOW)
    return e


def _build_ls(args):
    return {"job_id": "turn-ls-1", "requested_by": "dev:alpha", "job_type": "diagnostics", "action": "list",
            "command": ["ls", "/srv/app"], "target": "/srv/app", "context": "turn",
            "reason": "operator /run ls", "max_seconds": 30, "sudo": True}


JOBS = {
    "ls": rc.JobSpec("ls", [r"[a-z0-9-]{1,16}"], _build_ls, usage="[label]"),
    "evil": rc.JobSpec("evil", [], lambda a: {**_build_ls(a), "target": "/srv/wireguard"}),
}


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, env, timeout_s):
        self.calls.append(list(argv))
        return {"exit": 0, "stdout": "SECRET-STDOUT", "stderr": "", "timed_out": False}


def go(text, *, env=None, author=OP, synthetic=False, allow_synthetic=False, room_allowed=True, runner=None,
       run_turn=None):
    return rc.handle(text, author_id=author, operator_ids=[OP], jobs=JOBS, room_ref="dev",
                     room_allowed=room_allowed, synthetic=synthetic, allow_synthetic=allow_synthetic,
                     deny_tokens=("wireguard", "qbittorrent"), env=env, runner=runner, now=NOW,
                     run_turn=run_turn)


def _boom(*a, **k):
    raise AssertionError("must not reach the policy")


@pytest.mark.parametrize("text", ["hello", "please /run ls", "run ls", "/running ls", "", None,
                                  "the bot said:\n/run ls", "> /run ls x\nok"])
def test_not_a_command_returns_none(text):
    assert go(text, run_turn=_boom) is None


def test_operator_run_writes_receipt_and_summary(env):
    r = Runner()
    out = go("/run ls m5", env=env, runner=r)
    assert "decision dec-" in out and "status ok" in out and "exit 0" in out and "[tool:run-command]" in out
    assert "SECRET-STDOUT" not in out
    assert r.calls == [["ls", "/srv/app"]]
    rows = px.receipts()
    assert len(rows) == 1 and rows[0]["status"] == "ok" and rows[0]["sudo"] is False
    assert rows[0]["decision_id"] in out


def test_non_operator_refused(env):
    out = go("/run ls", env=env, author="2002", run_turn=_boom)
    assert "operator-only" in out and "nothing ran" in out
    assert px.receipts() == [] and jp.read_audit() == []


def test_empty_operator_set_refuses():
    out = rc.handle("/run ls", author_id="", operator_ids=[], jobs=JOBS, room_ref="r", room_allowed=True,
                    run_turn=_boom)
    assert "operator-only" in out


def test_synthetic_refused_even_with_operator_id(env):
    out = go("/run ls", env=env, synthetic=True, run_turn=_boom)
    assert "synthetic" in out


def test_synthetic_allowed_only_when_caller_opts_in(env):
    out = go("/run ls", env=env, synthetic=True, allow_synthetic=True, runner=Runner())
    assert "status ok" in out


def test_room_not_allowed(env):
    assert "not allowed in this room" in go("/run ls", env=env, room_allowed=False, run_turn=_boom)


def test_unknown_job(env):
    out = go("/run rm x", env=env, run_turn=_boom)
    assert "unknown job" in out and "known: evil, ls" in out


@pytest.mark.parametrize("text", ["/run ls wireguard", "/run ls WireGuard-up", "/run ls qbittorrent",
                                  "/run ls x-QBitTorrent"])
def test_deny_tokens_in_args(env, text):
    assert "hard deny" in go(text, env=env, run_turn=_boom)


def test_deny_token_in_built_target(env):
    assert "hard deny: 'wireguard'" in go("/run evil", env=env, run_turn=_boom)


@pytest.mark.parametrize("text", ["/run ls a;id", "/run ls $(id)", "/run ls a|b", "/run ls `id`",
                                  "/run ls ../../etc", "/run ls a b c d e", "/run ls " + "a" * 300,
                                  "/run ls\nid", "/run ls --sudo", "/run ls x y"])
def test_arg_injection_refused(env, text):
    out = go(text, env=env, run_turn=_boom)
    assert "refused" in out and "nothing ran" in out


def test_usage_on_bare_run(env):
    assert "usage" in go("/run", env=env, run_turn=_boom)


def test_host_exec_off_refuses_through_policy_seam(env):
    out = go("/run ls", env={**env, host_exec.HOST_EXEC_ENV: "0"}, runner=Runner())
    assert "refused" in out and "CHIP_HOST_EXEC off" in out
    assert px.receipts() == []


def test_sudo_is_always_stripped(env):
    seen = {}

    def fake(req, **kw):
        seen.update(req)
        return {"verdict": "refused", "reason": "x", "turn_ref": kw["turn_ref"]}

    go("/run ls", env=env, run_turn=fake)
    assert seen["sudo"] is False and seen["command"] == ["ls", "/srv/app"]


def test_summary_for_failed_receipt():
    out = rc.summarize("diag", {"verdict": "allow", "turn_ref": "dev:abc",
                                "receipt": {"decision_id": "dec-1", "status": "failed", "exit": 124,
                                            "seconds": 1.0, "reason": "time cap", "stdout_tail": "x"}})
    assert out.startswith("/run diag: decision dec-1 · status failed · exit 124 · 1.00s · time cap")
    assert "stdout" not in out
