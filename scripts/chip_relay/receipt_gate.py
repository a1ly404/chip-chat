"""Fail-closed sitrep schema for relay posts.

The model prompt may ask for these four lines. This module is the gate:
invalid bodies are replaced with an in-channel ERROR before the webhook fires.
"""

from __future__ import annotations

import re

ERROR_PREFIX = "[chip-relay] ERROR: receipt schema: "

_FIELD = re.compile(r"^(claim|status|evidence|next):\s*(.*)$", re.IGNORECASE)
_STATUS = {"done", "blocked", "refused"}
_SHA = re.compile(r"^sha:[0-9a-f]{7,40}$", re.IGNORECASE)
_TOOL = re.compile(r"^tool:[A-Za-z0-9_.:-]{4,}$")
_PR = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+/pull/\d+$")
_LINEAR = re.compile(r"^TICKET-\d+$", re.IGNORECASE)
_EXEC = re.compile(
    r"\b(ran|executed|execute|crontab|shell|subprocess)\b|`[^`]+`",
    re.IGNORECASE,
)
_TABLE = re.compile(r"^\s*\|")


def _parse(body: str) -> dict[str, str] | None:
    found: dict[str, str] = {}
    for line in body.splitlines():
        match = _FIELD.match(line.strip())
        if match is None:
            continue
        found[match.group(1).lower()] = match.group(2).strip()
    if set(found) != {"claim", "status", "evidence", "next"}:
        return None
    return found


def _doc_evidence(evidence: str) -> bool:
    return bool(_SHA.match(evidence) or _PR.match(evidence) or _LINEAR.match(evidence))


def receipt_error(body: str) -> str | None:
    """Return a reason string when the body must not be posted as a sitrep."""
    text = (body or "").strip()
    if not text:
        return "malformed body"
    table_lines = sum(1 for line in text.splitlines() if _TABLE.match(line))
    field_lines = sum(1 for line in text.splitlines() if _FIELD.match(line.strip()))
    fields = _parse(text)
    if fields is None:
        if table_lines >= 3:
            return "unlabeled-table"
        # A human reply with no receipt lines is conversation, not a failed sitrep.
        if field_lines == 0:
            return None
        return "missing fields"
    status = fields["status"].lower()
    if status not in _STATUS:
        return "malformed body"
    if not fields["claim"] or not fields["next"]:
        return "missing fields"
    evidence = fields["evidence"]
    if status == "done" and (not evidence or evidence.lower() == "none"):
        return "done-without-evidence"
    if status != "done" and evidence.lower() != "none" and not (
        _doc_evidence(evidence) or _TOOL.match(evidence)
    ):
        return "malformed body"
    if status == "done" and not (_doc_evidence(evidence) or _TOOL.match(evidence)):
        return "done-without-evidence"
    if status == "done" and _EXEC.search(text) and not _TOOL.match(evidence):
        return "execution-without-tool-receipt"
    return None


def gate_channel_body(body: str) -> str:
    reason = receipt_error(body)
    if reason is None:
        return body
    return f"{ERROR_PREFIX}{reason}"


def body_for_store(body: str, environ: dict[str, str] | None = None) -> str:
    """Relay shots store the gated body. Local room shots store the model text."""
    import os

    env = os.environ if environ is None else environ
    if env.get("CHIP_STORE_GATED") == "1":
        return gate_channel_body(body)
    return body
