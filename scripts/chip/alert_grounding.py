"""Alert -> recipe-id grounding (engine parity plan slice E).

Pure matcher over the D-schema ``match`` field (``chip.recipes.schema``): an
alert dict is compared to zero or more recipes and the best fully-matched
recipe wins, with a deterministic confidence and a reason that names the keys
that matched. No transport here (Discord/monitoring wiring is pack/relay
work), no mutating action, no invented runner: callers hand the recipe id to
``chip.recipes.exec.grounded_recipe_go`` themselves, which keeps the evidence
and approval boundaries that live there.

Semantics (documented so the matcher cannot drift into a second schema):

* ``recipe.match`` is a flat JSON object of ``key -> expected``. Every
  declared condition must match the corresponding alert field:
  - strings compare case-insensitively after ``str.strip()``;
  - booleans, ints, floats compare by equality (``True`` and ``1`` are equal);
  - lists compare as sets of Python scalars (any-of on the alert side);
  - nested objects are not traversed — keep match conditions flat;
  - a condition expecting a value the alert does not carry is a MISS, so an
    alert class with missing metadata grounds to nothing (fail to no-match,
    never invent).
* All conditions must match for a recipe to be a candidate; confidence then
  rises with the number of matched conditions: ``n / (n + 1)`` (>=0.5, never
  1.0 — grounding is advisory input to a GO gate, not a verdict, so 1.0 is
  reserved for the recipe exec itself).
* A recipe with an EMPTY ``match`` matches nothing: alerts may not be
  grounded by accident.
* Ties (equal condition counts) break on the lexicographically smaller
  ``recipe_id``, so the result is deterministic for the same inputs.

``ground()`` is pure; the flag check lives in ``ground_for_turn`` so
deployments must opt in before wiring transport.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

GROUNDING_ENV = "CHIP_ALERT_GROUNDING"


@dataclass(frozen=True)
class RecipeMatch:
    recipe_id: str
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
        }


def grounding_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """True only when ``CHIP_ALERT_GROUNDING`` is an explicit truthy value."""
    env = environ if environ is not None else os.environ
    return env.get(GROUNDING_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _condition_matches(expected: Any, actual: Any) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.strip().lower() == expected.strip().lower()
    if isinstance(actual, str) or isinstance(expected, str):
        return False  # string/non-string cross-type is a miss, never a partial
    if isinstance(actual, (list, tuple, set)):
        return expected in list(actual)
    # Booleans/ints/floats cross-compare by equality (True == 1) per the docs.
    return actual == expected


def _conditions_match(match: Mapping[str, Any], alert: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    hit: list[str] = []
    miss: list[str] = []
    for key, expected in match.items():
        if key not in alert:
            miss.append(key)
            continue
        if _condition_matches(expected, alert[key]):
            hit.append(key)
        else:
            miss.append(key)
    return hit, miss


def ground(
    alert: Mapping[str, Any] | None,
    recipes: Iterable[Mapping[str, Any]] | Iterable[Any],
) -> RecipeMatch | None:
    """Best fully-matched D-schema recipe for ``alert``, or None.

    Invalid recipe rows are skipped (not fatal): grounding reconsumes the
    same validation as the registry but a broken row must not crash an
    ingest path; the reason string in a ``RecipeMatch`` only ever names a
    recipe that exists.
    """
    if not isinstance(alert, Mapping) or not alert:
        return None
    best: tuple[int, RecipeMatch] | None = None
    for raw in recipes:
        if not isinstance(raw, Mapping):
            continue
        try:
            from chip.recipes.schema import validate_recipe  # local import: schema imports stay cheap

            recipe = validate_recipe(dict(raw))
        except (ValueError, TypeError):
            continue
        match = dict(recipe.match or {})
        if not match:
            continue  # empty match matches nothing, by design
        hit, _miss = _conditions_match(match, alert)
        if len(hit) != len(match):
            continue
        confidence = len(hit) / (len(hit) + 1)
        reason = "matched " + ", ".join(sorted(hit))
        candidate = (len(hit), RecipeMatch(recipe_id=recipe.id, confidence=confidence, reason=reason))
        if best is None or candidate[0] > best[0] or (
            candidate[0] == best[0] and candidate[1].recipe_id < best[1].recipe_id
        ):
            best = candidate
    return best[1] if best else None


def ground_for_turn(
    alert: Mapping[str, Any] | None,
    recipes: Iterable[Any],
    environ: Mapping[str, str] | None = None,
) -> RecipeMatch | None:
    """Flag-gated wrapper for turn-time use (``CHIP_ALERT_GROUNDING``)."""
    if not grounding_enabled(environ):
        return None
    return ground(alert, recipes)
