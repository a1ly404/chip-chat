"""Execute job-policy ``allow`` decisions (parity D slice 2).

:mod:`chip.job_policy` decides; this module is the only place an approved job argv runs.

Flow
----
1. :func:`authorize` — the PM evaluates a request (``job_policy.evaluate``). An ``allow`` becomes a
   **decision ticket**: id, request digest, command sha256, sudo flag, basis (rule / YOLO grant /
   none), issue + expiry time, and a ``binding`` sha256 over all of it. The ticket is appended to
   ``policy/decisions.jsonl`` (inside the protected policy store). An ``ask`` posts an operator
   confirm request through the notifier hook (default off). ``deny`` returns the reason.
2. :func:`execute` — runs the request bound to a ticket, after re-checking everything:

   * ``CHIP_JOB_POLICY`` and ``CHIP_POLICY_EXEC`` both on (default off), and not inside a job;
   * the ticket is the one the PM issued (stored row + binding hash match), unexpired
     (``CHIP_POLICY_DECISION_TTL_S``, default 900 s) and unused (single use, ``consumed.jsonl``);
   * the request presented now has the ticket's digest (argv/target/context/script/sudo swaps refuse)
     and the same job id and requester;
   * ``job_policy.recheck``: authority chain, policy pin, hard denies, job-type policy, caps;
   * sudo only when the ticket's basis still holds (``job_policy.sudo_basis_ok``) — an active matching
     operator rule or an unexpired YOLO grant for this exact scope. Never otherwise.

   The argv runs with ``shell=False`` under ``job_policy.job_env`` (no policy controls, no secrets),
   with the per-job time cap and spend cap, and ``sudo -n --`` prepended only for granted sudo.
   Every outcome (ran or refused) writes a receipt bound to the decision id and binding hash to
   ``policy/exec_receipts.jsonl`` (command sha256 and output sha256 + short tail, never raw argv).
3. :func:`run_dev_job` is the dev-bot entry point (authorize → execute); :func:`gate_job` and
   :func:`exec_step` route ``bg_jobs`` mutate jobs through the same path.
"""

from __future__ import annotations

import importlib
import shlex
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from chip import job_policy as jp

EXEC_FLAG = "CHIP_POLICY_EXEC"
NOTIFY_FLAG = "CHIP_POLICY_NOTIFY"
NOTIFY_HOOK_ENV = "CHIP_POLICY_NOTIFY_HOOK"
DECISION_TTL_ENV = "CHIP_POLICY_DECISION_TTL_S"
SUDO_ARGV = ("sudo", "-n", "--")
DEFAULT_SECONDS = 600
TAIL = 400

# runner(argv, env, timeout_s) -> {"exit": int, "stdout": str, "stderr": str, "timed_out": bool, "spend_usd": float}
Runner = Callable[[list[str], dict[str, str], int], dict[str, Any]]
# notifier(message, request_row) -> None (raise on failure)
Notifier = Callable[[str, dict[str, Any]], None]


class ExecRefused(jp.PolicyError):
    """The executor refused to run a decision."""


def exec_enabled(env: Mapping[str, str] | None = None) -> bool:
    return jp._truthy(jp._env(env).get(EXEC_FLAG, "0"))


def decisions_path() -> Path:
    return jp.policy_dir() / "decisions.jsonl"


def consumed_path() -> Path:
    return jp.policy_dir() / "consumed.jsonl"


def receipts_path() -> Path:
    return jp.policy_dir() / "exec_receipts.jsonl"


def notified_path() -> Path:
    return jp.policy_dir() / "notified.jsonl"


# ------------------------------------------------------------------ decision tickets


