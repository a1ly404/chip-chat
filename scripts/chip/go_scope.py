"""Scoped GO for deploy, absorb, and ladder proposals.

    GO: <ticket> fp=<fingerprint> lane=<lane> exp=<ISO-8601 Z>
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

_TICKET = re.compile(r"(?i)\bGO:\s*([A-Za-z0-9][-A-Za-z0-9_]*)\b")
_FP = re.compile(r"\bfp=([A-Za-z0-9._-]{3,64})\b")
_LANE = re.compile(r"\blane=([A-Za-z0-9-]{2,32})\b")
_EXP = re.compile(r"\bexp=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\b")
_ACTION = re.compile(r"\baction=([a-z0-9_-]+)\b", re.IGNORECASE)
_CONTAINER = re.compile(r"\bcontainer=([a-z0-9_-]+)\b", re.IGNORECASE)
_RUNBOOK = re.compile(r"\brunbook=([a-z0-9_-]+)\b", re.IGNORECASE)
_DEPLOY = re.compile(r"\b(deploy|absorb)\b", re.IGNORECASE)

_MISSING_MSG = "scoped GO needs GO: <ticket> fp= lane= exp="


def needs_scoped_go(text: str) -> bool:
    return _DEPLOY.search(text or "") is not None


def is_scoped_go_line(text: str) -> bool:
    raw = (text or "").strip()
    return raw.upper().startswith("GO:") and "fp=" in raw and "lane=" in raw


def propose_go_marker(text: str, *, now: datetime | None = None) -> tuple[dict[str, str] | None, str]:
    return parse_scoped_go(text, now=now)


def parse_scoped_go(text: str, *, now: datetime | None = None) -> tuple[dict[str, str] | None, str]:
    """Return fields when ticket, fingerprint, lane, and unexpired exp are present."""
    raw = text or ""
    ticket_m = _TICKET.search(raw)
    fp = _FP.search(raw)
    lane = _LANE.search(raw)
    exp = _EXP.search(raw)
    if ticket_m is None or fp is None or lane is None or exp is None:
        return None, _MISSING_MSG
    stamp = datetime.strptime(exp.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if stamp <= current:
        return None, "scoped GO expired"
    fields: dict[str, str] = {
        "ticket": ticket_m.group(1),
        "fp": fp.group(1),
        "lane": lane.group(1),
        "exp": exp.group(1),
    }
    if _ACTION.search(raw):
        fields["action"] = _ACTION.search(raw).group(1).lower()
    if _CONTAINER.search(raw):
        fields["container"] = _CONTAINER.search(raw).group(1).lower()
    if _RUNBOOK.search(raw):
        fields["runbook"] = _RUNBOOK.search(raw).group(1).lower()
    return fields, "scoped go"
