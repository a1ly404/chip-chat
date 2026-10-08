"""Mandatory signed-in verifier gate for scoped GO actions (mock-friendly)."""

from __future__ import annotations

import os


def verifier_signed_in_for(target: str, *, environ: dict[str, str] | None = None) -> bool:
    """True when a signed-in verifier session exists for ``target`` (container or runbook)."""
    env = environ if environ is not None else os.environ
    signed = (env.get("CHIP_GO_VERIFIER_SIGNED_IN") or "").strip().lower()
    if not signed:
        return False
    want = (target or "").strip().lower()
    return signed == want or signed == "*"
