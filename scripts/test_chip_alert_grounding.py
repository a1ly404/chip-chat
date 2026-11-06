"""Engine parity plan slice E: alert -> recipe-id grounding (Refs RAZ-16).

Pure matcher over the D-schema ``match`` field; the transport (Discord /
monitoring wiring) stays pack/relay work and is not exercised here. Offline,
synthetic fixtures only (no real host names).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chip.alert_grounding import (
    GROUNDING_ENV,
    RecipeMatch,
    grounding_enabled,
    ground,
    ground_for_turn,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "alert-grounding" / "recipes.json"


@pytest.fixture(scope="module")
def recipes() -> list[dict]:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(rows, list) and rows
    return rows


def test_flag_default_off_and_truthy():
    assert grounding_enabled({}) is False
    assert grounding_enabled({GROUNDING_ENV: "0"}) is False
    assert grounding_enabled({GROUNDING_ENV: "1"}) is True
    assert grounding_enabled({GROUNDING_ENV: "on"}) is True


def test_ground_for_turn_gate(recipes):
    alert = {"alert_class": "checkout-latency"}
    assert ground_for_turn(alert, recipes, environ={}) is None
    match = ground_for_turn(alert, recipes, environ={GROUNDING_ENV: "1"})
    assert isinstance(match, RecipeMatch) and match.recipe_id == "acme-checkout-probe"


def test_simple_match_and_reason(recipes):
    match = ground({"alert_class": "checkout-latency"}, recipes)
    assert match is not None and match.recipe_id == "acme-checkout-probe"
    assert match.reason == "matched alert_class"
    assert 0.5 <= match.confidence < 1.0


def test_specificity_prefers_more_conditions(recipes):
    match = ground(
        {"alert_class": "checkout-latency", "severity": "high", "environment": "prod"}, recipes
    )
    assert match is not None and match.recipe_id == "acme-checkout-probe-severity"
    assert "severity" in match.reason and "environment" in match.reason
    low = ground({"alert_class": "checkout-latency"}, recipes)
    assert match.confidence > low.confidence  # more = tighter the match


def test_missing_metadata_never_invents(recipes):
    """A condition whose alert field is absent is a MISS -> no grounding."""
    assert ground({"severity": "high", "environment": "prod"}, recipes) is None
    assert ground({}, recipes) is None
    assert ground(None, recipes) is None


def test_empty_match_grounds_nothing(recipes):
    result = ground({"anything": "x", "alert_class": "synthetic-ok"}, recipes)
    assert result.recipe_id == "acme-echo-ok"  # the empty-match row never wins


def test_tiebreak_is_deterministic(recipes):
    """Two single-key matches on the same alert -> lexicographic recipe id."""
    a = {"id": "acme-a", "version": "1", "match": {"alert_class": "checkout-latency"},
         "steps": [{"kind": "echo", "args": {}, "evidence_required": False}]}
    b = {"id": "acme-b", "version": "1", "match": {"alert_class": "checkout-latency"},
         "steps": [{"kind": "echo", "args": {}, "evidence_required": False}]}
    match = ground({"alert_class": "checkout-latency"}, [b, a])
    assert match.recipe_id == "acme-a"


def test_invalid_recipe_rows_are_skipped_not_fatal(recipes):
    broken = {"id": "", "steps": []}  # fails schema validation
    match = ground({"alert_class": "synthetic-ok"}, [broken, *recipes])
    assert match is not None and match.recipe_id == "acme-echo-ok"


def test_case_insensitive_match_and_cross_type_miss(recipes):
    match = ground({"alert_class": "CHECKOUT-LATENCY "}, recipes)  # case/space folded
    assert match is not None and match.recipe_id == "acme-checkout-probe"
    # 1 vs "1" is not a match; booleans/ints cross-compare by equality only.
    assert ground({"alert_class": 1}, recipes) is None
    num = {"id": "acme-num", "version": "1", "match": {"count": 3},
           "steps": [{"kind": "echo", "args": {}, "evidence_required": False}]}
    assert ground({"count": 3}, [num]) is not None
    assert ground({"count": True}, [num]) is None  # True != 3


def test_equality_true_equals_one():
    num = {"id": "acme-bool", "version": "1", "match": {"enabled": True},
           "steps": [{"kind": "echo", "args": {}, "evidence_required": False}]}
    assert ground({"enabled": 1}, [num]) is not None  # documented True==1 equality
    assert ground({"enabled": 0}, [num]) is None


def test_to_dict_shape(recipes):
    match = ground_for_turn(
        {"alert_class": "checkout-latency"}, recipes, environ={GROUNDING_ENV: "1"}
    )
    d = match.to_dict()
    assert set(d) == {"recipe_id", "confidence", "reason"} and d["recipe_id"] == "acme-checkout-probe"
