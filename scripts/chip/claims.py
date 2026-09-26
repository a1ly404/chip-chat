"""Chip-side claim convention (Grok-harvest port).

Law (docs/ENVELOPES.md): the FIRST ``claim: <persona> <scope>`` line in the room
transcript owns that scope — server created_at wins. Unlock via ``claim done`` /
confirmed ``handoff:`` / steal (heartbeat miss AND ping-no-reply — not implemented
here; needs the watchdog). Enforcement is discipline + this parser, not hooks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from chip import store

_CLAIM_RE = re.compile(r"^claim:\s*(\S+)(?:\s+(\S+))?", re.IGNORECASE)
_CLAIM_DONE_RE = re.compile(r"^claim\s+done\b", re.IGNORECASE)


def parse_claim_line(text: str) -> dict[str, str] | None:
    line = text.strip()
    m = _CLAIM_RE.match(line)
    if not m:
        return None
    return {"persona": m.group(1).lower(), "scope": (m.group(2) or "unscoped").lower()}


def is_claim_done_line(text: str) -> bool:
    return bool(_CLAIM_DONE_RE.match(text.strip()))


def scan_claims(room: str) -> dict[str, dict]:
    """Replay the transcript; return scope -> claim state.

    State: ``{"persona": ..., "status": "active"|"done", "seq": n}`` where seq is
    the transcript order the claim was made in (first claim for a scope wins;
    later claim lines for a taken scope are recorded as ``status: "duplicate"``).
    """
    path = store.thread_path(room)
    out: dict[str, dict] = {}
    if not path.is_file():
        return out
    seq = 0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        content = rec.get("content") if isinstance(rec, dict) else None
        if not isinstance(content, str):
            continue
        seq += 1
        agent = rec.get("agent") or "user"
        for line in content.splitlines():
            line = line.strip()
            parsed = parse_claim_line(line)
            if parsed:
                scope = parsed["scope"]
                if scope in out and out[scope]["status"] == "active":
                    out[scope]["duplicates"] = out[scope].get("duplicates", 0) + 1
                    continue
                if scope in out:
                    out[scope].update({"persona": parsed["persona"], "status": "active", "seq": seq})
                else:
                    out[scope] = {"persona": parsed["persona"], "status": "active", "seq": seq, "duplicates": 0}
                continue
            if is_claim_done_line(line):
                for state in out.values():
                    if state["status"] == "active" and (state["persona"] == agent or agent == "user"):
                        state["status"] = "done"
    return out


def claim_owner(room: str, scope: str) -> str | None:
    state = scan_claims(room).get(scope.strip().lower())
    if state and state["status"] == "active":
        return state["persona"]
    return None
