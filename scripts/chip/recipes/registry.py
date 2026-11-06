"""Recipe registry — in-memory / JSON file. Packs may ship recipe data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chip.recipes.schema import Recipe, validate_recipe

# Built-in non-mutating fixture recipes (engine CI / acme). Packs may replace.
DEFAULT_RECIPES: list[dict[str, Any]] = [
    {
        "id": "acme-checkout-probe",
        "version": "1",
        "match": {"alert_class": "checkout-latency"},
        "mutating": False,
        "mode": "shadow",
        "steps": [
            {
                "kind": "probe",
                "args": {"target": "checkout-api", "check": "latency_p99"},
                "evidence_required": True,
            }
        ],
    },
    {
        "id": "acme-echo-ok",
        "version": "1",
        "match": {"alert_class": "synthetic-ok"},
        "mutating": False,
        "mode": "shadow",
        "steps": [{"kind": "echo", "args": {"msg": "ok"}, "evidence_required": False}],
    },
]


def load_registry(
    recipes: list[dict[str, Any]] | None = None,
    *,
    path: Path | str | None = None,
) -> dict[str, Recipe]:
    """Load and validate recipes. ``path`` JSON is a list of recipe objects."""
    rows: list[dict[str, Any]]
    if path is not None:
        p = Path(path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("recipe registry file must be a JSON list")
        rows = [r for r in raw if isinstance(r, dict)]
    elif recipes is not None:
        rows = list(recipes)
    else:
        rows = list(DEFAULT_RECIPES)
    out: dict[str, Recipe] = {}
    for row in rows:
        recipe = validate_recipe(row)
        if recipe.id in out:
            raise ValueError(f"duplicate recipe id: {recipe.id}")
        out[recipe.id] = recipe
    return out


def recipe_by_id(recipe_id: str, registry: dict[str, Recipe] | None = None) -> Recipe | None:
    reg = registry if registry is not None else load_registry()
    return reg.get((recipe_id or "").strip())
