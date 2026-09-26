"""Persona learnings loader (Slice A prep — runtime wiring pending).

Reads a persona's ``learnings.md`` and returns advisory lines under hard caps.
The room system-prompt injection (slice A proper) builds on this helper; the
caps are proven by ``test_chip_learnings.py`` so the loader can be wired
without re-deriving the cap discipline.
"""

from __future__ import annotations

from pathlib import Path

MAX_LINES = 32
MAX_BYTES = 4096


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
