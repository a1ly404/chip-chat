"""Restart allow/deny helpers — fail-closed when pack or monitoring policy is absent."""

from __future__ import annotations

import sys
from typing import Any

from chip import pack_values, system_pack


def _log_refusal(channel: str, detail: str) -> None:
    print(f"[{channel}] {detail}", file=sys.stderr, flush=True)


def system_pack_loaded() -> bool:
    try:
        pack = system_pack.load_pack()
    except (OSError, ValueError):
        return False
    return bool(str(pack.get("name") or "").strip())


def solver_restart_allowlist(environ: dict[str, str]) -> frozenset[str] | None:
    """Config allowlist narrowed by CASE_INTAKE_SOLVER_RESTART_ALLOW; None if pack not loaded."""
    if not system_pack_loaded():
        return None
    base = frozenset(str(x).strip().lower() for x in pack_values.service_allowlist() if str(x).strip())
    raw = (environ.get("CASE_INTAKE_SOLVER_RESTART_ALLOW") or "").strip()
    if not raw:
        return base
    env_only = frozenset(x.strip().lower() for x in raw.split(",") if x.strip())
    return base & env_only


def solver_forbidden_container_names() -> frozenset[str]:
    if not system_pack_loaded():
        return frozenset()
    try:
        from chip_relay.monitoring_skills import load_policy
    except (ImportError, ModuleNotFoundError):
        return frozenset()
    try:
        return load_policy().forbidden_restart_containers
    except (OSError, ValueError):
        return frozenset()
