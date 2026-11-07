"""Offline tests for chip.action_auth (exact-action broker authorization).

All records are fixtures written into a tmp data dir by the TEST (operator
provisioning is a configuration step, never a runtime path here). No network,
no live exec, no flag flips.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "chip"))

from chip import action_auth as aa  # noqa: E402

ARTIFACT = b"#!/bin/sh\necho exact-action-canary\n"


def _env(tmp_path: Path, bot_ids: str = "") -> dict[str, str]:
    return {
        "CHIP_ACTION_AUTH_DIR": str(tmp_path / "action_auth"),
        "CHIP_ACTION_AUTH_BOT_IDS": bot_ids,
        "CHIP_CHAT_DATA_DIR": str(tmp_path / "dd"),  # fallback dir unused when explicit
    }


def _record_yaml(tmp_path: Path, **overrides) -> dict:
    valid_until = datetime.now(timezone.utc) + timedelta(hours=1)
    rec = {
        "record_id": overrides.get("record_id", "rec-1"),
        "artifact_sha256": overrides.get("artifact_sha256", hashlib.sha256(ARTIFACT).hexdigest()),
        "args_digest": overrides.get("args_digest", aa.args_digest({"container": "wave-canary"})),
        "arg_keys": overrides.get("arg_keys", ["container"]),
        "target": overrides.get("target", "wave-canary"),
        "execution_identity": overrides.get("execution_identity", "homelab-executor"),
        "expires_iso": overrides.get("expires_iso", valid_until.isoformat()),
        "single_use": overrides.get("single_use", True),
        "approver": overrides.get("approver", "Ally (GO 2026-11-07 reply, live-wave GO)"),
        "go_reference": overrides.get("go_reference", "ALLY-GO-live-wave-2026-11-07"),
    }
    (tmp_path / "action_auth").mkdir(parents=True, exist_ok=True)
    (tmp_path / "action_auth" / f"{rec['record_id']}.json").write_text(
        json.dumps(rec, indent=1), encoding="utf-8"
    )
    return rec


def _authorize(env, rid: str = "rec-1", **kw):
    broker = aa.ActionBroker(environ=env)
    return broker.authorize(
        rid,
        artifact_sha256=hashlib.sha256(ARTIFACT).hexdigest(),
        args=kw.get("args", {"container": "wave-canary"}),
        target=kw.get("target", "wave-canary"),
        identity=kw.get("identity", "homelab-executor"),
    )


def test_principal_path_authorizes_and_consumes(tmp_path):
    env = _env(tmp_path)
    _record_yaml(tmp_path)
    broker = aa.ActionBroker(environ=env)
    rec = broker.load_record("rec-1")[0]  # pre-check load only
    assert rec.single_use is True
    broker.consume(rec)  # lifecycle contract used by the glue
    state = json.loads((tmp_path / "action_auth" / "consumed_state.json").read_text())
    assert state["records"]["rec-1"]["consumed_ts"]
    # one consumption is all a single-use record gets until reconciled/executed
    with pytest.raises(aa.AuthorizationRefused) as err:
        aa.ActionBroker(environ=env).load_record("rec-1")
    assert err.value.code == "consumed-uncertain"


def test_authorize_consume_before_return(tmp_path):
    env = _env(tmp_path)
    _record_yaml(tmp_path)
    rec = _authorize(env)
    assert rec.record_id == "rec-1"
    state = json.loads((tmp_path / "action_auth" / "consumed_state.json").read_text())
    assert "consumed_ts" in state["records"]["rec-1"] and "executed_ts" not in state["records"]["rec-1"]


def test_unknown_record_refuses(tmp_path):
    env = _env(tmp_path)
    with pytest.raises(aa.AuthorizationRefused) as err:
        aa.ActionBroker(environ=env).load_record("ghost")
    assert err.value.code == "unknown-record"


def test_expired_record_refuses(tmp_path):
    env = _env(tmp_path)
    _record_yaml(tmp_path, expires_iso=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    with pytest.raises(aa.AuthorizationRefused) as err:
        _authorize(env)
    assert err.value.code == "expired"


def test_artifact_drift_refuses(tmp_path):
    env = _env(tmp_path)
    _record_yaml(tmp_path, artifact_sha256=hashlib.sha256(b"tampered").hexdigest())
    with pytest.raises(aa.AuthorizationRefused) as err:
        _authorize(env)
    assert err.value.code == "artifact-drift"


def test_args_sha_drift_refuses(tmp_path):
    env = _env(tmp_path)
    _record_yaml(tmp_path)
    with pytest.raises(aa.AuthorizationRefused) as err:
        _authorize(env, args={"container": "OTHER"})
    assert err.value.code == "args-drift"


def test_args_unknown_shape_refuses(tmp_path):
    env = _env(tmp_path)
    _record_yaml(tmp_path)
    with pytest.raises(aa.AuthorizationRefused) as err:
        _authorize(env, args={"container": "wave-canary", "extra": 1})
    assert err.value.code == "args-shape"


def test_target_mismatch_refuses(tmp_path):
    env = _env(tmp_path)
    _record_yaml(tmp_path)
    with pytest.raises(aa.AuthorizationRefused) as err:
        _authorize(env, target="something-else")
    assert err.value.code == "target-mismatch"


def test_identity_mismatch_refuses(tmp_path):
    env = _env(tmp_path)
    _record_yaml(tmp_path)
    with pytest.raises(aa.AuthorizationRefused) as err:
        _authorize(env, identity="root@host")
    assert err.value.code == "identity-mismatch"


def test_replayed_single_use_refuses(tmp_path):
    env = _env(tmp_path)
    _record_yaml(tmp_path)
    _authorize(env)  # consumes the single-use record
    # retry of the same authorization: consumed but never verified -> uncertain
    with pytest.raises(aa.AuthorizationRefused) as err:
        _authorize(env)
    assert err.value.code == "consumed-uncertain"


def test_self_approval_refused(tmp_path):
    env = _env(tmp_path, bot_ids="bot-ocgo")
    _record_yaml(tmp_path, approver="bot-ocgo")
    with pytest.raises(aa.AuthorizationRefused) as err:
        aa.ActionBroker(environ=env).load_record("rec-1")
    assert err.value.code == "self-approval"


def test_empty_go_reference_refused(tmp_path):
    env = _env(tmp_path)
    _record_yaml(tmp_path, go_reference=" ")
    with pytest.raises(aa.AuthorizationRefused) as err:
        aa.ActionBroker(environ=env).load_record("rec-1")
    assert err.value.code == "self-approval"


def test_crash_reconcile_reports_consumed_uncertain(tmp_path):
    env = _env(tmp_path)
    rec = _record_yaml(tmp_path)
    broker = aa.ActionBroker(environ=env)
    broker.consume(broker.load_record("rec-1")[0])
    # process crash simulation: consumed, never marked executed
    rows = aa.ActionBroker(environ=env).reconcile(["rec-1"])
    assert rows == [{"record_id": "rec-1", "status": "consumed-uncertain",
                     "consumed_ts": rows[0]["consumed_ts"]}]
    with pytest.raises(aa.AuthorizationRefused) as err:
        aa.ActionBroker(environ=env).load_record("rec-1")
    assert err.value.code == "consumed-uncertain"


def test_non_single_use_record_reusable(tmp_path):
    env = _env(tmp_path)
    rec = _record_yaml(tmp_path, single_use=False)
    broker = aa.ActionBroker(environ=env)
    broker.consume(broker.load_record("rec-1")[0])
    broker.consume(broker.load_record("rec-1")[0])  # idempotent while not single_use
    assert rec["single_use"] is False
    assert json.loads((tmp_path / "action_auth" / "consumed_state.json").read_text())[
        "records"]["rec-1"]["consumed_ts"]


def test_executed_record_replay_refuses(tmp_path):
    env = _env(tmp_path)
    _record_yaml(tmp_path)
    broker = aa.ActionBroker(environ=env)
    rec = broker.load_record("rec-1")[0]
    broker.consume(rec)
    broker.mark_executed(rec, 0)
    with pytest.raises(aa.AuthorizationRefused) as err:
        broker.load_record("rec-1")
    assert err.value.code == "replayed"


def test_malformed_record_refuses(tmp_path):
    env = _env(tmp_path)
    _record_yaml(tmp_path)
    p = tmp_path / "action_auth" / "rec-1.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data.pop("go_reference")
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(aa.AuthorizationRefused) as err:
        aa.ActionBroker(environ=env).load_record("rec-1")
    assert err.value.code == "malformed-record"


def test_missing_records_dir_refuses(tmp_path):
    env = _env(tmp_path)
    # no action_auth dir at all: fail-closed, never allow
    with pytest.raises(aa.AuthorizationRefused) as err:
        aa.ActionBroker(environ=env).load_record("rec-1")
    assert err.value.code == "unknown-record"
