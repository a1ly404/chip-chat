"""Alert remediations (parity D slice 4): pinned alert -> fix-job templates, PM picks, executor runs.

End to end with no human: an alert row lands in a JSONL file, the PM tick picks it, the pinned
remediation turns it into a mutate exec job, the PM grants sudo in YOLO, the executor runs the
hash-bound fix script (fake runner, sudo never really invoked) and writes a receipt.
Keys are generated at runtime; nothing touches a real host.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chip import bg_jobs, bg_picker, cli, job_policy as jp, pm_tick, policy_exec as px, remediations as rem
from chip.config import data_dir

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean():
    jp.clear_hooks()
    px.clear_notifiers()
    yield
    jp.clear_hooks()
    px.clear_notifiers()


class FakeRunner:
    def __init__(self, **res):
        self.calls: list[list[str]] = []
        self.res = {"exit": 0, "stdout": "healthy", "stderr": "", "timed_out": False, **res}

    def __call__(self, argv, env, timeout_s):
        self.calls.append(list(argv))
        return dict(self.res)


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    op_dir = tmp_path / "operator"
    op_dir.mkdir()
    key = jp.OperatorKey.generate()
    key_file = op_dir / "op.key"
    key_file.write_text(key.seed_hex() + "\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    heal = work / "heal.sh"
    heal.write_text('#!/bin/sh\necho "heal $1"\n', encoding="utf-8")
    heal.chmod(0o755)
    sha = hashlib.sha256(heal.read_bytes()).hexdigest()
    policy = {
        "job_types": {
            "container-heal": {
                "allow_commands": [],
                "scripts": {str(heal): sha},
                "allow_targets": ["app-*", "canary"],
                "sudo": "ask",
                "max_seconds": 120,
            }
        },
        "remediations": [
            {
                "id": "container-heal",
                "match": "^container-down:(?P<target>[a-z0-9][a-z0-9_.-]{0,62})$",
                "requested_by": "dev:remediator",
                "job_type": "container-heal",
                "action": "restart-container",
                "command": [str(heal), "{target}"],
                "target": "{target}",
                "script_path": str(heal),
                "sudo": True,
                "max_seconds": 90,
                "reason": "container down: restart and verify health",
            }
        ],
    }
    pol_file = op_dir / "policy.json"
    pol_file.write_text(json.dumps(policy), encoding="utf-8")
    alerts = tmp_path / "probe_alerts.jsonl"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        jp.FLAG: "1",
        px.EXEC_FLAG: "1",
        rem.FLAG: "1",
        jp.YOLO_ENV: "1",
        jp.PUBKEY_ENV: key.public.hex(),
        jp.KEY_FILE_ENV: str(key_file),
        jp.POLICY_FILE_ENV: str(pol_file),
        "CHIP_JOBS": "1",
        "CHIP_PICKER": "1",
        "CHIP_PM_TICK": "1",
        "CHIP_PM_ALERTS_EXTRA_PATHS": str(alerts),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in ("CHIP_BGJOB_ID", "CHIP_JOB_ACTOR"):
        monkeypatch.delenv(k, raising=False)
    jp.pin_policy(key, env=env, now=NOW)
    return {"env": env, "key": key, "pol_file": pol_file, "heal": heal, "sha": sha, "alerts": alerts, "policy": policy}


def _alert(path: Path, key: str, detail: str = "container exited (1)") -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"alert": {"key": key, "detail": detail, "raw": f"{key} {detail}"}, "gate": "probe"}) + "\n")


def _tick(setup, runner):
    orig = px.subprocess_runner
    px.subprocess_runner = runner  # exec_step falls back to the module default runner
    try:
        return pm_tick.tick(NOW, alerts=pm_tick.default_alert_stream(setup["env"]), env=setup["env"])
    finally:
        px.subprocess_runner = orig


# ------------------------------------------------------------------ planning


def test_plan_builds_exact_request_from_pinned_template(setup):
    p = rem.plan({"kind": "alert", "id": "container-down:app-web"}, setup["env"])
    assert p and p["remediation"] == "container-heal"
    r = p["policy_request"]
    assert r["command"] == [str(setup["heal"]), "app-web"] and r["target"] == "app-web"
    assert r["sudo"] is True and r["max_spend_usd"] == 0.0 and r["script_sha256"] == setup["sha"]
    assert r["requested_by"] == "dev:remediator"


@pytest.mark.parametrize(
    "ident",
    ["container-down:-rf", "container-down:a b", "container-down:../etc", "container-down:x;rm", "other:app-web",
     "xcontainer-down:app-web"],
)
def test_unsafe_or_unmatched_ids_plan_nothing(setup, ident):
    assert rem.plan({"kind": "alert", "id": ident}, setup["env"]) is None


def test_match_uses_alert_id_only_not_text(setup):
    item = {"kind": "alert", "id": "fp-1", "title": "container-down:app-web", "detail": "container-down:app-web"}
    assert rem.plan(item, setup["env"]) is None


def test_unpinned_policy_edit_disables_remediations(setup):
    doc = json.loads(setup["pol_file"].read_text())
    doc["remediations"][0]["command"] = ["/bin/sh", "{target}"]
    setup["pol_file"].write_text(json.dumps(doc))
    templates, why = rem.load(setup["env"])
    assert templates == [] and "not pinned" in why


def test_flags_gate_remediation(setup):
    assert rem.enabled(setup["env"])
    for k in (rem.FLAG, jp.FLAG, px.EXEC_FLAG):
        assert not rem.enabled({**setup["env"], k: "0"})


def test_templates_must_request_as_dev_bot(setup):
    doc = json.loads(setup["pol_file"].read_text())
    doc["remediations"][0]["requested_by"] = "pm"
    setup["pol_file"].write_text(json.dumps(doc))
    jp.pin_policy(setup["key"], env=setup["env"], now=NOW)
    assert rem.load(setup["env"])[0] == []


# ------------------------------------------------------------------ picker


def test_picker_queues_mutate_exec_job_for_matching_alert(setup):
    res = bg_picker.pick([{"kind": "alert", "id": "container-down:app-web", "severity": "warning"}], now=NOW, env=setup["env"])
    job = res["enqueued"][0]
    assert job["mode"] == "mutate" and job["max_hops"] == 1 and job["max_spend_usd"] == 0.0
    assert job["params"]["exec"] is True and job["params"]["policy_request"]["job_id"] == job["id"]


def test_picker_stays_read_only_when_flag_off(setup):
    env = {**setup["env"], rem.FLAG: "0"}
    res = bg_picker.pick([{"kind": "alert", "id": "container-down:app-web", "severity": "warning"}], now=NOW, env=env)
    assert res["enqueued"][0]["mode"] == "read"


# ------------------------------------------------------------------ end to end


def test_alert_to_yolo_fix_with_receipt_no_human(setup):
    _alert(setup["alerts"], "container-down:app-web")
    runner = FakeRunner()
    out = _tick(setup, runner)
    assert out["status"] == "ok" and len(out["picked"]) == 1
    assert out["ran"][0]["status"] == "ok", out
    assert runner.calls and runner.calls[0][:3] == ["sudo", "-n", "--"] and runner.calls[0][-1] == "app-web"
    grants = jp.grants()
    assert len(grants) == 1 and grants[0]["requested_by"] == "dev:remediator"
    rec = px.receipts(job_id=out["picked"][0])
    assert rec[-1]["status"] == "ok" and rec[-1]["sudo"] is True and rec[-1]["grant_id"] == grants[0]["id"]
    assert jp.pending_requests() == []  # nobody was asked


def test_alert_without_yolo_asks_operator_and_waits(setup, monkeypatch):
    # Pin the wall clock so the 24h confirm TTL is measured from NOW, not today.
    monkeypatch.setattr(jp, "_now", lambda now=None: (now or NOW).astimezone(timezone.utc))
    env = {**setup["env"], jp.YOLO_ENV: "0"}
    setup["env"].update(env)
    _alert(setup["alerts"], "container-down:app-web")
    runner = FakeRunner()
    out = _tick(setup, runner)
    assert not runner.calls and out["deferred"] and "operator confirm" in out["deferred"][0]["reason"]
    assert len(jp.pending_requests()) == 1


def test_target_outside_policy_is_denied_with_job_failure(setup):
    _alert(setup["alerts"], "container-down:vpn-gw")
    runner = FakeRunner()
    out = _tick(setup, runner)
    assert not runner.calls
    assert out["ran"][0]["status"] == "failed" and "policy deny" in out["ran"][0]["reason"]
    assert any(a.get("verdict") == "deny" for a in jp.read_audit(10))


def test_plugin_hard_deny_beats_yolo(setup):
    jp.register_hard_deny("vpn", lambda r: "vpn is off limits" if r.target == "app-vpn" else None)
    _alert(setup["alerts"], "container-down:app-vpn")
    runner = FakeRunner()
    out = _tick(setup, runner)
    assert not runner.calls and "hard deny" in out["ran"][0]["reason"] and jp.grants() == []


def test_failed_fix_is_a_failed_job_with_receipt(setup):
    _alert(setup["alerts"], "container-down:app-web")
    runner = FakeRunner(exit=3, stdout="still unhealthy")
    out = _tick(setup, runner)
    assert out["ran"][0]["status"] == "failed"
    assert px.receipts(job_id=out["picked"][0])[-1]["status"] == "failed"


def test_same_alert_is_fixed_once_per_day(setup):
    runner = FakeRunner()
    _alert(setup["alerts"], "container-down:app-web")
    _tick(setup, runner)
    _alert(setup["alerts"], "container-down:app-web")
    out = _tick(setup, runner)
    assert out["picked"] == [] and len(runner.calls) == 1


def test_extra_alert_paths_keep_separate_cursors(setup):
    base = pm_tick.default_alerts_path(setup["env"])
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_text(json.dumps({"alert": {"key": "disk-low", "detail": "disk failed"}}) + "\n")
    _alert(setup["alerts"], "container-down:canary")
    stream = pm_tick.default_alert_stream(setup["env"])
    items, info = stream.read()
    assert {i["id"] for i in items} == {"disk-low", "container-down:canary"} and info["lines"] == 2
    stream.commit()
    again, _ = pm_tick.default_alert_stream(setup["env"]).read()
    assert again == []


def test_cli_remediations_plan(setup, capsys):
    rc = cli.main(["policy", "remediations", "--alert-id", "container-down:canary"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["enabled"] and out["plan"]["policy_request"]["target"] == "canary"
