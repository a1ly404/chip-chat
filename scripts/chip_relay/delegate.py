"""Explicit ``delegate:<persona> <task>`` handoffs (relay-controlled, not AUTO)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from chip import config, store

MAX_DELEGATE_HOPS = 3
# Nested ``delegate:`` hops within one handoff chain (one human/inject turn → webhook follow-ups).
_chain_depth: dict[str, int] = {}
_DELEGATE_LINE = re.compile(
    r"^delegate:(?P<persona>[A-Za-z0-9][A-Za-z0-9_.-]{0,31})\s+(?P<task>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DelegateRequest:
    persona: str
    task: str


def known_persona_ids() -> frozenset[str]:
    return frozenset(config.load_personas().keys())


def parse_delegate_line(line: str) -> DelegateRequest | None:
    """Parse one line like ``delegate:dev fix the flake``."""
    wanted = line.strip()
    if not wanted:
        return None
    match = _DELEGATE_LINE.match(wanted)
    if not match:
        return None
    persona = match.group("persona").strip().lower()
    task = match.group("task").strip()
    if not task:
        return None
    if persona not in known_persona_ids():
        return None
    return DelegateRequest(persona=persona, task=task)


def parse_delegate(text: str) -> DelegateRequest | None:
    """Return the first valid ``delegate:`` line in a multi-line reply."""
    if not isinstance(text, str):
        return None
    for line in text.splitlines():
        found = parse_delegate_line(line)
        if found is not None:
            return found
    return None


def reset_delegate_chain(room_id: str) -> None:
    """Start a new handoff chain (call at the beginning of each human/inject turn)."""
    _chain_depth[room_id] = 0


def next_delegate_chain_hop(room_id: str) -> int:
    """1-based hop index for the next delegate in the current chain."""
    return _chain_depth.get(room_id, 0) + 1


def record_delegate_chain_hop(room_id: str, hop: int) -> None:
    """Mark that ``hop`` was consumed (logged whether or not chip ran)."""
    _chain_depth[room_id] = hop


def max_logged_delegate_hop(room_id: str) -> int:
    """Highest ``relay_handoff.hop`` recorded in the room transcript (lifetime peak)."""
    path = store.thread_path(room_id)
    if not path.is_file():
        return 0
    peak = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        handoff = rec.get("relay_handoff")
        if not isinstance(handoff, dict):
            continue
        hop = handoff.get("hop")
        if isinstance(hop, int) and hop > peak:
            peak = hop
    return peak


# ---------------------------------------------------------------------------
# Phase-2 brief wiring: delegate hops anchored to a ticket id get the brief-builder
# (goal + budget + memory pulls + claim discipline).
# Opt-in via CHIP_TASKBRIEF=1; the palace gate is FAIL-VISIBLE — an unreachable
# palace REFUSES the delegation (alert line, log-only) instead of silently
# downgrading to a raw hop (golden 5 — the refusal is the honest move).
# ---------------------------------------------------------------------------

def task_anchor_re() -> re.Pattern[str]:
    from chip import pack_values

    return pack_values.ticket_anchor_pattern()


def task_brief_opts(task: str, persona: str, environ: dict[str, str] | None = None) -> dict:
    """Return ``{"task_id","task_type"}`` for anchored tasks, else ``{}``.

    Raises TaskPalaceUnavailable (fail-visible) when the gate fails — the
    caller refuses the hop; never silently forwards the raw task string.
    """
    env = environ if environ is not None else os.environ
    if (env.get("CHIP_TASKBRIEF", "") or "").strip().lower() not in {"1", "true", "yes"}:
        return {}  # not opted in — legacy delegate shape, zero behavior change
    import os as _os

    anchor = task_anchor_re().search(task or "")
    if not anchor:
        return {}
    from chip import taskflow

    taskflow.preflight(environ)
    return {"task_id": anchor.group(0), "task_type": "diagnosis"}


def brief_text_for(persona: str, room_id: str, task: str, environ: dict[str, str] | None = None) -> str:
    """The brief body for an anchored hop (used when opted in)."""
    from chip import taskflow

    m = task_anchor_re().search(task or "")
    task_id = m.group(0) if m else ""
    return taskflow.build_brief(task_id, persona, room_id, task.strip(),
                                environ=environ).text
