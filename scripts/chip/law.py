"""Amendment-5 room law: binding markdown plus advisory memory notes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from chip.paths import REPO_ROOT
from chip import system_pack

_EMPTY_CHANNELS = REPO_ROOT / "var" / "empty_channel_law"


def _channels_dir() -> Path:
    path = system_pack.optional_surface("channel_law_dir")
    if path is not None:
        return path
    _EMPTY_CHANNELS.mkdir(parents=True, exist_ok=True)
    return _EMPTY_CHANNELS


def __getattr__(name: str):
    if name == "CHANNELS_DIR":
        return _channels_dir()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
_SECTION = re.compile(r"^##\s+(law|memory)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class RoomLaw:
    room: str
    path: Path | None
    law: str
    memory: str


def safe_room_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name.strip())


def parse_law_markdown(text: str) -> tuple[str, str]:
    """Split a channel file into binding law and advisory memory.

    Without ``## Law`` / ``## Memory`` headings, the whole file is binding law.
    """
    sections: dict[str, list[str]] = {"_pre": [], "law": [], "memory": []}
    current = "_pre"
    found = False
    for line in text.splitlines():
        match = _SECTION.match(line.strip())
        if match:
            found = True
            current = match.group(1).lower()
            continue
        sections[current].append(line)
    if not found:
        return text.strip(), ""
    preamble = "\n".join(sections["_pre"]).strip()
    law = "\n".join(sections["law"]).strip()
    if preamble and law:
        law = preamble + "\n\n" + law
    elif preamble:
        law = preamble
    memory = "\n".join(sections["memory"]).strip()
    return law, memory


def load_room_law(room: str) -> RoomLaw:
    safe = safe_room_name(room) or "room"
    path = _channels_dir() / f"{safe}.md"
    if not path.is_file():
        return RoomLaw(room=room, path=None, law="", memory="")
    law, memory = parse_law_markdown(path.read_text(encoding="utf-8"))
    return RoomLaw(room=room, path=path, law=law, memory=memory)


def law_label(room_law: RoomLaw) -> str:
    if room_law.path is None:
        return "law=(none)"
    try:
        rel = room_law.path.relative_to(REPO_ROOT)
    except ValueError:
        rel = room_law.path
    return f"law={rel.as_posix()}"


def compose_system(
    persona_system: str,
    room_law: RoomLaw,
    *,
    mined_memory_block: str = "",
    session_boot_prefix: str = "",
    learnings_block: str = "",
) -> str:
    """Law is binding. Memory notes and mined memories never override it."""
    parts: list[str] = []
    boot = (session_boot_prefix or "").strip()
    if boot:
        parts.append(boot)
    if room_law.law.strip():
        parts.append(
            "ROOM LAW (binding). Follow this exactly. "
            "It overrides persona style and any memory notes.\n"
            + room_law.law.strip()
        )
    if room_law.memory.strip():
        parts.append(
            "MEMORY NOTES (advisory only). "
            "Use these only when they do not conflict with room law.\n"
            + room_law.memory.strip()
        )
    mined = (mined_memory_block or "").strip()
    if mined and not boot:
        parts.append(mined)
    learnings = (learnings_block or "").strip()
    if learnings:
        parts.append(learnings)
    if not parts:
        return persona_system
    return "\n\n".join(parts) + "\n\n" + persona_system
