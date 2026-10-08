"""Per-turn cost ledger columns (Phase 1, operator lock 2026-09-28).

Adds the task-binding columns to every room-shot log row:

- ``task_id``      — a ticket anchor (e.g. ``TICKET-123``); binding spend to the
                     OUTCOME is first-class per the aligned plan.
- ``task_type``    — free label of the task class (diagnosis/triage/fix-package/docs).
- ``turn_of_task`` — 1-based turn index for that task's cycle; derived from an
                     append-only counter file so a shot's turn number survives
                     across processes (the loop is process-serial per the
                     one-writer law; the counter is atomic per process).

Backward compatible: absent task args keep producing exactly the old row shape
(existing tests + relay consumers untouched).

usd_cost per turn: the shot's ``cost_usd`` row field already carries it
(cli.py ``log_message(... cost_usd=call_cost)``); the ledger contract names it
``usd_cost`` for the task ledger — we write BOTH keys into task-bound rows
(existing consumers read ``cost_usd``; the Phase-1 contract reads ``usd_cost``:
one extra alias per task row, zero change for task-less rows).

memory_op_counts: {reads: N, writes: N, writes_lines: N} collected at the same
call sites as the ops themselves (recall in build_room_messages; mining in
close_room_session). Fail-open-safe: counting is observational — if a memory
layer is unavailable (mempalace module missing), ops count 0 and the row
still lands (an absent-memory turn is user-visible as the absence of the
MINED MEMORIES block, not silent).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

TASK_TYPES_KNOWN = frozenset(
    {"diagnosis", "triage", "fix-package", "docs", "ledger", "standup", "other"}
)


class TaskCounter:
    """Append-only turn counter per task_id (one file, atomic rewrites).

    Store location: ``CHIP_CHAT_DATA_DIR/task_ledger/task_turns.json`` —
    shared per the one-writer law: the ROOM process owns its task's counter
    row while it runs; concurrent writers ARE possible but harmless here:
    the object of record for cost is the room JSONL row (this counter exists
    only to make turn_of_task continuous across processes; a losing race
    just under-counts and the row still tells the truth).
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, int]:
        try:
            return dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}  # garbage counter file = fresh, the row still tells truth

    def next_turn(self, task_id: str) -> int:
        counters = self._load()
        n = int(counters.get(task_id, 0)) + 1
        counters[task_id] = n
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(counters, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic per-process; cross-process races tolerated (see header)
        return n


def retro_tickets_path(data_dir: Path | None = None) -> Path:
    """Append-only retro tickets. Same task_ledger directory as the turn counter."""
    from chip import config

    base = Path(data_dir) if data_dir is not None else config.data_dir()
    return base / "task_ledger" / "retro_tickets.jsonl"


def file_retro_ticket(
    *,
    claimed: str,
    verified: str,
    catch: str,
    task_id: str = "RETRO",
) -> None:
    """File one retro on the existing task ledger. No second tracker."""
    from datetime import datetime, timezone

    from chip import store

    store.append_jsonl(
        retro_tickets_path(),
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "task_type": "ledger",
            "claimed": claimed,
            "verified": verified,
            "catch": catch,
        },
    )


def counter_path(data_dir: Path) -> Path:
    return Path(env_path) if (env_path := os.environ.get("CHIP_TASK_COUNTER_PATH", "")) else Path(data_dir) / "task_ledger" / "task_turns.json"


def counts_for_row(
    task_id: str,
    task_type: str,
    turn: int,
    usd_cost: float,
    memory_ops: Mapping[str, int],
) -> dict[str, Any]:
    """The Phase-1 column block (invariant: keys ALWAYS present for task rows)."""
    return {
        "task_id": task_id,
        "task_type": task_type,
        "turn_of_task": turn,
        "usd_cost": round(float(usd_cost), 6),
        "memory_op_counts": {
            "reads": int(memory_ops.get("reads", 0)),
            "writes": int(memory_ops.get("writes", 0)),
            "written_lines": int(memory_ops.get("written_lines", 0)),
        },
    }