"""Scoped GO apply path — behind ``CHIP_SCOPED_GO_ENABLED`` (default off)."""

from __future__ import annotations

import hashlib
import os
from typing import Any

from chip import go_proposals, go_scope, go_verify, runbooks

GO_WRITE_WIRED = False  # set True when CHIP_SCOPED_GO_ENABLED at import (tests reload module)


class GoWriteNotWiredError(RuntimeError):
    """Raised when scoped GO apply is disabled."""


_BLOCKED_ACTIONS = frozenset(
    {"media-delete", "media-transcode", "streaming-restart", "credential-flip", "send", "spend"}
)
_BLOCKED_RUNBOOKS = frozenset(
    {"media-transcode", "streaming-restart", "credential-flip", "media-delete"}
)


def scoped_go_enabled(environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return env.get("CHIP_SCOPED_GO_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def _refresh_wired_flag(environ: dict[str, str] | None = None) -> bool:
    global GO_WRITE_WIRED
    GO_WRITE_WIRED = scoped_go_enabled(environ)
    return GO_WRITE_WIRED


def format_propose_receipt(
    *,
    runbook_id: str,
    fingerprint: str,
    lane: str,
    ticket: str,
    evidence: str,
    exp_hint: str,
) -> str:
    return (
        f"claim: propose {runbook_id} for fp={fingerprint}\n"
        f"status: propose\n"
        f"evidence: {evidence}\n"
        f"next: operator GO: GO: {ticket} fp={fingerprint} lane={lane} exp={exp_hint} runbook={runbook_id}"
    )


def format_ladder_propose_card(*, case_id: str, verdict: str, fp: str, lane: str, ticket: str, exp_hint: str) -> str:
    return (
        f"claim: ladder {verdict} for case={case_id} fp={fp}\n"
        f"status: propose\n"
        f"evidence: tool:case-intake-ladder.{case_id}\n"
        f"next: operator GO: GO: {ticket} fp={fp} lane={lane} exp={exp_hint} action=docker_restart container=<name>"
    )


def ladder_proposal_fp(case_id: str, description: str, verdict: str) -> str:
    blob = f"{case_id}|{verdict}|{(description or '')[:120]}"
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def record_ladder_proposal(
    *,
    case_id: str,
    description: str,
    verdict: str,
    lane: str,
    ticket: str,
    container: str = "",
) -> str:
    fp = ladder_proposal_fp(case_id, description, verdict)
    go_proposals.save_ladder_proposal(fp=fp, lane=lane, ticket=ticket, container=container, case_id=case_id)
    return fp


def try_apply_scoped_go(
    go_text: str,
    *,
    persona: str,
    room: str,
    pending_fingerprint: str | None = None,
    pending_lane: str | None = None,
    runbook_id: str | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Parse scoped operator GO; match proposal; optional docker_restart. Returns (verdict, four_line_receipt)."""
    env = environ if environ is not None else os.environ
    if not _refresh_wired_flag(env):
        raise GoWriteNotWiredError("CHIP_SCOPED_GO_ENABLED is off")

    fields, reason = go_scope.parse_scoped_go(go_text)
    if fields is None:
        return "refused", _receipt("scoped-go", "refused", reason, "hold for operator")

    if go_proposals.token_used(go_text):
        go_proposals.write_verify_row(fp=fields.get("fp", ""), ticket=fields["ticket"], status="refused", detail="replayed token")
        return "refused", _receipt("scoped-go", "refused", "scoped GO token already used", "hold for operator")

    rb = (runbook_id or fields.get("runbook") or "").strip().lower()
    if rb and runbooks.runbook_class(rb) == "blocked":
        return "blocked", _receipt(rb, "blocked", "runbook class blocked", "hold for operator")
    if rb in _BLOCKED_RUNBOOKS or (fields.get("action") or "") in _BLOCKED_ACTIONS:
        return "refused", _receipt("scoped-go", "refused", "action forbidden for GO", "hold for operator")

    prop = go_proposals.latest_proposal(fields["fp"])
    if prop is None:
        return "refused", _receipt("scoped-go", "refused", "no matching proposal for fp", "hold for operator")
    if prop.get("lane") != fields["lane"]:
        return "refused", _receipt("scoped-go", "refused", "scoped GO lane mismatch", "hold for operator")
    if str(prop.get("ticket")) != fields["ticket"]:
        return "refused", _receipt("scoped-go", "refused", "scoped GO ticket mismatch", "hold for operator")
    if pending_fingerprint and pending_fingerprint != fields["fp"]:
        return "refused", _receipt("scoped-go", "refused", "scoped GO fingerprint mismatch", "hold for operator")
    if pending_lane and pending_lane != fields["lane"]:
        return "refused", _receipt("scoped-go", "refused", "scoped GO lane mismatch", "hold for operator")

    container = (fields.get("container") or prop.get("container") or "").strip().lower()
    action = (fields.get("action") or ("docker_restart" if container else "")).lower()
    target = container or rb or "scoped-go"

    if not go_verify.verifier_signed_in_for(target, environ=env):
        go_proposals.write_verify_row(fp=fields["fp"], ticket=fields["ticket"], status="refused", detail="no signed-in verifier")
        return "refused", _receipt("scoped-go", "refused", "verify required: no signed-in verifier", "hold for operator")

    if action == "docker_restart":
        if not container:
            return "refused", _receipt("scoped-go", "refused", "docker_restart needs container=", "hold for operator")
        verdict, detail = _docker_restart_scoped(container, environ=env)
        go_proposals.mark_token_used(go_text, meta={"fp": fields["fp"], "container": container})
        go_proposals.write_verify_row(fp=fields["fp"], ticket=fields["ticket"], status="done" if verdict == "done" else "refused", detail=detail)
        return verdict, _receipt(container, verdict, detail, "re-run health probe")

    go_proposals.mark_token_used(go_text, meta={"fp": fields["fp"]})
    return "refused", _receipt("scoped-go", "refused", "no executable action on GO line", "hold for operator")


def _docker_restart_scoped(container: str, *, environ: dict[str, str]) -> tuple[str, str]:
    from chip_relay.monitoring_skills import HARD_FORBIDDEN_RESTART_CONTAINERS, _docker_restart, load_policy

    wanted = container.strip().lower()
    policy = load_policy()
    if not policy.loaded:
        return "refused", "no monitoring restart policy loaded (fail-closed)"
    if wanted in HARD_FORBIDDEN_RESTART_CONTAINERS:
        return "refused", f"forbidden container {wanted}"
    if wanted in policy.forbidden_restart_containers:
        return "refused", f"forbidden container {wanted}"
    if wanted not in policy.allowed_restart_containers:
        return "refused", f"{wanted} not in allowed_restart_containers"
    from chip import pack_values

    blocked = pack_values.solver_forbidden_substrings()
    if any(pat in wanted for pat in blocked) or any(tok in wanted for tok in ("media", "delete")):
        return "refused", "blocked media/native target"
    ok, note = _docker_restart(wanted, policy)
    return ("done" if ok else "refused"), note


def _receipt(claim: str, status: str, evidence: str, nxt: str) -> str:
    return f"claim: scoped GO {claim}\nstatus: {status}\nevidence: {evidence}\nnext: {nxt}"
