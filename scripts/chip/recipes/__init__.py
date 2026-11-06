"""Generic recipe registry + grounded GO (slice A1 / plan slice D).

Public surface:
- ``CHIP_RECIPE_EXEC`` — default off; when on, allows registry-only live exec
  for **non-mutating** recipes via ``grounded_recipe_go``.
- Shadow / would-act paths stay available when the flag is off (no mutation).
- Homelab recipe *content* stays in private packs; this package is the engine
  schema + gate only.
"""

from __future__ import annotations

from chip.recipes.exec import (
    RECIPE_EXEC_ENV,
    FakeRunner,
    RecipeExecRefused,
    RecipeGoResult,
    StepResult,
    grounded_recipe_go,
    recipe_exec_enabled,
)
from chip.recipes.registry import DEFAULT_RECIPES, load_registry, recipe_by_id
from chip.recipes.schema import Recipe, RecipeStep, validate_recipe

__all__ = [
    "DEFAULT_RECIPES",
    "FakeRunner",
    "RECIPE_EXEC_ENV",
    "Recipe",
    "RecipeExecRefused",
    "RecipeGoResult",
    "RecipeStep",
    "StepResult",
    "grounded_recipe_go",
    "load_registry",
    "recipe_by_id",
    "recipe_exec_enabled",
    "validate_recipe",
]
