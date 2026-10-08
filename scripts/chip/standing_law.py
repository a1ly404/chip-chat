"""Standing law bundle (Spec A): law.md rules + session_boot injection order."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from chip import config, constraints, system_pack

_RULE_HEADER = re.compile(r"^###\s+id:(\S+)\s*$", re.IGNORECASE)
_META = re.compile(r"^(severity|owner|applies_to):\s*(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class LawRule:
    id: str
    text: str
    severity: str
    owner: str
    applies_to: tuple[str, ...]


def law_bundle_path() -> Path:
    return config.data_dir() / "law.md"


def seed_law_bundle_if_missing() -> Path:
    path = law_bundle_path()
    if path.is_file():
        return path
    try:
        seed: Path | None = system_pack.surface("standing_law", system_pack.load_pack())
    except (OSError, ValueError, KeyError):
        seed = None
    path.parent.mkdir(parents=True, exist_ok=True)
    if seed is not None:
        path.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        path.write_text("", encoding="utf-8")
    return path


def parse_law_rules(text: str) -> list[LawRule]:
    rules: list[LawRule] = []
    current_id: str | None = None
    meta: dict[str, str] = {}
    body: list[str] = []

    def flush() -> None:
        nonlocal current_id, meta, body
        if not current_id:
            return
        rules.append(
            LawRule(
                id=current_id,
                text="\n".join(body).strip(),
                severity=(meta.get("severity") or "SOFT").upper(),
                owner=meta.get("owner") or "PM",
                applies_to=tuple(t.strip() for t in (meta.get("applies_to") or "*").split(",") if t.strip()),
            )
        )
        current_id = None
        meta = {}
        body = []

    for line in text.splitlines():
        hdr = _RULE_HEADER.match(line.strip())
        if hdr:
            flush()
            current_id = hdr.group(1)
            continue
        if current_id:
            m = _META.match(line.strip())
            if m:
                meta[m.group(1).lower()] = m.group(2).strip()
                continue
            body.append(line)
    flush()
    return rules


def load_law_rules() -> list[LawRule]:
    path = seed_law_bundle_if_missing()
    return parse_law_rules(path.read_text(encoding="utf-8"))


def _tag_matches(tag: str, room: str, persona: str) -> bool:
    tag = tag.strip()
    if tag in ("*", "all"):
        return True
    if tag.startswith("room:"):
        return tag[5:].strip() == room
    if tag.startswith("persona:"):
        return tag[8:].strip() == persona
    return tag == room or tag == persona


def rule_applies(rule: LawRule, room: str, persona: str) -> bool:
    return any(_tag_matches(t, room, persona) for t in rule.applies_to)


def format_hard_law_block(room: str, persona: str) -> str:
    hard = [r for r in load_law_rules() if r.severity == "HARD" and rule_applies(r, room, persona)]
    if not hard:
        return ""
    lines = [
        "STANDING LAW (HARD, binding). Room/persona-tagged rules from law.md.",
    ]
    for r in hard:
        lines.append(f"- [{r.id}] ({r.owner}) {r.text}")
    return "\n".join(lines)


def session_boot_system_prefix(
    room: str,
    persona: str,
    *,
    advisory_memory_block: str = "",
) -> str:
    """
    Inject order for system prompt prefix (turns are separate messages):
    (1) HARD constraints, (2) HARD law.md for tags, (4) advisory memory labeled not_law.
    """
    parts: list[str] = []
    hard_c = constraints.format_hard_constraints_block()
    if hard_c:
        parts.append(hard_c)
    hard_law = format_hard_law_block(room, persona)
    if hard_law:
        parts.append(hard_law)
    adv = (advisory_memory_block or "").strip()
    if adv:
        parts.append(
            "ADVISORY MEMORY (not_law). Does not override HARD constraints or HARD standing law.\n"
            + adv
        )
    return "\n\n".join(parts)


def boot_payload_for_audit(room: str, persona: str, *, advisory_memory_block: str = "") -> dict[str, str]:
    """Structured boot sections for tests (T3)."""
    return {
        "hard_constraints": constraints.format_hard_constraints_block(),
        "hard_law": format_hard_law_block(room, persona),
        "advisory_memory": advisory_memory_block,
    }
