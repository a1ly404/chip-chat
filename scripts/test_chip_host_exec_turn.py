"""Turn/relay host-exec seam: ``host_exec.run_turn_request`` routes through the job policy.

A chat turn's host job must get a PM decision and an executor receipt like any dev-bot job, and
must do nothing at all while ``CHIP_HOST_EXEC`` is off. Keys are generated at runtime; nothing
real runs except a tiny temp script in the hash-bound case.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chip import host_exec, job_policy as jp, policy_exec as px

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean():
    jp.clear_hooks()
    px.clear_notifiers()
    yield
    jp.clear_hooks()
    px.clear_notifiers()


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    op_dir = tmp_path / "operator"
    op_dir.mkdir()
    key = jp.OperatorKey.generate()
    key_file = op_dir / "op.key"
    key_file.write_text(key.seed_hex() + "\n", encoding="utf-8")
    script = tmp_path / "diag.sh"
    script.write_text("#!/bin/sh\necho \"diag actor=$CHIP_JOB_ACTOR\"\n", encoding="utf-8")
    script.chmod(0o755)
    sha = hashlib.sha256(script.read_bytes()).hexdigest()
    policy = {
        "job_types": {
            "diagnostics": {"allow_commands": ["ls"], "allow_targets": ["/srv/app"], "max_seconds": 60},
            "scripted": {"allow_commands": [], "scripts": {str(script): sha}, "max_seconds": 10},
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
        host_exec.HOST_EXEC_ENV: "1",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in ("CHIP_BGJOB_ID", "CHIP_JOB_ACTOR"):
        monkeypatch.delenv(k, raising=False)
    jp.pin_policy(key, env=env, now=NOW)
    return {"env": env, "script": script, "sha": sha}


def req(**over):
    base = {
        "job_id": "t1",
        "requested_by": "dev:alpha",
        "job_type": "diagnostics",
        "action": "list",
        "command": ["ls", "/srv/app"],
        "target": "/srv/app",
        "context": "turn",
        "sudo": False,
        "reason": "operator asked for a listing",
        "max_seconds": 30,
    }
    base.update(over)
    return base


class FakeRunner:
    def __init__(self, **res):
        self.calls: list[list[str]] = []
        self.res = {"exit": 0, "stdout": "ok", "stderr": "", "timed_out": False, **res}

    def __call__(self, argv, env, timeout_s):
        self.calls.append(list(argv))
        return dict(self.res)


def test_flag_off_refuses_without_side_effects(setup):
    env = {**setup["env"], host_exec.HOST_EXEC_ENV: "0"}
    r = FakeRunner()
    out = host_exec.run_turn_request(req(), turn_ref="room-1/msg-1", env=env, now=NOW, runner=r)
    assert out["verdict"] == "refused" and "CHIP_HOST_EXEC off" in out["reason"]
    assert out["receipt"] is None and out["path"] == "turn"
    assert r.calls == [] and px.receipts() == [] and jp.read_audit() == []


def test_flag_unset_is_off(setup):
    env = {k: v for k, v in setup["env"].items() if k != host_exec.HOST_EXEC_ENV}
    out = host_exec.run_turn_request(req(), turn_ref="r/m", env=env, now=NOW, runner=FakeRunner())
    assert out["verdict"] == "refused" and px.receipts() == []


@pytest.mark.parametrize("ref", ["", "   ", None])
def test_turn_ref_required(setup, ref):
    out = host_exec.run_turn_request(req(), turn_ref=ref, env=setup["env"], now=NOW, runner=FakeRunner())
    assert out["verdict"] == "refused" and "turn_ref" in out["reason"] and px.receipts() == []


def test_non_object_request_refused(setup):
    out = host_exec.run_turn_request(["ls"], turn_ref="r/m", env=setup["env"], now=NOW, runner=FakeRunner())
    assert out["verdict"] == "refused" and px.receipts() == []


def test_allowed_turn_job_writes_policy_receipt(setup):
    r = FakeRunner()
    out = host_exec.run_turn_request(req(), turn_ref="room-1/msg-9", env=setup["env"], now=NOW, runner=r)
    assert out["verdict"] == "allow", out
    assert out["path"] == "turn" and out["turn_ref"] == "room-1/msg-9"
    rec = out["receipt"]
    assert rec["status"] == "ok" and rec["decision_id"] == out["ticket"]["id"]
    assert r.calls == [["ls", "/srv/app"]]
    rows = px.receipts()
    assert [x["decision_id"] for x in rows] == [out["ticket"]["id"]]


def test_real_hash_bound_script_from_turn(setup):
    s = str(setup["script"])
    r = req(job_type="scripted", command=[s], script_path=s, script_sha256=setup["sha"], target="", max_seconds=5)
    out = host_exec.run_turn_request(r, turn_ref="r/m", env=setup["env"], now=NOW)
    assert out["verdict"] == "allow", out
    assert out["receipt"]["status"] == "ok" and "diag actor=dev:alpha" in out["receipt"]["stdout_tail"]


@pytest.mark.parametrize(
    "over",
    [
        {"requested_by": "pm"},                     # only dev bots request
        {"requested_by": "operator"},
        {"command": ["rm", "-rf", "/srv/app"]},     # not in allow_commands
        {"job_type": "unknown"},
        {"target": "/etc"},
    ],
)
def test_policy_still_decides_for_turns(setup, over):
    r = FakeRunner()
    out = host_exec.run_turn_request(req(**over), turn_ref="r/m", env=setup["env"], now=NOW, runner=r)
    assert out["verdict"] == "deny", out
    assert r.calls == [] and "receipt" not in out


def test_sudo_from_turn_asks_operator_when_not_yolo(setup):
    r = FakeRunner()
    out = host_exec.run_turn_request(req(sudo=True), turn_ref="r/m", env=setup["env"], now=NOW, runner=r)
    assert out["verdict"] == "ask" and r.calls == [] and "receipt" not in out
    # the operator confirm row says the ask came from a chat turn
    assert any("chat turn r/m" in json.dumps(p) for p in jp.pending_requests(NOW))


def test_policy_exec_flag_off_still_refuses(setup):
    env = {**setup["env"], px.EXEC_FLAG: "0"}
    r = FakeRunner()
    out = host_exec.run_turn_request(req(), turn_ref="r/m", env=env, now=NOW, runner=r)
    assert r.calls == []
    rec = out.get("receipt")
    assert out["verdict"] != "allow" or (rec and rec["status"] == "refused")
