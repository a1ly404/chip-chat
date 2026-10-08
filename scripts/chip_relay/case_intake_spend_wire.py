"""Pipeline hooks for case intake spend attribution (slice 5)."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from chip_relay.case_intake import load_limits
from chip_relay.case_intake_spend import (
    estimate_usd,
    global_monthly_usd,
    per_case_warn_flag,
    record_attempt,
)

if TYPE_CHECKING:
    from chip_relay.case_intake import IntakeResult


def _skip_spend(environ: dict[str, str]) -> bool:
    return environ.get("CASE_INTAKE_SKIP_SPEND_GATE", "1").strip() in {"1", "true", "yes"}


def monthly_gate_for_parse(environ: dict[str, str]) -> float:
    if _skip_spend(environ):
        return 0.0
    return global_monthly_usd()


def log_intake_spend(
    *,
    environ: dict[str, str],
    channel: str,
    case_id: str,
    model: str,
    tokens: int,
    blocked: bool,
    outcome: str,
    now: float,
) -> None:
    if _skip_spend(environ):
        return
    limits = load_limits()
    usd = estimate_usd(model, tokens)
    global_warn = global_monthly_usd() >= float(limits.get("budget_warn_usd", 5))
    per_case = per_case_warn_flag(case_id, usd, now=now)
    record_attempt(
        case_id=case_id,
        channel=channel,
        model=model,
        tokens=tokens,
        blocked=blocked,
        outcome=outcome,
        global_warn=global_warn,
        per_case_warn=per_case,
        now=now,
    )


def apply_post_parse_spend(
    environ: dict[str, str],
    channel: str,
    incoming_text: str,
    result: IntakeResult,
    *,
    now: float,
) -> str | None:
    """Return ``blocked`` when global hard gate stopped GLM parse."""
    if _skip_spend(environ):
        return None
    inp_sha = hashlib.sha256(incoming_text.encode()).hexdigest()[:12]
    if result.kind == "blocked":
        model = result.receipt.model if result.receipt else "unknown"
        log_intake_spend(
            environ=environ,
            channel=channel,
            case_id=f"gate-blocked-{inp_sha}",
            model=model,
            tokens=0,
            blocked=True,
            outcome="blocked_global",
            now=now,
        )
        return "blocked"
    if result.receipt is None:
        return None
    rec = result.receipt
    if result.kind == "pending":
        log_intake_spend(
            environ=environ,
            channel=channel,
            case_id=f"pending-{inp_sha}",
            model=rec.model,
            tokens=rec.tokens,
            blocked=False,
            outcome="llm_pending_clarify",
            now=now,
        )
    elif result.kind == "case" and result.case is not None:
        log_intake_spend(
            environ=environ,
            channel=channel,
            case_id=result.case.id,
            model=rec.model,
            tokens=rec.tokens,
            blocked=False,
            outcome="llm_parsed_clean",
            now=now,
        )
    return None
