"""Allowlisted tool manifest (no raw shell)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chip import system_pack
from chip.paths import REPO_ROOT

MANIFEST_ENV = "CHIP_TOOLS_MANIFEST"


def __getattr__(name: str):
    if name == "MANIFEST_FILE":
        path = system_pack.optional_surface("tools")
        if path is None:
            raise FileNotFoundError("tools surface missing")
        return path
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
VALID_RISKS = frozenset({RISK_LOW, RISK_MEDIUM, RISK_HIGH})


@dataclass(frozen=True)
class ToolSpec:
    id: str
    argv: tuple[str, ...]
    risk: str
    allow_extra_args: bool
    description: str = ""


def manifest_path() -> Path:
    override = os.environ.get(MANIFEST_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    path = system_pack.optional_surface("tools")
    if path is not None:
        return path
    return REPO_ROOT / "var" / "empty_tools_manifest.json"


def load_manifest_raw() -> dict[str, Any]:
    path = manifest_path()
    if not path.is_file():
        return {"version": 1, "tools": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {"version": 1, "tools": []}


def default_timeout_seconds() -> float:
    raw = load_manifest_raw().get("default_timeout_seconds", 30)
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return 30.0


def _parse_tool(entry: dict[str, Any]) -> ToolSpec | None:
    tid = str(entry.get("id") or "").strip()
    argv_raw = entry.get("argv")
    if not tid or not isinstance(argv_raw, list) or not argv_raw:
        return None
    argv = tuple(str(x) for x in argv_raw)
    risk = str(entry.get("risk") or RISK_MEDIUM).strip().lower()
    if risk not in VALID_RISKS:
        risk = RISK_MEDIUM
    allow_extra = bool(entry.get("allow_extra_args", False))
    desc = str(entry.get("description") or "")
    return ToolSpec(id=tid, argv=argv, risk=risk, allow_extra_args=allow_extra, description=desc)


def load_tools() -> dict[str, ToolSpec]:
    raw = load_manifest_raw()
    tools_list = raw.get("tools")
    if not isinstance(tools_list, list):
        return {}
    out: dict[str, ToolSpec] = {}
    for entry in tools_list:
        if not isinstance(entry, dict):
            continue
        spec = _parse_tool(entry)
        if spec:
            out[spec.id] = spec
    return out


def resolve_tool(name: str) -> ToolSpec | None:
    """Resolve by tool id (e.g. pytest, git-status)."""
    key = name.strip()
    if not key:
        return None
    return load_tools().get(key)


def list_tools() -> list[ToolSpec]:
    return sorted(load_tools().values(), key=lambda t: t.id)
