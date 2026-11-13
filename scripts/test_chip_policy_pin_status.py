"""`chip policy status` reports whether the policy pin is CURRENT, not merely present (fail closed)."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from chip import cli, job_policy as jp, pm_summary

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_hooks():
    jp.clear_hooks()
    yield
    jp.clear_hooks()


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    op_dir = tmp_path / "operator"
    op_dir.mkdir()
    key = jp.OperatorKey.generate()
    key_file = op_dir / "op.key"
    key_file.write_text(key.seed_hex() + "\n", encoding="utf-8")
    pol_file = op_dir / "policy.json"
    pol_file.write_text(json.dumps({"job_types": {"build": {"allow_commands": ["make"]}}}), encoding="utf-8")
    env = {
        jp.FLAG: "1",
        jp.YOLO_ENV: "0",
        jp.PUBKEY_ENV: key.public.hex(),
        jp.KEY_FILE_ENV: str(key_file),
        jp.POLICY_FILE_ENV: str(pol_file),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return {"env": env, "key": key, "policy_file": pol_file}


def _sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def test_unpinned_is_not_current_and_not_armed(setup):
    st = jp.status(setup["env"])
    assert st["pin"] == "unpinned" and st["pin_current"] is False and st["armed"] is False
    assert st["pinned"] is False and st["pinned_sha16"] is None
    assert st["policy_sha16"] == _sha16(setup["policy_file"])
    assert "not pinned" in st["pin_warning"]


def test_current_pin_is_armed(setup):
    jp.pin_policy(setup["key"], env=setup["env"], now=NOW)
    st = jp.status(setup["env"])
    assert st["pin"] == "current" and st["pin_current"] is True and st["armed"] is True
    assert st["pinned_sha16"] == st["policy_sha16"] == _sha16(setup["policy_file"])
    assert "pin_warning" not in st


def test_stale_pin_is_present_but_not_healthy(setup):
    jp.pin_policy(setup["key"], env=setup["env"], now=NOW)
    old = _sha16(setup["policy_file"])
    setup["policy_file"].write_text(json.dumps({"job_types": {"build": {"allow_commands": ["make", "dd"]}}}))
    st = jp.status(setup["env"])
    assert st["pinned"] is True  # present ...
    assert st["pin"] == "stale" and st["pin_current"] is False and st["armed"] is False  # ... but not current
    assert st["pinned_sha16"] == old and st["policy_sha16"] == _sha16(setup["policy_file"]) != old
    assert "STALE" in st["pin_warning"] and old in st["pin_warning"]
    # the status agrees with the decision path: a stale pin denies
    d = jp.evaluate({"job_id": "j", "requested_by": "dev:a", "job_type": "build", "action": "build",
                     "command": ["make"], "sudo": False}, actor="pm", env=setup["env"], now=NOW)
    assert d.verdict == "deny" and "not pinned" in d.reason
    jp.pin_policy(setup["key"], env=setup["env"], now=NOW)
    assert jp.status(setup["env"])["pin"] == "current"


def test_missing_policy_file_is_policy_error(setup):
    jp.pin_policy(setup["key"], env=setup["env"], now=NOW)
    setup["policy_file"].unlink()
    st = jp.status(setup["env"])
    assert st["pin"] == "policy_error" and st["pin_current"] is False and st["armed"] is False
    assert "not found" in st["policy_error"]


def test_no_policy_file_configured_is_not_current(setup):
    env = {k: v for k, v in setup["env"].items() if k != jp.POLICY_FILE_ENV}
    st = jp.status(env)
    assert st["pin"] == "no_policy_file" and st["pin_current"] is False and st["armed"] is False


def test_flag_off_is_never_armed(setup):
    jp.pin_policy(setup["key"], env=setup["env"], now=NOW)
    st = jp.status({**setup["env"], jp.FLAG: "0"})
    assert st["pin_current"] is True and st["armed"] is False


def test_tampered_store_is_not_current(setup):
    jp.pin_policy(setup["key"], env=setup["env"], now=NOW)
    path = jp.authority_path()
    lines = path.read_text().splitlines()
    row = json.loads(lines[0])
    row["payload"]["sha256"] = "0" * 64
    path.write_text(json.dumps(row) + "\n" + "\n".join(lines[1:]))
    st = jp.status(setup["env"])
    assert st["authority"].startswith("tampered") and st["pin"] == "tampered"
    assert st["pin_current"] is False and st["armed"] is False


def test_cli_status_prints_pin_and_strict_exit(setup, capsys):
    jp.pin_policy(setup["key"], env=setup["env"], now=NOW)
    assert cli.main(["policy", "status", "--strict"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["pin"] == "current" and out["pin_current"] is True
    setup["policy_file"].write_text(json.dumps({"job_types": {}}))
    assert cli.main(["policy", "status"]) == 0  # default stays read-only rc 0 ...
    cap = capsys.readouterr()
    assert json.loads(cap.out)["pin_current"] is False
    assert "WARNING: policy pin is STALE" in cap.err  # ... but says so loudly
    assert cli.main(["policy", "status", "--strict"]) == 6  # fail closed for callers that gate on it


def test_pm_summary_waits_on_stale_pin(setup):
    jp.pin_policy(setup["key"], env=setup["env"], now=NOW)
    s = pm_summary.build(date(2026, 11, 10), env=setup["env"], jobs=[])
    assert not any("policy pin" in w for w in s["waiting"])
    setup["policy_file"].write_text(json.dumps({"job_types": {}}))
    s = pm_summary.build(date(2026, 11, 10), env=setup["env"], jobs=[])
    assert any("policy pin is STALE" in w for w in s["waiting"])


def test_pin_waiting_quiet_when_policy_off(setup):
    assert jp.pin_waiting({**setup["env"], jp.FLAG: "0"}) == []
    env = {k: v for k, v in setup["env"].items() if k != jp.POLICY_FILE_ENV}
    assert jp.pin_waiting(env) == []
