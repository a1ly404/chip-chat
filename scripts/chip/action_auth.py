"""Exact-action authorization for the host-action broker (parity wave, RAZ-16).

JEV decides whether an action may proceed; the broker decides WHAT executes and
under WHICH authorization. This module is that enforcement core: an approval
record binds an immutable artifact hash, a canonical digest of validated
arguments, one target, one execution identity, an expiry and a single-use flag.

Fail-closed laws enforced here (all refusals are deterministic, never advisory):

- the artifact bytes present at execution time must hash to the approved
  artifact digest (no check-then-replace gap: the same call that authorizes
  re-hashes what will actually run);
- arguments must SHA-match the approved canonical-JSON digest and carry exactly
  the declared argument keys (unknown shape = refusal);
- target and execution identity must equal the record's, byte for byte;
- expiry, unknown records, replayed single-use records and missing config all
  refuse;
- a record found ``consumed`` but without a verified execution result is
  terminal ``consumed-uncertain``: retries ALWAYS need a fresh record (the
  crash window between consume and exec cannot be papered over by cached
  validity);
- approvers must be external GO references; the names in
  ``CHIP_ACTION_AUTH_BOT_IDS`` (comma-separated operator identity list) are
  refused as approvers at load time, so a bot approval can never exist;
- records MAY carry a ``policy_version`` string and a ``job_id`` string. When
  the operator pins ``CHIP_ACTION_AUTH_POLICY_VERSION`` in the environment,
  a record with a different (or absent) policy version refuses at load: the
  effective policy can only move by an operator changing the pinned
  environment, never by editing a record;
- ``CHIP_ACTION_AUTH_PM_ID`` names the authenticated PM principal id. A
  record whose ``approver`` satisfies the same external-GO law as above may
  cite that principal; the PM stays an approver of concrete jobs, not a
  writer of policy: policy version and scope widening remain operator-pinned.

The module creates NOTHING on its own: approval records are provisioned by the
operator (configuration step, outside the runtime bot path) and only read here.
Default-off overall: callers opt in explicitly; an unset/absent record directory
refuses everything (fail-closed), it never falls back to allow.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip.config import data_dir

DIRNAME = "action_auth"


class AuthorizationRefused(RuntimeError):
    """Deterministic refusal with a machine-readable ``code``."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"authorization refused: {code}: {detail}")
        self.code = code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def args_digest(args: dict[str, Any]) -> str:
    """Canonical argument digest: sorted keys, compact separators, UTF-8."""
    canonical = json.dumps(
        args, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return sha256_bytes(canonical.encode("utf-8"))


@dataclass
class ActionRecord:
    record_id: str
    artifact_sha256: str
    args_digest: str
    arg_keys: tuple[str, ...]
    target: str
    execution_identity: str
    expires_iso: str
    single_use: bool
    approver: str
    go_reference: str
    policy_version: str = ""
    job_id: str = ""

    @classmethod
    def from_file(cls, path: Path) -> "ActionRecord":
        raw = json.loads(path.read_text(encoding="utf-8"))
        for field in (
            "record_id",
            "artifact_sha256",
            "args_digest",
            "arg_keys",
            "target",
            "execution_identity",
            "expires_iso",
            "single_use",
            "approver",
            "go_reference",
        ):
            if field not in raw:
                raise AuthorizationRefused(
                    "malformed-record", f"{path.name} missing {field}"
                )
        if not re.fullmatch(r"[0-9a-f]{64}", raw["artifact_sha256"]):
            raise AuthorizationRefused(
                "malformed-record", f"{path.name} artifact_sha256 shape"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", raw["args_digest"]):
            raise AuthorizationRefused(
                "malformed-record", f"{path.name} args_digest shape"
            )
        return cls(
            record_id=str(raw["record_id"]),
            artifact_sha256=str(raw["artifact_sha256"]),
            args_digest=str(raw["args_digest"]),
            arg_keys=tuple(sorted(str(k) for k in raw["arg_keys"])),
            target=str(raw["target"]),
            execution_identity=str(raw["execution_identity"]),
            expires_iso=str(raw["expires_iso"]),
            single_use=bool(raw["single_use"]),
            approver=str(raw["approver"]),
            go_reference=str(raw["go_reference"]),
            policy_version=str(raw.get("policy_version", "")).strip(),
            job_id=str(raw.get("job_id", "")).strip(),
        )


def policy_version(environ: dict[str, str] | None = None) -> str:
    """The operator-pinned policy version, or '' when unpinned."""
    env = environ if environ is not None else os.environ
    return str(env.get("CHIP_ACTION_AUTH_POLICY_VERSION", "")).strip()


def pm_principal_id(environ: dict[str, str] | None = None) -> str:
    """The authenticated PM principal id, or '' when the principal is not armed."""
    env = environ if environ is not None else os.environ
    return str(env.get("CHIP_ACTION_AUTH_PM_ID", "")).strip()


def records_dir(environ: dict[str, str] | None = None) -> Path:
    env = environ if environ is not None else os.environ
    raw = env.get("CHIP_ACTION_AUTH_DIR", "").strip()
    return Path(raw) if raw else data_dir() / DIRNAME


def state_dir(environ: dict[str, str] | None = None) -> Path:
    """The consumable execution-state directory (writes land here, ONLY here).

    Default: the records dir (unchanged behavior). When the operator sets
    CHIP_ACTION_AUTH_STATE_DIR, the single-use consume/executed bookkeeping
    lives there instead — this is the boundary that lets a consumer hold the
    records store READ-ONLY (records = pre-provisioned approvals written by
    a separate provisioning identity) while still consuming records.
    """
    env = environ if environ is not None else os.environ
    raw = str(env.get("CHIP_ACTION_AUTH_STATE_DIR", "")).strip()
    return Path(raw) if raw else records_dir(environ)


def status_path(environ: dict[str, str] | None = None) -> Path:
    return state_dir(environ) / "consumed_state.json"


def _bot_ids(environ: dict[str, str] | None = None) -> set[str]:
    env = environ if environ is not None else os.environ
    return {
        x.strip()
        for x in env.get("CHIP_ACTION_AUTH_BOT_IDS", "").split(",")
        if x.strip()
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload_io = json.dumps(payload, sort_keys=True, indent=1).encode("utf-8")
    tmp.write_bytes(payload_io)
    for _ in range(2):  # Windows rename can transiently lose to a reader
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(tmp, path)


class ActionBroker:
    """Loads operator-provisioned records and authorizes exact actions."""

    def __init__(self, environ: dict[str, str] | None = None) -> None:
        self.environ = environ if environ is not None else os.environ
        self.dir = records_dir(self.environ)
        self.state_path = status_path(self.environ)

    # -- loading ----------------------------------------------------------
    def load_record(self, record_id: str) -> tuple[ActionRecord, dict[str, Any]]:
        state = self._state()
        path = self.dir / f"{record_id}.json"
        if not path.is_file():
            raise AuthorizationRefused("unknown-record", record_id)
        record = ActionRecord.from_file(path)
        if record.record_id != record_id:
            raise AuthorizationRefused("malformed-record", "id field mismatch")
        if record.approver in _bot_ids(self.environ):
            raise AuthorizationRefused(
                "self-approval", f"approver {record.approver!r} is a runtime identity"
            )
        if not record.go_reference.strip():
            raise AuthorizationRefused("self-approval", "empty go_reference")
        # Policy pin (operator environment, read-only to the lane): when the
        # operator pins a policy version, every record must name THAT version;
        # widening scope is always an operator action, never a record edit.
        pinned = policy_version(self.environ)
        if pinned and record.policy_version != pinned:
            raise AuthorizationRefused(
                "policy-version-mismatch",
                f"record {record.policy_version!r} != operator-pinned {pinned!r}",
            )
        entry = state.get("records", {}).get(record_id, {})
        # Load-time expiry: an expired record never authorizes anything.
        if _now() >= _iso(record.expires_iso):
            raise AuthorizationRefused("expired", record.expires_iso)
        if (
            record.single_use
            and entry.get("consumed_ts")
            and not entry.get("executed_ts")
        ):
            raise AuthorizationRefused(
                "consumed-uncertain",
                "record consumed without a verified execution result; fresh approval required",
            )
        if record.single_use and entry.get("executed_ts"):
            raise AuthorizationRefused("replayed", f"{record_id} already executed")
        return record, entry

    def _state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {"records": {}}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    # -- consume (before exec) + execute bookkeeping -----------------------
    def consume(self, record: ActionRecord) -> None:
        state = self._state()
        entry = state["records"].setdefault(record.record_id, {})
        if entry.get("consumed_ts") and record.single_use:
            raise AuthorizationRefused(
                "replayed", f"{record.record_id} consumed concurrently"
            )
        entry["consumed_ts"] = _now().isoformat()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.state_path, state)

    def mark_executed(self, record: ActionRecord, exit_code: int) -> None:
        state = self._state()
        entry = state["records"].setdefault(record.record_id, {})
        entry["executed_ts"] = _now().isoformat()
        entry["exit_code"] = int(exit_code)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.state_path, state)

    # -- authorization -----------------------------------------------------
    def authorize(
        self,
        record_id: str,
        *,
        artifact_sha256: str,
        args: dict[str, Any],
        target: str,
        identity: str,
    ) -> ActionRecord:
        """Verify EVERYTHING, then consume the record.

        Returns the record only when every deterministic check passes; the
        caller MUST run the verified artifact immediately after this returns
        and call ``mark_executed`` with the observed exit code.
        """
        record, _entry = self.load_record(record_id)
        if artifact_sha256 != record.artifact_sha256:
            raise AuthorizationRefused("artifact-drift", record_id)
        if tuple(sorted(args)) != record.arg_keys:
            raise AuthorizationRefused(
                "args-shape", f"keys {sorted(args)} != declared {list(record.arg_keys)}"
            )
        if args_digest(args) != record.args_digest:
            raise AuthorizationRefused("args-drift", record_id)
        if target != record.target:
            raise AuthorizationRefused(
                "target-mismatch", f"{target!r} != {record.target!r}"
            )
        if identity != record.execution_identity:
            raise AuthorizationRefused(
                "identity-mismatch", f"{identity!r} != {record.execution_identity!r}"
            )
        self.consume(record)
        return record

    def reconcile(self, record_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Crash reconciliation: report consumed-but-unverified records."""
        state = self._state()
        out = []
        for rid, entry in sorted(state.get("records", {}).items()):
            if record_ids is not None and rid not in record_ids:
                continue
            if entry.get("consumed_ts") and not entry.get("executed_ts"):
                out.append({"record_id": rid, "status": "consumed-uncertain", **entry})
        return out
