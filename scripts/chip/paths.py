"""Repo root, import-free so chip.system_pack and chip.config can both use it."""

from __future__ import annotations

from pathlib import Path

_MARKER_FILES = ("pyproject.toml", "BUILD_SHA.txt")
_MARKER_DIRS = ("systems", "scripts/chip")


def find_repo_root(start: Path | None = None) -> Path:
    """Locate the chip-chat project root from a checkout or editable install."""
    here = (start or Path(__file__).resolve()).resolve()
    for candidate in (here, *here.parents):
        if any((candidate / name).is_file() for name in _MARKER_FILES):
            if (candidate / "scripts" / "chip").is_dir():
                return candidate
        if (candidate / "systems").is_dir() and (candidate / "scripts" / "chip").is_dir():
            return candidate
    # Normal install: package lives under site-packages/chip; no systems tree.
    return here.parents[2]


REPO_ROOT = find_repo_root()
