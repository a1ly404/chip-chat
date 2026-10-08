"""HARD/SOFT constraints store (Spec A) — persisted under CHIP_CHAT_DATA_DIR."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip import config, store

MAX_HARD_CONSTRAINTS = 50

CONSTRAINT_LINE = re.compile(
    r"^CONSTRAINT:(HARD|SOFT)\s+(.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Constraint:
    id: str
    rule_id: str
    value: str
    source_turn: str
    expires_at: str | None
    hard: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "value": self.value,
            "source_turn": self.source_turn,
            "expires_at": self.expires_at,
            "hard": self.hard,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Constraint:
        return cls(
            id=str(raw["id"]),
            rule_id=str(raw.get("rule_id") or raw["id"]),
            value=str(raw["value"]),
            source_turn=str(raw.get("source_turn") or ""),
            expires_at=raw.get("expires_at") if raw.get("expires_at") else None,
            hard=bool(raw.get("hard")),
        )


def constraints_path() -> Path:
    return config.data_dir() / "constraints.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return f"c-{uuid.uuid4().hex[:10]}"


def load_constraints(*, include_expired: bool = False) -> list[Constraint]:
    path = constraints_path()
    if not path.is_file():
        return []
    now = _now_iso()
    out: list[Constraint] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        try:
            c = Constraint.from_dict(raw)
        except (KeyError, TypeError):
            continue
        if not include_expired and c.expires_at and c.expires_at <= now:
            continue
        out.append(c)
    return out


def append_constraint(constraint: Constraint) -> None:
    store.append_jsonl(constraints_path(), constraint.to_dict())


def count_hard() -> int:
    return sum(1 for c in load_constraints() if c.hard)


def parse_constraint_message(text: str) -> tuple[bool, str, str] | None:
    """Return (hard, value, remainder) if message is CONSTRAINT:HARD|SOFT ..."""
    for line in text.strip().splitlines():
        match = CONSTRAINT_LINE.match(line.strip())
        if match:
            kind = match.group(1).upper()
            value = match.group(2).strip()
            return kind == "HARD", value, line
    match = CONSTRAINT_LINE.match(text.strip())
    if not match:
        return None
    kind = match.group(1).upper()
    return kind == "HARD", match.group(2).strip(), text.strip()


def try_add_from_message(
    text: str,
    *,
    author_role: str,
    source_turn: str,
    rule_id: str | None = None,
) -> str | None:
    """
    Operator-only CONSTRAINT lines. Returns error token (e.g. BLOCKED:constraint_cap) or None on success.
    Non-operator messages are ignored (no error).
    """
    from chip import system_pack

    if author_role != system_pack.pack_identity_string("operator_name", default="Operator"):
        return None
    parsed = parse_constraint_message(text)
    if not parsed:
        return None
    hard, value, _ = parsed
    if hard and count_hard() >= MAX_HARD_CONSTRAINTS:
        return "BLOCKED:constraint_cap"
    cid = _new_id()
    rid = rule_id or cid
    append_constraint(
        Constraint(
            id=cid,
            rule_id=rid,
            value=value,
            source_turn=source_turn,
            expires_at=None,
            hard=hard,
        )
    )
    return None


def add_from_tool(
    *,
    actor: str,
    value: str,
    hard: bool,
    source_turn: str,
    rule_id: str | None = None,
) -> str | None:
    """constraints.add with pack operator actor only."""
    from chip import system_pack

    if actor != system_pack.pack_identity_string("operator_name", default="Operator"):
        return None
    if hard and count_hard() >= MAX_HARD_CONSTRAINTS:
        return "BLOCKED:constraint_cap"
    cid = _new_id()
    rid = rule_id or cid
    append_constraint(
        Constraint(
            id=cid,
            rule_id=rid,
            value=value,
            source_turn=source_turn,
            expires_at=None,
            hard=hard,
        )
    )
    return None


_FORBIDDEN_WORD = re.compile(r"[a-z]{3,}", re.IGNORECASE)


def hard_constraint_conflicts(constraint: Constraint, user_message: str) -> bool:
    """Mechanical conflict: HARD value 'never <phrase>' vs user message word overlap."""
    if not constraint.hard:
        return False
    value = constraint.value.strip().lower()
    user = user_message.strip().lower()
    if not value or not user:
        return False
    if value.startswith("never "):
        phrase = value[6:].strip()
        for word in _FORBIDDEN_WORD.findall(phrase):
            if re.search(rf"\b{re.escape(word.lower())}\b", user):
                return True
    return False


def first_hard_block(user_message: str) -> str | None:
    """Return BLOCKED:<constraint.id> for the first conflicting HARD constraint."""
    for c in load_constraints():
        if hard_constraint_conflicts(c, user_message):
            return f"BLOCKED:{c.id}"
    return None


def format_hard_constraints_block() -> str:
    hard = [c for c in load_constraints() if c.hard]
    if not hard:
        return ""
    lines = [
        "HARD CONSTRAINTS (binding). These override advisory memory and user requests.",
    ]
    for c in hard:
        lines.append(f"- [{c.id}] {c.value}")
    return "\n".join(lines)
