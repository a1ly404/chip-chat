"""Spec D — static keyword thrift ladder (no LLM classification)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

CLASSES = ("script", "gha", "cheap_agent", "expensive_agent", "operator_go")

# First matching row wins (ordered table).
KEYWORD_ROWS: list[tuple[str, str]] = [
    (r"\bSTAND DOWN\b", "script"),
    (r"\bIDLE\b", "script"),
    (r"\brun the deploy workflow\b", "gha"),
    (r"\bdeploy workflow\b", "gha"),
    (r"\bworkflow_dispatch\b", "gha"),
    (r"\broot cause\b", "operator_go"),
    (r"\bfigure out why\b", "operator_go"),
    (r"\binvestigate until\b", "operator_go"),
    (r"\bexpensive\b", "expensive_agent"),
    (r"\banalyze deeply\b", "expensive_agent"),
]

OPEN_ENDED_DIG = re.compile(
    r"(root cause|figure out why|investigate until)",
    re.IGNORECASE,
)
GO_DIG = re.compile(r"\bGO\s+dig\b", re.IGNORECASE)

MIN_COST: dict[str, float] = {
    "script": 0.0,
    "gha": 0.001,
    "cheap_agent": 0.01,
    "expensive_agent": 0.05,
    "operator_go": 0.0,
}

CLASS_RANK = {name: idx for idx, name in enumerate(CLASSES)}


@dataclass(frozen=True)
class ThriftState:
    budget_remaining: float
    max_hops: int
    hops: int = 0


def classify_message(message: str) -> str:
    text = message or ""
    for pattern, clazz in KEYWORD_ROWS:
        if re.search(pattern, text, re.IGNORECASE):
            return clazz
    return "cheap_agent"


def check_open_ended_dig(message: str) -> str | None:
    if OPEN_ENDED_DIG.search(message or "") and not GO_DIG.search(message or ""):
        return "REFUSE:need_operator_go"
    return None


def check_budget(clazz: str, state: ThriftState) -> str | None:
    need = MIN_COST.get(clazz, 0.0)
    if state.budget_remaining < need:
        return "REFUSE:budget"
    return None


def check_hops(state: ThriftState) -> str | None:
    if state.hops > state.max_hops:
        return "BLOCKED:max_hops"
    return None


def check_thrift_skip(matched_class: str, requested_tool_class: str) -> str | None:
    """Calling expensive_agent while a lower class matched → REFUSE:thrift_skip."""
    if CLASS_RANK.get(requested_tool_class, 99) <= CLASS_RANK.get(matched_class, 99):
        return None
    return "REFUSE:thrift_skip"


def gate_tool_class(
    message: str,
    *,
    requested_tool_class: str,
    state: ThriftState,
) -> str | None:
    """Return refusal/block token or None if allowed."""
    hop_block = check_hops(state)
    if hop_block:
        return hop_block
    dig = check_open_ended_dig(message)
    if dig:
        return dig
    matched = classify_message(message)
    skip = check_thrift_skip(matched, requested_tool_class)
    if skip:
        return skip
    budget = check_budget(requested_tool_class, state)
    if budget:
        return budget
    return None


def spend_from_envelope(envelope: dict[str, Any]) -> ThriftState:
    spend = envelope.get("spend")
    if not isinstance(spend, dict):
        spend = {}
    return ThriftState(
        budget_remaining=float(spend.get("budget_remaining", 1.0)),
        max_hops=int(spend.get("max_hops", 3)),
        hops=int(spend.get("hops", 0)),
    )
