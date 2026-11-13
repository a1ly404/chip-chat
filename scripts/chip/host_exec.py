"""Typed host-exec protocol over ``docs/host-hooks.md`` (engine parity slice H).

The engine does NOT run host commands — that stays plugin-side per the
contract. What the engine owns here is the *typed seam* a host adapter must
implement, plus the dry-run side every caller can rely on:

* ``HostAdapter`` protocol — ``allowed_hooks()``, ``run(hook, args)``;
* ``ManifestAdapter`` — a reference adapter built from a
  ``plugins/*/host-hooks.yaml``-shaped manifest: explicit-deny on every hook
  that is not in the manifest, the contract's guard rule honoured
  (``CHIP_HOST_LOCK_FILE`` present ⇒ exit 3 for ``may_restart`` hooks), and a
  receipt line appended per call in the contract's shape;
* exit codes are the contract's: 0 ok / nothing to do, 1 problem found
  unfixable here, 2 acted, 3 refused by a guard, 64 usage/config error;
* the engine's own adapter calls are ALWAYS dry-run: the engine records a
  receipt and never pretends to act; a real execution path belongs to a
  plugin adapter and is this module's extension point, not an engine
  property; denial never invents a host name.

Flag: ``CHIP_HOST_EXEC`` (default off in repo) gates turn/relay wiring; the
module stays injectable so tests run without network, hostnames or secrets.

Turn/relay seam: :func:`run_turn_request` is the one entry a chat turn (or a
relay command handler) calls to run a host job. It refuses unless
``CHIP_HOST_EXEC`` is on, then hands the request to the job-policy executor
(``policy_exec.run_dev_job``: PM authorize -> bound, capped execute), so every
turn-originated run gets a decision id and a ``policy/exec_receipts.jsonl``
receipt exactly like a dev-bot job. Nothing here widens policy: requester
identity, hard denies, script pins, caps and sudo basis are all the policy's.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

HOST_EXEC_ENV = "CHIP_HOST_EXEC"
RECEIPTS_DIR_ENV = "CHIP_HOST_RECEIPTS_DIR"
LOCK_FILE_ENV = "CHIP_HOST_LOCK_FILE"

# Receipt-shaped refusal used when a caller asks for a hook the manifest
# does not declare (explicit deny; do not leak what exists via timing).
DENY_EXIT = 3
GUARD_EXIT = 3


def host_exec_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """True only when ``CHIP_HOST_EXEC`` is an explicit truthy value."""
    env = environ if environ is not None else os.environ
    return env.get(HOST_EXEC_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class HostResult:
    hook: str
    exit: int
    detail: str
    receipt_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"hook": self.hook, "exit": self.exit, "detail": self.detail, "receipt_path": self.receipt_path}


@runtime_checkable
class HostAdapter(Protocol):
    """Plugin-side contract: allowlisted hook names only, receipts, guard rule.

    ``run`` must follow ``docs/host-hooks.md``: idempotent, no secrets in
    receipts, exit codes 0/1/2/3/64 (3 = refused by a guard), and it appends
    one JSON line per run to ``<CHIP_HOST_RECEIPTS_DIR>/<hook>.jsonl``.
    """

    def allowed_hooks(self) -> frozenset[str]: ...

    def run(self, hook: str, args: Mapping[str, Any] | None = None) -> HostResult: ...


def _append_receipt(receipts_dir: Path, hook: str, exit_code: int, detail: str) -> str:
    receipts_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "hook": hook,
        "exit": int(exit_code),
        "detail": detail[:400],
    }
    path = receipts_dir / f"{hook}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return str(path)


class ManifestAdapter:
    """Reference adapter over an example-shaped host-hooks manifest (dry-run).

    Real implementations come from a plugin (the homelab client ships one).
    This engine-side adapter refuses politely: explicitly deny undeclared
    hooks (exit 3), honour the contract's ``CHIP_HOST_LOCK_FILE`` guard for
    every hook:may_restart=true (exit 3), and do dry-run receipts otherwise
    (exit 0 with an explicit dry-run detail — it never pretends to have
    restarted anything).
    """

    def __init__(
        self,
        manifest: Mapping[str, Any],
        *,
        environ: Mapping[str, str] | None = None,
        receipts_dir: Path | str | None = None,
        dry_run: bool = True,
    ) -> None:
        env = environ if environ is not None else os.environ
        self.dry_run = dry_run
        self.lock_file = str(env.get(LOCK_FILE_ENV, "") or "").strip() or None
        hooks = manifest.get("hooks") or {}
        if not isinstance(hooks, Mapping):
            raise ValueError("manifest hooks must be an object")
        self._hooks: dict[str, Mapping[str, Any]] = {
            str(name): dict(spec or {}) for name, spec in hooks.items() if isinstance(spec, Mapping)
        }
        if receipts_dir is not None:
            self.receipts_dir = Path(receipts_dir)
        else:
            raw = str(env.get(RECEIPTS_DIR_ENV, "") or "").strip()
            self.receipts_dir = Path(raw) if raw else None

    def allowed_hooks(self) -> frozenset[str]:
        return frozenset(self._hooks)

    def _receipts_dir(self) -> Path:
        if self.receipts_dir is None:
            raise ValueError(f"{RECEIPTS_DIR_ENV} unset: receipts need a host path")
        return self.receipts_dir

    def run(self, hook: str, args: Mapping[str, Any] | None = None) -> HostResult:
        args = args or {}
        hook = str(hook or "").strip()
        spec = self._hooks.get(hook)
        if spec is None:
            return HostResult(hook=hook or "(empty)", exit=DENY_EXIT, detail="denied: hook not declared in manifest")
        if bool(spec.get("may_restart")) and self.lock_file and Path(self.lock_file).exists():
            path = _append_receipt(self._receipts_dir(), hook, GUARD_EXIT, "refused: lock file present")
            return HostResult(hook=hook, exit=GUARD_EXIT, detail="refused: lock file present", receipt_path=path)
        if self.dry_run:
            path = _append_receipt(self._receipts_dir(), hook, 0, "dry-run: engine adapter never runs host commands")
            return HostResult(hook=hook, exit=0, detail="dry-run: engine adapter never runs host commands", receipt_path=path)
        raise NotImplementedError("real execution belongs to a plugin adapter; engine stays dry-run")


# ------------------------------------------------------------------ chat turn / relay seam

TURN_PATH = "turn"
TURN_REF_MAX = 64


def _turn_refusal(reason: str, turn_ref: str) -> dict[str, Any]:
    return {"verdict": "refused", "reason": reason, "path": TURN_PATH, "turn_ref": turn_ref, "receipt": None}


def run_turn_request(
    raw: Any,
    *,
    turn_ref: str,
    env: Mapping[str, str] | None = None,
    pack: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    runner: Any = None,
    notifier: Any = None,
) -> dict[str, Any]:
    """Run one host job requested from a chat turn through the job policy.

    ``raw`` is a job-policy request (same shape as a dev-bot job: ``job_id``,
    ``requested_by`` = ``dev:<name>``, ``job_type``, ``command``, ...).
    ``turn_ref`` identifies the turn (room/message id) and is recorded in the
    PM reason so the audit row ties back to the chat. Refuses with no side
    effects when ``CHIP_HOST_EXEC`` is off, the turn ref is empty, or the
    request is not an object. Otherwise returns ``policy_exec.run_dev_job``'s
    result (verdict, decision ticket, and ``receipt`` when it ran) plus
    ``path``/``turn_ref``.
    """
    ref = str(turn_ref or "").strip()[:TURN_REF_MAX]
    gate_env = env if env is not None else os.environ
    if not host_exec_enabled(gate_env):
        return _turn_refusal(f"{HOST_EXEC_ENV} off: turn host-exec disabled", ref)
    if not ref:
        return _turn_refusal("turn_ref required: a turn host-exec must name its turn", ref)
    if not isinstance(raw, Mapping):
        return _turn_refusal("request must be a JSON object", ref)
    from chip import policy_exec as px

    why = str(raw.get("reason") or "").strip()[:160]
    out = px.run_dev_job(
        raw,
        pm_reason=f"chat turn {ref}: {why}" if why else f"chat turn {ref}",
        env=env,
        pack=pack,
        now=now,
        runner=runner,
        notifier=notifier,
    )
    out["path"] = TURN_PATH
    out["turn_ref"] = ref
    return out
