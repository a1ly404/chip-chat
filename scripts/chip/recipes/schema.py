"""Recipe JSON schema (engine-generic). Homelab content stays in packs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ALLOWED_STEP_KINDS = frozenset(
    {
        "probe",  # non-mutating health/status check
        "echo",  # non-mutating noop / fixture
        "fake",  # test-only runner kind
        "restart",  # mutating — A1 refuses live even when CHIP_RECIPE_EXEC=1
        "compose_recreate",  # mutating — A1 refuses live
    }
)


@dataclass(frozen=True)
class RecipeStep:
    kind: str
    args: dict[str, Any] = field(default_factory=dict)
    evidence_required: bool = True


@dataclass(frozen=True)
class Recipe:
    id: str
    version: str
    match: dict[str, Any] = field(default_factory=dict)
    steps: tuple[RecipeStep, ...] = ()
    mutating: bool = False
    mode: str = "shadow"  # advisory default in recipe data; exec gate owns live


def validate_recipe(raw: Any) -> Recipe:
    """Validate a recipe dict; raise ValueError on schema errors."""
    if not isinstance(raw, dict):
        raise ValueError("recipe must be an object")
    rid = str(raw.get("id") or "").strip()
    version = str(raw.get("version") or "").strip()
    if not rid or not version:
        raise ValueError("recipe requires non-empty id and version")
    match = raw.get("match") or {}
    if not isinstance(match, dict):
        raise ValueError("recipe.match must be an object")
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError("recipe.steps must be a non-empty list")
    steps: list[RecipeStep] = []
    for item in steps_raw:
        if not isinstance(item, dict):
            raise ValueError("recipe step must be an object")
        kind = str(item.get("kind") or "").strip()
        if kind not in ALLOWED_STEP_KINDS:
            raise ValueError(f"unknown step kind: {kind!r}")
        args = item.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError("step.args must be an object")
        ev = item.get("evidence_required", True)
        if not isinstance(ev, bool):
            raise ValueError("step.evidence_required must be bool")
        steps.append(RecipeStep(kind=kind, args=dict(args), evidence_required=ev))
    mutating = bool(raw.get("mutating", False))
    # Mutating kinds force mutating=True even if author omitted the flag.
    if any(s.kind in {"restart", "compose_recreate"} for s in steps):
        mutating = True
    mode = str(raw.get("mode") or "shadow").strip().lower() or "shadow"
    if mode not in {"off", "shadow", "live"}:
        raise ValueError(f"recipe.mode must be off|shadow|live, got {mode!r}")
    return Recipe(
        id=rid,
        version=version,
        match=dict(match),
        steps=tuple(steps),
        mutating=mutating,
        mode=mode,
    )