@dataclass(frozen=True)
class Ticket:
    id: str
    job_id: str
    requested_by: str
    digest: str
    command_sha256: str
    sudo: bool
    rule_id: str
    grant_id: str
    issued_at: str
    expires_at: str
    binding: str = ""

    def body(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("binding")
        return d

    def compute_binding(self) -> str:
        return jp.sha256_text(jp.canon(self.body()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Ticket":
        if not isinstance(raw, Mapping):
            raise ExecRefused("decision ticket must be an object")
        try:
            return cls(**{k: raw[k] for k in cls.__dataclass_fields__})
        except (KeyError, TypeError) as exc:
            raise ExecRefused("malformed decision ticket") from exc


def _stored_ticket(ticket_id: str) -> dict[str, Any] | None:
    for row in jp._jsonl(decisions_path()):
        if row.get("id") == ticket_id:
            return row
    return None


def _consumed(ticket_id: str) -> bool:
    return any(r.get("id") == ticket_id for r in jp._jsonl(consumed_path()))


@dataclass
class Authorization:
    verdict: str
    reason: str
    ticket: Ticket | None = None
    request_id: str = ""
    notify: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"verdict": self.verdict, "reason": self.reason}
        if self.ticket:
            out["ticket"] = self.ticket.to_dict()
        if self.request_id:
            out["request_id"] = self.request_id
        if self.notify:
            out["notify"] = self.notify
        return out


def authorize(
    raw: Mapping[str, Any] | jp.JobRequest,
    *,
    actor: str,
    pm_reason: str = "",
    env: Mapping[str, str] | None = None,
    pack: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    notifier: Notifier | None = None,
) -> Authorization:
    """PM decision → ticket (allow) / operator confirm post (ask) / refusal (deny)."""
    e = jp._env(env)
    n = jp._now(now)
    dec = jp.evaluate(raw, actor=actor, pm_reason=pm_reason, env=e, pack=pack, now=n)
    if dec.verdict == "ask":
        sent = notify_ask(dec.request_id, env=e, now=n, notifier=notifier)
        return Authorization("ask", dec.reason, request_id=dec.request_id, notify=sent)
    if dec.verdict != "allow":
        return Authorization("deny", dec.reason, request_id=dec.request_id)
    req = raw if isinstance(raw, jp.JobRequest) else jp.JobRequest.from_dict(raw)
    ttl = jp._int_env(e, DECISION_TTL_ENV, 900, 10, 3600)
    t = Ticket(
        id="dec-" + uuid.uuid4().hex[:16],
        job_id=req.job_id,
        requested_by=req.requested_by,
        digest=req.digest(),
        command_sha256=req.command_sha256(),
        sudo=req.sudo,
        rule_id=dec.rule_id,
        grant_id=dec.grant_id,
        issued_at=jp._iso(n),
        expires_at=jp._iso(n + timedelta(seconds=ttl)),
    )
    t = Ticket(**{**t.to_dict(), "binding": t.compute_binding()})
    jp._append(decisions_path(), {**t.to_dict(), "actor": actor[:96], "reason": dec.reason})
    return Authorization("allow", dec.reason, ticket=t)


# ------------------------------------------------------------------ execution


def subprocess_runner(argv: list[str], env: dict[str, str], timeout_s: int) -> dict[str, Any]:
    """Default runner: no shell, no stdin, captured output, hard timeout."""
    try:
        cp = subprocess.run(  # argv approved by job policy, shell=False
            argv, env=env, timeout=timeout_s, capture_output=True, text=True, stdin=subprocess.DEVNULL, shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return {"exit": 124, "stdout": out, "stderr": "", "timed_out": True}
    except OSError as exc:
        return {"exit": 127, "stdout": "", "stderr": f"{type(exc).__name__}", "timed_out": False}
    return {"exit": cp.returncode, "stdout": cp.stdout or "", "stderr": cp.stderr or "", "timed_out": False}


def _receipt(ticket: Mapping[str, Any] | None, req: jp.JobRequest | None, status: str, reason: str,
             now: datetime, **extra: Any) -> dict[str, Any]:
    t = ticket or {}
    row: dict[str, Any] = {
        "ts": jp._iso(now),
        "decision_id": str(t.get("id", ""))[:64],
        "binding": str(t.get("binding", ""))[:64],
        "job_id": req.job_id if req else str(t.get("job_id", ""))[:96],
        "requested_by": req.requested_by if req else str(t.get("requested_by", ""))[:96],
        "command_sha256": req.command_sha256() if req else "",
        "sudo": bool(req.sudo) if req else False,
        "rule_id": str(t.get("rule_id", "")),
        "grant_id": str(t.get("grant_id", "")),
        "status": status,
        "reason": reason[:300],
        **extra,
    }
    jp._append(receipts_path(), row)
    return row


def _tail(text: str) -> str:
    text = text or ""
    return text[-TAIL:]


def execute(
    ticket: Mapping[str, Any] | Ticket,
    raw: Mapping[str, Any] | jp.JobRequest,
    *,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Run the request bound to an ``allow`` ticket. Always returns a receipt; refusals have
    ``status="refused"`` and nothing ran."""
    e = jp._env(env)
    n = jp._now(now)
    tdict = ticket.to_dict() if isinstance(ticket, Ticket) else dict(ticket or {})
    req: jp.JobRequest | None = None

    def refuse(why: str) -> dict[str, Any]:
        return _receipt(tdict, req, "refused", why, n)

    if not jp.enabled(e):
        return refuse("job policy disabled")
    if not exec_enabled(e):
        return refuse(f"{EXEC_FLAG} is not enabled")
    for marker in jp.IN_SYSTEM_MARKERS:
        if e.get(marker):
            return refuse(f"executor refused inside a job ({marker} set)")
    try:
        req = raw if isinstance(raw, jp.JobRequest) else jp.JobRequest.from_dict(raw)
    except jp.PolicyError as exc:
        return refuse(f"malformed request: {exc}")
    try:
        t = Ticket.from_dict(tdict)
    except ExecRefused as exc:
        return refuse(str(exc))
    stored = _stored_ticket(t.id)
    if stored is None:
        return refuse("unknown decision (not issued by the PM)")
    if Ticket.from_dict(stored).to_dict() != t.to_dict() or t.compute_binding() != t.binding:
        return refuse("decision ticket altered (binding mismatch)")
    if jp._parse(t.expires_at) <= n:
        return refuse("decision expired")
    if _consumed(t.id):
        return refuse("decision already used (single use)")
    if req.digest() != t.digest or req.command_sha256() != t.command_sha256:
        return refuse("request does not match the decision (argv/target/scope changed)")
    if req.job_id != t.job_id or req.requested_by != t.requested_by or req.sudo != t.sudo:
        return refuse("request does not match the decision (job/requester/sudo changed)")
    why, spec, rows = jp.recheck(req, env=e, now=n)
    if why:
        return refuse(f"recheck: {why}")
    if req.sudo:
        if str(spec.get("sudo", "ask")).lower() == "never":
            return refuse(f"sudo never allowed for {req.job_type}")
        why = jp.sudo_basis_ok(req, rule_id=t.rule_id, grant_id=t.grant_id, rows=rows, env=e, now=n)
        if why:
            return refuse(f"sudo: {why}")
    elif t.rule_id or t.grant_id:
        return refuse("decision basis does not match a non-sudo request")
    # claim (single use) before running so a crash cannot replay it
    jp._append(consumed_path(), {"id": t.id, "ts": jp._iso(n), "job_id": req.job_id})
    max_secs = min(int(spec.get("max_seconds", DEFAULT_SECONDS)), jp.HARD_MAX_SECONDS)
    secs = max(1, min(req.max_seconds or max_secs, max_secs))
    spend_cap = float(req.max_spend_usd or 0.0)
    argv = list(req.command)
    if req.sudo:
        argv = [*SUDO_ARGV, *argv]
    child_env = jp.job_env(e, job_id=req.job_id, actor=req.requested_by)
    run = runner or subprocess_runner
    started = time.monotonic()
    try:
        res = run(argv, child_env, secs) or {}
    except Exception as exc:  # runner errors fail the job, never raise
        return _receipt(tdict, req, "failed", f"runner error: {type(exc).__name__}", n, seconds_cap=secs)
    took = round(time.monotonic() - started, 3)
    spend = max(0.0, float(res.get("spend_usd", 0.0) or 0.0))
    out, err = str(res.get("stdout", "")), str(res.get("stderr", ""))
    code = int(res.get("exit", 1))
    if res.get("timed_out"):
        status, reason = "failed", "time cap"
    elif spend > spend_cap + 1e-9:
        status, reason = "failed", "spend cap"
    elif code != 0:
        status, reason = "failed", f"exit {code}"
    else:
        status, reason = "ok", ""
    return _receipt(
        tdict, req, status, reason, n,
        exit=code, seconds=took, seconds_cap=secs, spend_usd=spend, spend_cap_usd=spend_cap,
        stdout_sha256=jp.sha256_text(out), stdout_tail=_tail(out), stderr_tail=_tail(err),
    )


def run_dev_job(
    raw: Mapping[str, Any],
    *,
    pm_reason: str = "",
    env: Mapping[str, str] | None = None,
    pack: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    runner: Runner | None = None,
    notifier: Notifier | None = None,
) -> dict[str, Any]:
    """Dev-bot entry point: the PM authorizes the request, then the executor runs it."""
    auth = authorize(raw, actor=jp.PM, pm_reason=pm_reason, env=env, pack=pack, now=now, notifier=notifier)
    out = auth.to_dict()
    if auth.verdict == "allow" and auth.ticket:
        out["receipt"] = execute(auth.ticket, raw, env=env, now=now, runner=runner)
    return out


# ------------------------------------------------------------------ operator confirm notifications

_NOTIFIERS: list[Notifier] = []


def register_notifier(fn: Notifier) -> None:
    """Plugins register the operator channel here (e.g. a chat webhook)."""
    if not callable(fn):
        raise TypeError("notifier must be callable")
    _NOTIFIERS.append(fn)


def clear_notifiers() -> None:
    """Test helper."""
    _NOTIFIERS.clear()


def _env_notifiers(env: Mapping[str, str]) -> list[Notifier]:
    out: list[Notifier] = []
    for spec in [s.strip() for s in str(env.get(NOTIFY_HOOK_ENV, "")).split(",") if s.strip()]:
        mod, _, attr = spec.partition(":")
        fn = getattr(importlib.import_module(mod), attr or "notify")
        out.append(fn)
    return out


def confirm_message(row: Mapping[str, Any]) -> str:
    r = row.get("request") or {}
    cmd = shlex.join([str(x) for x in r.get("command") or []])
    if len(cmd) > 300:
        cmd = cmd[:297] + "..."
    return (
        f"Operator confirm needed: {r.get('requested_by')} asks for sudo on job {r.get('job_id')} "
        f"({r.get('job_type')}): {r.get('action')} on {r.get('target') or '-'} [context {r.get('context') or '-'}]\n"
        f"Command: {cmd}\n"
        f"Why (PM): {str(row.get('pm_reason') or '-')[:300]}\n"
        f"Expires: {row.get('expires_at')}\n"
        f"Confirm: `chip policy confirm {row.get('id')}` (runs once + saves an exact-scope rule) "
        f"or decline: `chip policy decline {row.get('id')}`"
    )


def notify_ask(
    request_id: str,
    *,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
    notifier: Notifier | None = None,
) -> str:
    """Post one confirm request to the operator channel. Returns ``off`` (CHIP_POLICY_NOTIFY not on),
    ``no-notifier``, ``duplicate`` (already posted), ``sent`` or ``error``. Never raises."""
    e = jp._env(env)
    if not jp._truthy(e.get(NOTIFY_FLAG, "0")):
        return "off"
    if any(r.get("id") == request_id for r in jp._jsonl(notified_path())):
        return "duplicate"
    row = next((p for p in jp.pending_requests(now) if p.get("id") == request_id), None)
    if row is None:
        return "not-pending"
    try:
        fns = [notifier] if notifier else [*_NOTIFIERS, *_env_notifiers(e)]
    except Exception:
        return "error"
    if not fns:
        return "no-notifier"
    msg = confirm_message(row)
    try:
        for fn in fns:
            fn(msg, dict(row))
    except Exception:
        return "error"
    jp._append(notified_path(), {"id": request_id, "ts": jp._iso(jp._now(now))})
    return "sent"


# ------------------------------------------------------------------ bg_jobs routing


def gate_job(
    job: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
    notifier: Notifier | None = None,
) -> dict[str, Any]:
    """Policy gate for a ``bg_jobs`` mutate job. The request lives in ``params.policy_request``;
    its ``job_id`` is forced to the bg job id. Returns ``{"verdict", "reason", ...}``; on allow a
    fresh ticket (any ticket already on the job record is ignored)."""
    params = job.get("params") or {}
    raw = params.get("policy_request")
    if not isinstance(raw, Mapping):
        return {"verdict": "deny", "reason": "mutate job needs params.policy_request under CHIP_JOB_POLICY"}
    raw = {**dict(raw), "job_id": str(job.get("id", ""))}
    auth = authorize(raw, actor=jp.PM, pm_reason=str(params.get("pm_reason") or ""), env=env, now=now, notifier=notifier)
    return auth.to_dict()


def exec_step(*, env: Mapping[str, str] | None = None, runner: Runner | None = None,
              clock: Callable[[], datetime] | None = None):
    """bg_jobs step for an exec job: runs ``params.policy_request`` under the job's ticket once."""

    def _step(job: dict[str, Any], hop: int, prior: list[dict[str, Any]]) -> dict[str, Any]:
        ticket = (job.get("policy") or {}).get("ticket")
        raw = {**dict((job.get("params") or {}).get("policy_request") or {}), "job_id": str(job.get("id", ""))}
        n = clock() if clock else None
        rec = execute(ticket or {}, raw, env=env, now=n, runner=runner)
        if rec["status"] != "ok":
            raise ExecRefused(f"{rec['status']}: {rec['reason']}")
        return {"output": f"exec ok (decision {rec['decision_id']}, exit {rec.get('exit')}): {rec.get('stdout_tail', '')}"[:2000],
                "spend_usd": float(rec.get("spend_usd", 0.0)), "done": True}

    return _step


def receipts(limit: int = 50) -> list[dict[str, Any]]:
    return jp._jsonl(receipts_path())[-max(1, int(limit)):]


__all__ = [
    "Authorization", "ExecRefused", "Ticket", "authorize", "clear_notifiers", "confirm_message", "exec_enabled",
    "exec_step", "execute", "gate_job", "notify_ask", "receipts", "register_notifier", "run_dev_job",
    "subprocess_runner",
]
