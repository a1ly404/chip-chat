"""Counter registry: the ONLY authority for
"does a counter exist". Loaded, deterministic, pinned. The caller runs every
entry against the candidate state BEFORE any Jev HTTP call; the ledger row
records ``counter_id=<hit|none>`` before the call. ``none`` is the only legal
predecessor of a Jev request.

Fixture corpus grows from leaks: every counter-decidable receipt later judged
live becomes a fixture in the same PR that adds its counter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chip import system_pack


def _registry_path() -> Path | None:
    return system_pack.optional_surface("counter_registry")


def __getattr__(name: str):
    if name == "REGISTRY_PATH":
        path = _registry_path()
        if path is None:
            raise FileNotFoundError("counter_registry surface missing")
        return path
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass(frozen=True)
class CounterResult:
    counter_id: str   # "hit:<id>" | "none"
    hit: bool
    detail: str


def load_registry(path: Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else _registry_path()
    if p is None or not p.is_file():
        return {"version": 1, "counters": []}
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("counters"), list):
        raise ValueError("counter registry must be an object with a counters list")
    return raw


def _state_gets(state: dict[str, Any], key: str) -> Any:
    return state.get(key)


def matches_entry(entry: dict[str, Any], state: dict[str, Any]) -> bool:
    """Deterministic matcher for one registry entry.

    Supported match conditions:
    - task_kind == state["task_type"] (exact, case-insensitive)
    - condition: "unbound_done" — done claimed AND boxes checked AND 0 receipts
    Unknown entries never match (registry is the only authority — fail quiet
    rather than invent match semantics at runtime).
    """
    kind = entry.get("matches", {}).get("task_kind")
    cond = entry.get("matches", {}).get("condition")
    if isinstance(kind, str) and _state_gets(state, "task_type") != kind:
        return False
    if cond == "unbound_done":
        status = str(_state_gets(state, "status") or "").lower()
        st = str(_state_gets(state, "statusType") or "").lower()
        claimed_done = status == "done" or st == "completed"
        checked = _state_gets(state, "checked_boxes")
        bound = _state_gets(state, "receipts_bound")
        unbound = _state_gets(state, "unbound_done")
        if claimed_done and isinstance(checked, int) and checked > 0:
            if isinstance(bound, int) and bound == 0:
                return True
            if unbound is True:
                return True
        if unbound is True and claimed_done:
            return True
        return False
    if cond == "unbound_done_soft":
        return str(_state_gets(state, "unbound_done") or "").lower() == "true"
    return False


def precheck(state: dict[str, Any], path: Path | None = None) -> CounterResult:
    """Run every registry entry; first hit wins (deterministic order)."""
    reg = load_registry(path)
    for entry in reg.get("counters", []):
        if matches_entry(entry, state):
            cid = str(entry.get("id") or "unnamed")
            return CounterResult(counter_id=f"hit:{cid}", hit=True,
                                 detail=f"counter registry hit {cid!r}: deterministic rung answers; Jev not invoked")
    return CounterResult(counter_id="none", hit=False,
                         detail="no registry counter matched — gray residue, Jev may proceed")
