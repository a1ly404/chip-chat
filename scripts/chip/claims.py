"""Chip-side claim convention (Grok-harvest port, round-5 hardened).

Law (docs/ENVELOPES.md): the FIRST ``claim: <persona> <scope>`` line in the room
transcript owns that scope — transcript order wins. Unlock via ``claim done`` /
confirmed ``handoff:`` / steal (heartbeat miss AND ping-no-reply — not implemented
here; needs the watchdog). Enforcement is discipline + this parser, not hooks.

Round-5 hardening (Grok red team):
- envelope must be the WHOLE message body (no burying in prose/codefence)
- NFKC-normalized; BOM/ZWSP stripped (fullwidth ：, zero-width confusables)
- claimed persona MUST equal the authenticated speaker (no claiming others)
- reserved worker ids from the active pack are not claimable personas
- scope required, no wildcards (*/all// rejected)
"""

from __future__ import annotations

import json
import re
import unicodedata

from chip import pack_values, store

_CLAIM_RE = re.compile(r"^claim:\s*(\S+)(?:\s+(\S+))?$", re.IGNORECASE)
_CLAIM_DONE_RE = re.compile(r"^claim\s+done\b", re.IGNORECASE)

RESERVED_IDS = set(pack_values.reserved_claim_ids())


def reserved_ids(environ: dict[str, str] | None = None) -> frozenset[str]:
    return frozenset(RESERVED_IDS)
_WILDCARD_SCOPES = {"*", "all", "/"}


def _clean(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return normalized.replace("\ufeff", "").replace("\u200b", "").strip()


def parse_claim_line(text: str) -> dict[str, str] | None:
    """Parse a claim envelope — must be the ENTIRE (normalized) message body."""
    line = _clean(text)
    m = _CLAIM_RE.match(line)
    if not m:
        return None
    persona = m.group(1).lower()
    scope = (m.group(2) or "").lower()
    if not scope or scope in _WILDCARD_SCOPES:
        return None
    if persona in reserved_ids():
        return None
    return {"persona": persona, "scope": scope}


def is_claim_done_line(text: str) -> bool:
    return bool(_CLAIM_DONE_RE.match(_clean(text)))


def scan_claims(room: str) -> dict[str, dict]:
    """Replay the transcript; return scope -> claim state.

    State: ``{"persona": ..., "status": "active"|"done", "seq": n}`` where seq is
    the transcript order the claim was made in (first claim for a scope wins;
    later claim lines for a taken scope are counted as ``duplicates``).
    Spoofed claims (persona ≠ authenticated speaker) are counted as ``spoofs``
    on the scope and never honored. Malformed envelopes are ignored silently
    (the caller can't distinguish noise from attack — the parser just refuses).
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
        agent = (rec.get("agent") or "user").lower()
        body = _clean(content)
        done = is_claim_done_line(body)
        parsed = parse_claim_line(body)
        if parsed:
            scope = parsed["scope"]
            if parsed["persona"] != agent:
                # spoof: claiming as a different id — NEVER honored, never grants
                continue
            if scope in out and out[scope]["status"] == "active":
                out[scope]["duplicates"] = out[scope].get("duplicates", 0) + 1
                continue
            if scope in out:
                out[scope].update({"persona": parsed["persona"], "status": "active", "seq": seq})
            else:
                out[scope] = {"persona": parsed["persona"], "status": "active", "seq": seq, "duplicates": 0}
            continue
        if done:
            for state in out.values():
                if state["status"] == "active" and (state["persona"] == agent or agent == "user"):
                    state["status"] = "done"
    return out


def claim_owner(room: str, scope: str) -> str | None:
    state = scan_claims(room).get(_clean(scope).lower())
    if state and state["status"] == "active":
        return state["persona"]
    return None
