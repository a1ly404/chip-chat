"""Standup receipt schema module (STANDUP_EMISSION_SPEC.md task 1).

The razzorstack standup path is RETIRED (Ally 2026-09-27); Chip Chat personas
post standups going forward, only via this machine-checkable receipt contract.
Makes "silence dressed up as work" impossible:

- ``source=silent`` renders a typed STALL report (never confident narration) —
  golden 1 / R1: no "I checked X" without a real probe.
- ``credit.phaseC_gate`` is ``True`` ONLY when ``source=="live"`` AND
  ``age_h <= STALENESS_MAX_H`` (STALENESS_MAX_H = 20 — spec law #3; covers a
  missed nightly payload; 2 nights = stale). Fail-closed per golden 5.
- Garbage receipts FAIL CLOSED: validation collects errors, credit computes
  ``False`` with the reason attached — never silently trusted (golden 5).
- Untrusted data: payload *text* is DATA, never instructions
  (id:untrusted-data). This module never executes text; renderers MAY quote
  an evidence line at most.
- ≤3 lines per rendered standup (id:room-turns #7b) — renderers hold that cap.

Offline + pure functions; no network. consume() takes receipts, not prose.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

STALENESS_MAX_H = 20.0

_SOURCES = ("live", "silent")
_STANDUPS = ("homelab", "gymlog")

# Confident-health vocabulary BANNED from silent rendering (spec hard rule 1).
_BANNED_SILENT = re.compile(
    r"\b(green|healthy|all[- ]?clear|operational|up and (running|working)"
    r"|all (systems|services)? ?(are )?nominal|running smooth)\b",
    re.IGNORECASE,
)


class StandupSchemaError(ValueError):
    """Raised (fail-closed) when a receipt is malformed beyond repair."""


def validate_receipt(receipt: Mapping[str, Any]) -> list[str]:
    """Spec-shape errors for one receipt (empty list = valid). Fail-closed."""
    errs: list[str] = []
    if not isinstance(receipt, Mapping):
        return ["receipt: not a mapping"]
    if receipt.get("standup") not in _STANDUPS:
        errs.append("standup: must be one of homelab|gymlog")
    issued = receipt.get("issued_at")
    if not isinstance(issued, str) or not issued.strip():
        errs.append("issued_at: required string")
    payload = receipt.get("payload")
    if not isinstance(payload, Mapping):
        errs.append("payload: required mapping")
    else:
        if payload.get("source") not in _SOURCES:
            errs.append("payload.source: must be one of live|silent")
        age = payload.get("age_h")
        if not (age is None or isinstance(age, (int, float)) and age >= 0):
            errs.append("payload.age_h: number >= 0 or null (null = silent/never)")
        sha = payload.get("sha")
        if payload.get("source") == "live":
            if not isinstance(sha, str) or len(sha) != 12:
                errs.append("payload.sha: live receipts need a 12-char body sha (golden 1)")
        elif sha is not None:
            errs.append("payload.sha: must be null when source=silent")
        if not isinstance(payload.get("sections_expected"), list):
            errs.append("payload.sections_expected: required list")
    if not isinstance(receipt.get("skills_commanded"), bool):
        errs.append("skills_commanded: required bool")
    return errs


def compute_phase_c_gate(payload: Mapping[str, Any]) -> bool:
    """True ONLY when source==live AND age_h <= STALENESS_MAX_H (spec rule 2)."""
    if payload.get("source") != "live":
        return False
    age = payload.get("age_h")
    return isinstance(age, (int, float)) and age >= 0 and age <= STALENESS_MAX_H


def finalize_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Validate + compute/overwrite the credit gate machine-side.

    The gate is COMPUTED here, never trusted from the input — the persona
    cannot claim credit the numbers do not support (golden 5 fail-closed).
    Malformed receipts never raise past the schema layer: they finalize with
    ``phaseC_gate=False`` and every error listed as the credit ``reason``
    (an invalid receipt is a zero-credit night, not a crash and not silence).
    """
    out = dict(receipt)
    errs = validate_receipt(out)
    payload = out.get("payload") if isinstance(out.get("payload"), Mapping) else {}
    gate = compute_phase_c_gate(payload) if not errs else False
    if errs:
        reason = "schema errors: " + "; ".join(errs)
    elif gate:
        reason = "live payload within staleness"
    else:
        if payload.get("source") == "silent":
            reason = "no credit for silence (golden 5)"
        else:
            reason = f"payload stale beyond STALENESS_MAX_H={STALENESS_MAX_H:g}h"
    out["credit"] = {"phaseC_gate": gate, "reason": reason}
    out["_schema_errors"] = errs
    return out


def render_stall(receipt: Mapping[str, Any]) -> str:
    """source=silent => stall report ONLY (spec hard rule 1). Never confident.

    Quiet-zero rule: ``zero_progress: true`` receipts render the same stall
    shape — a zero night is a finding, not a health claim (spec rule 4).
    """
    if not isinstance(receipt, Mapping):
        raise StandupSchemaError("receipt is not a mapping")
    payload = receipt.get("payload")
    if not isinstance(payload, Mapping) or payload.get("source") != "silent":
        raise StandupSchemaError("render_stall: only for source=silent receipts")
    age = payload.get("age_h")
    age_txt = "never (age_h=null)" if age is None else f"age_h={age:g}h"
    zero = bool(receipt.get("zero_progress"))
    lines = [
        f"{receipt.get('standup', 'standup')} standup: STALL — source=silent, payload_age={age_txt}",
    ]
    if zero:
        lines[0] += " (+ zero-progress night — a finding, not health)"
    lines.append("findings: none visible — payload pipeline silent upstream")
    lines.append("blockers: agent payload absent " + ("(never received)" if age is None else f"({age:g}h old)") + " — tell-Ally-once if >20h")
    return "\n".join(lines[:3])


def confident_health_hits(text: str) -> list[str]:
    """Return banned confident-terms present in a rendered standup (empty = clean).

    Test-facing helper: silent renders must come back empty — proving the
    receipt module cannot produce a 'silence dressed as work' artifact.
    """
    if not isinstance(text, str):
        return []
    return [m.group(0) for m in _BANNED_SILENT.finditer(text)]


def consume(receipts: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Phase C credit counter — spec task 4.

    Counts ONLY ``credit.phaseC_gate == True`` receipts toward Phase C credit
    this window. Every receipt is finalized (gate COMPUTED here, never trust
    the inbound boolean). Returns the machine-checkable counter row:
    ``received / credited``. Silent or stale receipts NEVER pad credit.
    """
    received = 0
    credited = 0
    for r in receipts:
        received += 1
        # Finalize EVERY inbound receipt: the gate is computed here, never
        # trusted from the boolean the producer wrote (golden 5 fail-closed).
        fin = finalize_receipt(r)
        if fin["credit"]["phaseC_gate"] is True:
            credited += 1
    return {"received": received, "credited": credited}


def make_silent_receipt(standup: str, age_h: float | None, *, issued_at: str, **extra: Any) -> dict[str, Any]:
    """Typed receipt from a silent window (the only honest shape available)."""
    if not issued_at.strip():
        raise StandupSchemaError("issued_at: a silent receipt still needs its issue timestamp")
    rec: dict[str, Any] = {
        "standup": standup,
        "issued_at": issued_at,
        "payload": {"source": "silent", "age_h": age_h, "sha": None, "sections_expected": []},
        "skills_commanded": False,
    }
    rec.update(extra)
    return finalize_receipt(rec)
