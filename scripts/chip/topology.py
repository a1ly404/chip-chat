"""Topology lock — solo default, mothballed swarm/trio, delegate hop cap (SoT: pack chip_topology.json)."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from chip.paths import REPO_ROOT
from chip import system_pack

TOPOLOGY_REFUSAL = "topology: swarm/trio mothballed — solo default"

_DEFAULT_TOPOLOGY: dict[str, Any] = {
    "default_topology": "pm_hub",
    "one_persona_per_go": True,
    "interface": {"human_talks_to": "pm"},
    "swarm": {"mothballed": True, "min_participants": 2, "unlock_env": "CHIP_TOPOLOGY_SWARM_GO"},
    "ops_ab_harness": {"mothballed": True, "trio_requires_swarm_unlock": True},
    "collab_ab": {"mothballed": True},
    "delegate": {
        "max_hops_per_chain": 3,
        "no_agent_to_agent_reply_chains": True,
    },
    "relay": {"auto_multi_persona_fanout": False},
    "pm_orchestrator": {
        "enabled_default": False,
        "unlock_env": "CHIP_RELAY_PM_ORCHESTRATOR",
        "tick_after_specialist_receipt": True,
    },
    "visible_room": {"goal_is_human_visibility_not_agent_coherence": True},
}


def __getattr__(name: str):
    if name == "TOPOLOGY_FILE":
        path = system_pack.optional_surface("topology")
        if path is None:
            raise FileNotFoundError("topology surface missing")
        return path
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    path = system_pack.optional_surface("topology")
    if path is None:
        return dict(_DEFAULT_TOPOLOGY)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected object")
    return raw


def default_topology() -> str:
    return str(load().get("default_topology") or "pm_hub")


def human_entry_persona() -> str:
    iface = load().get("interface") or {}
    return str(iface.get("human_talks_to") or "pm")


def agent_to_agent_reply_chains_allowed() -> bool:
    delegate = load().get("delegate") or {}
    if delegate.get("no_agent_to_agent_reply_chains", True):
        return False
    visible = load().get("visible_room") or {}
    return not bool(visible.get("goal_is_human_visibility_not_agent_coherence", True))


def one_persona_per_go() -> bool:
    return bool(load().get("one_persona_per_go", True))


def max_delegate_hops() -> int:
    delegate = load().get("delegate") or {}
    hops = int(delegate.get("max_hops_per_chain", 3))
    if hops < 1 or hops > 3:
        path = system_pack.optional_surface("topology")
        label = path or "topology"
        raise ValueError(f"{label}: delegate.max_hops_per_chain must be 1..3")
    return hops


def _swarm_unlocked() -> bool:
    swarm = load().get("swarm") or {}
    if not swarm.get("mothballed", True):
        return True
    key = str(swarm.get("unlock_env") or "CHIP_TOPOLOGY_SWARM_GO")
    return os.environ.get(key, "").strip().lower() in ("1", "true", "yes")


def swarm_allowed(participant_count: int = 2) -> bool:
    """True when multi-persona collab / trio surfaces may run."""
    swarm = load().get("swarm") or {}
    min_p = int(swarm.get("min_participants", 2))
    if participant_count < min_p:
        return True
    return _swarm_unlocked()


def enforce_swarm_allowed(participant_count: int) -> None:
    if swarm_allowed(participant_count):
        return
    raise PermissionError(TOPOLOGY_REFUSAL)


def collab_ab_allowed() -> bool:
    block = load().get("collab_ab") or {}
    if not block.get("mothballed", True):
        return True
    return _swarm_unlocked()


def enforce_collab_ab() -> None:
    if collab_ab_allowed():
        return
    raise PermissionError(TOPOLOGY_REFUSAL)


def ops_ab_trio_arm_allowed(arm: str) -> bool:
    if arm not in ("trio", "both"):
        return True
    harness = load().get("ops_ab_harness") or {}
    if not harness.get("mothballed", True):
        return True
    if harness.get("trio_requires_swarm_unlock", True) and not _swarm_unlocked():
        return False
    return _swarm_unlocked()


def enforce_ops_ab_arm(arm: str) -> None:
    if ops_ab_trio_arm_allowed(arm):
        return
    raise PermissionError(TOPOLOGY_REFUSAL)


def relay_auto_fanout_enabled() -> bool:
    relay = load().get("relay") or {}
    return bool(relay.get("auto_multi_persona_fanout", False))


def pm_orchestrator_tick_enabled(environ: dict[str, str] | None = None) -> bool:
    """True when relay may schedule one pm tick after a specialist receipt."""
    block = load().get("pm_orchestrator") or {}
    if not block.get("tick_after_specialist_receipt", True):
        return False
    key = str(block.get("unlock_env") or "CHIP_RELAY_PM_ORCHESTRATOR")
    env = environ if environ is not None else os.environ
    val = (env.get(key) or "").strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "off", "no"}:
        return False
    return bool(block.get("enabled_default", False))
