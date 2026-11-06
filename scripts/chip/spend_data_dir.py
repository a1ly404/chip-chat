"""Fail-closed spend data-dir pin (engine parity plan slice B).

The spend ledger used to fall back to ``REPO_ROOT / var / chip-chat`` whenever
``CHIP_CHAT_DATA_DIR`` was unset, so every engine worktree or two different
checkouts on one machine silently kept its own ledger and the $5 monthly
cutoff was evaluated against the wrong spend history. With
``CHIP_SPEND_PIN_DATA_DIR=1`` the live-completion gate refuses to run at all
until ``CHIP_CHAT_DATA_DIR`` is set to an absolute path — fail closed before
any API call or ledger write. Mock runs never reach this gate, so mock stays
free regardless of the flag.
"""

from __future__ import annotations

import os
from pathlib import Path

from chip.config import data_dir

PIN_ENV = "CHIP_SPEND_PIN_DATA_DIR"


class SpendDataDirNotPinned(RuntimeError):
    """``CHIP_SPEND_PIN_DATA_DIR=1`` but the spend data dir is not pinned."""


def pin_enabled(environ: dict[str, str] | None = None) -> bool:
    """True only when ``CHIP_SPEND_PIN_DATA_DIR`` is an explicit truthy value."""
    env = environ if environ is not None else os.environ
    return env.get(PIN_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def pinned_data_dir(environ: dict[str, str] | None = None) -> Path:
    """Resolved data dir for spend state when the pin is on.

    Requires an absolute ``CHIP_CHAT_DATA_DIR``; refuses the HOME fallback, the
    repo-local ``var/chip-chat`` fallback (which is cwd/worktree-anchored) and
    relative paths. Raises ``SpendDataDirNotPinned`` otherwise.
    """
    env = environ if environ is not None else os.environ
    raw = env.get("CHIP_CHAT_DATA_DIR", "").strip()
    if not raw:
        raise SpendDataDirNotPinned(
            f"{PIN_ENV}=1 requires an absolute CHIP_CHAT_DATA_DIR; "
            "the cwd/repo-local spend fallback is refused (fail closed)"
        )
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise SpendDataDirNotPinned(
            f"{PIN_ENV}=1 requires CHIP_CHAT_DATA_DIR to be absolute, got {raw!r} "
            "(relative paths are worktree-anchored)"
        )
    return path


def ensure_pinned_data_dir(environ: dict[str, str] | None = None) -> Path:
    """Fail-closed gate step: return the pinned dir or raise.

    The returned path equals ``chip.config.data_dir()`` in this state, so the
    ledger files (``spend.json`` / ``spend.jsonl``) resolve inside the pinned
    dir and every worktree that pins the same env shares one cutoff decision.
    """
    pinned = pinned_data_dir(environ)
    resolved = data_dir()
    if resolved != pinned:
        raise SpendDataDirNotPinned(
            f"spend dir drift: pinned {pinned} resolves to {resolved}"
        )
    return pinned
