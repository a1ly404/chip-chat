"""Relay-stamped speaker. Collab rejects a caller argument that disagrees."""

from __future__ import annotations

import os

SPEAKER_ENV = "CHIP_TURN_SPEAKER"


def current() -> str:
    return os.environ.get(SPEAKER_ENV, "").strip()


def require_match(claimed: str) -> None:
    stamped = current()
    if stamped and stamped != claimed:
        raise PermissionError("speaker")
