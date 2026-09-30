"""Scoped GO for deploy and absorb.

A bare ``GO: <ticket>`` may wake above-read work. Deploy and absorb also need
a fingerprint, a lane, and an expiry on that same text:

    GO: <ticket> fp=<fingerprint> lane=<lane> exp=<ISO-8601 Z>
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

_FP = re.compile(r"\bfp=([A-Za-z0-9._-]{3,64})\b")
_LANE = re.compile(r"\blane=([A-Za-z0-9-]{2,32})\b")
_EXP = re.compile(r"\bexp=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\b")
_DEPLOY = re.compile(r"\b(deploy|absorb)\b", re.IGNORECASE)


def needs_scoped_go(text: str) -> bool:
    return _DEPLOY.search(text or "") is not None


def parse_scoped_go(text: str, *, now: datetime | None = None) -> tuple[dict[str, str] | None, str]:
    """Return fields when fingerprint, lane, and unexpired exp are all present."""
    raw = text or ""
    fp = _FP.search(raw)
    lane = _LANE.search(raw)
    exp = _EXP.search(raw)
    if fp is None or lane is None or exp is None:
        return None, "deploy/absorb needs GO fp= lane= exp="
    stamp = datetime.strptime(exp.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if stamp <= current:
        return None, "scoped GO expired"
    return {"fp": fp.group(1), "lane": lane.group(1), "exp": exp.group(1)}, "scoped go"
