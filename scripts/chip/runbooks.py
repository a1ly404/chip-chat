"""Runbook registry (Grok round-6 gap): id → auto | propose | blocked.

Graduation law (id:auto-heal): a fixer may auto-execute ONLY runbooks whose class
is ``auto``. Everything else is propose-only until an operator graduates it. Unknown ids
fail-closed to ``propose`` — never auto. Blocked ids are never autonomous
(destructive data operations / credential flips — the SYSTEM AutoFix incident class).
"""

from __future__ import annotations

import json
from pathlib import Path

from chip import config
from chip import system_pack

DEFAULT_REGISTRY: dict[str, str] = {
    # auto = idempotent, named target, rollback = one command
    "restart-named-service": "auto",
    "clear-stale-suppress": "auto",
    "re-run-health-probe": "auto",
    "post-learning": "auto",
    # propose = operator reviews the proposal card
    "compose-recreate": "propose",
    "dead-image": "propose",
    "disk-cleanup": "propose",
    "encode": "propose",
    # blocked = never autonomous (incident classes); pack runbooks surface may add more
    "destructive-data-op": "blocked",
    "credential-flip": "blocked",
    "media-delete": "blocked",
}


def registry_path() -> Path:
    return system_pack.surface("runbooks", system_pack.load_pack())


def load_registry() -> dict[str, str]:
    p = registry_path()
    if not p.is_file():
        return dict(DEFAULT_REGISTRY)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            merged = dict(DEFAULT_REGISTRY)
            merged.update({str(k): str(v) for k, v in raw.items()})
            return merged
    except json.JSONDecodeError:
        pass
    return dict(DEFAULT_REGISTRY)


def runbook_class(runbook_id: str, registry: dict[str, str] | None = None) -> str:
    """Class for a runbook id; UNKNOWN ids fail-closed to 'propose'."""
    reg = registry if registry is not None else load_registry()
    return reg.get((runbook_id or "").strip().lower(), "propose")


def may_autorun(runbook_id: str, registry: dict[str, str] | None = None) -> bool:
    return runbook_class(runbook_id, registry) == "auto"
