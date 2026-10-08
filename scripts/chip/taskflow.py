"""Phase 2 task flow (operator-locked plan 2026-09-28): brief -> claim -> execute -> bind -> close.

One module, five legs:

1. CLAIM: first-claim-wins via the EXISTING chip.claims law (no second registry —
   extends the same room-JSONL claim envelope; one-lane scope applies).
2. BRIEF: minimal task brief for a delegating persona:
   goal + AC + budget line + MemPalace pulls + claim-ownership line.
   Envelope format per docs/ENVELOPES.md; machine-checkable over narrative.
3. RESULT BINDING: task_id -> receipt bundle (Linear comment body the agent lane
   posts + done-gate pre-check reference) — receipts exist on disk; Fabricating
   a "posted" receipt in code would be golden-1 fiction, so binding produces the
   receipt TEXT and the anchor file, never a fake ticket write.
4. MEM PALACE CYCLE WRITE (M2 spec): append-only namespace
   ``tasks/<room>/<agent>.jsonl`` inside the palace tree, fields exactly:
   task_id, agent, task_type, outcome, receipts, lessons, verified_as_of.
5. FAIL-VISIBLE GATE: palace root unreadable/absent -> TaskPalaceUnavailable ->
   delegation REFUSED (the relay logs it, no silent re-degrade to room-only).
   The gate refuses at BRIEF build so the task never spends.

Budget line: derived from the live spend ledger (spend.load_ledger monthly/
session) — WARN/$5 HARD/$10 caps quoted verbatim in the brief (golden 2).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip import claims, mempalace, pack_values, spend
from chip.law import safe_room_name

TASKFLOW_PALACE_ENV = "CHIP_TASK_PALACE_DIR"  # prod override; default = mempalace config
MEM_CONFIG = Path.home() / ".mempalace" / "config.json"


class TaskPalaceUnavailable(RuntimeError):
    """Delegation must refuse: palace root unreadable/missing (fail visible)."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"mem palace unavailable -> delegation refused (fail-visible): {detail}")


class TaskScopeConflict(RuntimeError):
    """First-claim-wins: another persona holds the scope (golden 4)."""


def mem_config_path(env: dict[str, str] | None) -> Path | None:
    home = str((env or {}).get("MEM_CONFIG_HOME", "") or "~/.mempalace/config.json")
    cfg = Path(os.path.expanduser(home))
    return cfg if cfg.is_file() else None


def palace_root(environ: dict[str, str] | None = None) -> Path:
    env = environ if environ is not None else os.environ
    override = (env.get(TASKFLOW_PALACE_ENV, "") or "").strip()
    if override:
        return Path(override)
    cfg_file = mem_config_path(env)
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8")) if cfg_file else {}
    except (OSError, ValueError):
        cfg = {}
    root = str(cfg.get("palace_path") or "").strip()
    if not root:
        raise TaskPalaceUnavailable("no palace_path in ~/.mempalace/config.json")
    return Path(root)


def preflight(environ: dict[str, str] | None = None) -> bool:
    """Gate check: palace root must EXIST (dir). Raises on failure (fail-visible)."""
    root = palace_root(environ)
    if not root.is_dir():
        raise TaskPalaceUnavailable(f"palace root missing: {root}")
    probe = root / "tasks"
    probe.mkdir(parents=True, exist_ok=True)  # namespace dir creation is a harmless failure
    if not probe.is_dir():
        raise TaskPalaceUnavailable(f"tasks namespace not creatable: {probe}")
    return True


@dataclass
class Brief:
    task_id: str
    persona: str
    goal: str
    acceptance: list[str]
    room: str
    text: str
    turn_scope: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "persona": self.persona, "goal": self.goal,
            "acceptance": self.acceptance, "room": self.room, "turn_scope": self.turn_scope,
        }


def build_brief(
    task_id: str,
    persona: str,
    room: str,
    goal: str,
    *,
    acceptance: list[str] | None = None,
    agent: str | None = None,
    environ: dict[str, str] | None = None,
    palace_sections: list[str] | None = None,
) -> Brief:
    """Assemble the minimal brief: goal + AC + budget + MemPalace + claim discipline.

    Budget line quotes the live caps verbatim (golden 2 — budget walls bind).
    Memory pulls use the mempalace LITE recall for (room, agent) — advisory
    blocks only, never binding (id:room-turns advisory rule).
    """
    preflight(environ)
    agent = agent or persona
    ledger = spend.load_ledger()
    monthly = f"${float(ledger.get('monthly_usd') or 0):.2f}"
    lines = [
        f"task-brief: {task_id} ({persona})",
        f"goal: {goal.strip()}",
    ]
    for ac in acceptance or ["output is machine-checkable per ENVELOPES.md"]:
        lines.append(f"ac: {ac}")
    lines.append(
        "budget: this task runs under chip spend caps — monthly LIVE "
        f"{monthly} of $10.00 HARD / $5.00 WARN; session walls apply; refuse digs (rule 2)"
    )
    lines.append("claim: you own the scope via chip.claims first-claim-wins — ONE claim, ONE canonical path (claim line:"
                 f" claim: {persona} {task_id.lower()})")
    mem = mempalace.load_memories(room, agent)
    if mem:
        lines.append(f"memory (advisory, top-{min(3, max(1, len(mem)))}):")
        picked = mempalace.select_top_k(mem, goal, k=3, token_budget=400)
        for row in mempalace.format_recall_block(picked).splitlines():
            lines.append(f"  {row}")
    else:
        lines.append("memory: (none for this room/agent — first task on this lane)")
    lines.append(
        "deliverable: fix-package/diagnosis ONLY if lane requires; "
        + pack_values.taskflow_deliverable_receipt_clause()
    )
    return Brief(task_id=task_id, persona=persona, goal=goal,
                 acceptance=acceptance or [], room=room,
                 text="\n".join(lines))


