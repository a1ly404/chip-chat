"""Advisory persona learnings for relay turns (engine parity slice F).

Reads a persona's ``learnings.md`` and returns capped advisory lines.
Injection into room / relay system prompts is gated by ``CHIP_RELAY_LEARNINGS``
(default off). Never writes; never overrides ROOM LAW.
"""

from __future__ import annotations

import os
from pathlib import Path

from chip import system_pack

MAX_LINES = 32
MAX_BYTES = 4096
RELAY_LEARNINGS_ENV = "CHIP_RELAY_LEARNINGS"
ADVISORY_LABEL = "PERSONA LEARNINGS (advisory only; never overrides ROOM LAW):"


def learnings_path(agent: str) -> Path:
    return system_pack.surface("persona_dir", system_pack.load_pack()) / agent / "learnings.md"


def relay_learnings_enabled(environ: dict[str, str] | None = None) -> bool:
    """True only when ``CHIP_RELAY_LEARNINGS`` is an explicit truthy value."""
    env = environ if environ is not None else os.environ
    return env.get(RELAY_LEARNINGS_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def load_learnings(path: str | Path, *, max_lines: int = MAX_LINES, max_bytes: int = MAX_BYTES) -> list[str]:
    """Return advisory learning lines from ``path`` under hard caps.

    - Markdown headers (``#``), blank lines, and the "not loaded at runtime yet"
      stub banner are skipped.
    - Line cap applies first (newest-last wins: the stub is append-only, so the
      most recent lines are kept).
    - Byte cap is enforced on the joined result.
    """
    file = Path(path)
    if not file.is_file():
        return []
    lines: list[str] = []
    for raw in file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("not loaded at runtime"):
            continue
        lines.append(line)
    lines = lines[-max_lines:]
    out: list[str] = []
    total = 0
    for line in reversed(lines):
        cost = len(line.encode("utf-8")) + 1
        if total + cost > max_bytes:
            break
        out.append(line)
        total += cost
    out.reverse()
    return out


def render_learnings_block(
    agent: str, *, max_lines: int = MAX_LINES, max_bytes: int = MAX_BYTES
) -> str:
    """Advisory learnings block text; "" when nothing to add.

    Does **not** check ``CHIP_RELAY_LEARNINGS`` — callers that inject into relay
    turns must use ``advisory_learnings_for_turn`` so the flag stays default-off.
    """
    lines = load_learnings(learnings_path(agent), max_lines=max_lines, max_bytes=max_bytes)
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return f"{ADVISORY_LABEL}\n{body}"


def advisory_learnings_for_turn(
    agent: str,
    *,
    environ: dict[str, str] | None = None,
    max_lines: int = MAX_LINES,
    max_bytes: int = MAX_BYTES,
) -> str:
    """Flag-gated advisory block for room/relay turns.

    Returns "" when ``CHIP_RELAY_LEARNINGS`` is unset/false (safe default), or when
    the persona has no loadable learnings. Read-only; never writes files.
    """
    if not relay_learnings_enabled(environ):
        return ""
    return render_learnings_block(agent, max_lines=max_lines, max_bytes=max_bytes)