def claim_if_free(room: str, persona: str, scope: str) -> str:
    """Append the claim envelope line to the room transcript; fail if owned."""
    from chip.lanes import decide, persona_root
    from chip.speaker import require_match

    require_match(persona)
    if decide(scope, []) == "deny":
        raise PermissionError("deny")
    if "/" in scope or "\\" in scope:
        root = persona_root(persona)
        if root is None or not Path(scope).expanduser().resolve().is_relative_to(root):
            raise PermissionError("deny")
    existing = claims.claim_owner(room, scope)
    if existing and existing != persona:
        raise TaskScopeConflict(f"{room}:{scope} already claimed by {existing}")
    line = f"claim: {persona} {scope}"
    from chip import store
    store.log_message(room, "user", line, agent=persona)
    who = claims.claim_owner(room, scope)
    if who != persona:
        raise TaskScopeConflict(f"claim lost race for {room}:{scope} -> {who}")
    return line


def palace_task_path(room: str, agent: str, root: Path | None = None) -> Path:
    """M2 namespace: <palace>/tasks/<room>/<agent>.jsonl (append-only, sanitized)."""
    base = root if root is not None else palace_root()
    day_dir = base / "tasks" / safe_room_name(room)
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir / f"{safe_room_name(agent)}.jsonl"


def write_task_memory(
    room: str,
    agent: str,
    *,
    task_id: str,
    task_type: str,
    outcome: str,
    receipts: list[str],
    lessons: str,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """M2 closing write per task cycle (spec fields verbatim).

    environ threads EVERYWHERE (preflight + path resolution): tests must run
    an isolated palace — a test leaking into the shared cross-host palace is
    exactly the pollution class this fail-visible gate exists to prevent.
    """
    root = palace_root(environ)
    preflight(environ)
    record = {
        "task_id": task_id,
        "agent": agent,
        "task_type": task_type,
        "outcome": outcome,
        "receipts": receipts,
        "lessons": lessons,
        "verified_as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = palace_task_path(room, agent, root)
    existed = path.exists()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return {"path": str(path), "created": not existed, "written": 1}


def bind_result(
    task_id: str,
    room: str,
    agent: str,
    outcome: str,
    *,
    receipts: list[str],
    lessons: str,
    task_type: str,
    done_gate_ok: bool | None = None,
    linear_anchor: str | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Result binding: /tasks ledger file + M2 palace write + Linear-comment body.

    The comment text is RETURNED (posting happens in the agent lane's MCP step);
    the module never pretends to have posted (golden 1). done-gate reference:
    receipts presence = soft PASS; flipping Linear Done stays the separate
    done-gate law (chip.done_gate — stamps bound in section, else refuse).
    """
    if done_gate_ok is None:
        done_gate_ok = _done_gate_reference(receipts)
    verdict = "PASS" if done_gate_ok else "REFUSE"
    comment_body = (
        f"task result: {task_id} — {agent}\n"
        f"class: {task_type}, receipt-anchored\n"
        f"outcome: {outcome}\n"
        f"receipts: {', '.join(receipts) if receipts else '(none)'}\n"
        f"done-gate verdict: {verdict} (soft, reference only — Done flips follow chip.done_gate law)"
    )
    from chip import store
    store.log_message(room, "system", f"task result bound: {task_id} ({agent})",
        extra={
            "task_id": task_id, "agent": agent,
            "task_type": task_type, "outcome": outcome,
            "receipts": receipts,
            "linear_anchor": linear_anchor,
        })
    palace = write_task_memory(room, agent, task_id=task_id, task_type=task_type,
                               outcome=outcome, receipts=receipts, lessons=lessons,
                               environ=environ)
    return {"linear_comment": comment_body, "palace": palace}


def _done_gate_reference(receipts: list[str]) -> bool:
    # done-gate hook (verify-as-of law): a ticket-anchored task with receipts gets
    # a soft PASS here; flipping Linear Done stays the done-gate's separate law.
    return bool(receipts)
