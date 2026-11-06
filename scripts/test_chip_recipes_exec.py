"""A1: CHIP_RECIPE_EXEC + grounded_recipe_go (registry-only, fake runner)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chip.recipes import (
    RECIPE_EXEC_ENV,
    FakeRunner,
    grounded_recipe_go,
    load_registry,
    recipe_exec_enabled,
    validate_recipe,
)

MUTATING = {
    "id": "acme-restart-checkout",
    "version": "1",
    "match": {"alert_class": "checkout-down"},
    "mutating": True,
    "mode": "shadow",
    "steps": [
        {
            "kind": "restart",
            "args": {"target": "checkout-api"},
            "evidence_required": True,
        }
    ],
}


@pytest.fixture()
def registry():
    return load_registry()


def test_flag_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RECIPE_EXEC_ENV, raising=False)
    assert recipe_exec_enabled() is False
    monkeypatch.setenv(RECIPE_EXEC_ENV, "0")
    assert recipe_exec_enabled() is False
    monkeypatch.setenv(RECIPE_EXEC_ENV, "true")
    assert recipe_exec_enabled() is True


def test_validate_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown step kind"):
        validate_recipe(
            {
                "id": "x",
                "version": "1",
                "steps": [{"kind": "rm-rf", "args": {}, "evidence_required": False}],
            }
        )


def test_shadow_works_when_flag_off(monkeypatch: pytest.MonkeyPatch, registry) -> None:
    monkeypatch.delenv(RECIPE_EXEC_ENV, raising=False)
    runner = FakeRunner()
    result = grounded_recipe_go(
        "acme-checkout-probe",
        evidence=["tool:probe.checkout-api ok"],
        live=False,
        registry=registry,
        runner=runner,
    )
    assert result.status == "shadow"
    assert "would-act" in result.detail
    assert runner.calls == []  # shadow never runs


def test_live_refused_when_flag_off(monkeypatch: pytest.MonkeyPatch, registry) -> None:
    monkeypatch.delenv(RECIPE_EXEC_ENV, raising=False)
    runner = FakeRunner()
    result = grounded_recipe_go(
        "acme-echo-ok",
        live=True,
        registry=registry,
        runner=runner,
    )
    assert result.status == "refused"
    assert RECIPE_EXEC_ENV in result.detail
    assert runner.calls == []


def test_live_exec_non_mutating_when_flag_on(monkeypatch: pytest.MonkeyPatch, registry) -> None:
    monkeypatch.setenv(RECIPE_EXEC_ENV, "1")
    runner = FakeRunner()
    result = grounded_recipe_go(
        "acme-echo-ok",
        live=True,
        registry=registry,
        runner=runner,
        environ={RECIPE_EXEC_ENV: "1"},
    )
    assert result.status == "executed"
    assert runner.calls == [("acme-echo-ok", "echo")]
    assert result.step_results[0].ok is True


def test_live_requires_evidence_when_marked(monkeypatch: pytest.MonkeyPatch, registry) -> None:
    monkeypatch.setenv(RECIPE_EXEC_ENV, "1")
    result = grounded_recipe_go(
        "acme-checkout-probe",
        evidence=[],
        live=True,
        registry=registry,
        environ={RECIPE_EXEC_ENV: "1"},
    )
    assert result.status == "refused"
    assert "evidence_required" in result.detail


def test_unknown_recipe_refused(registry) -> None:
    result = grounded_recipe_go("not-a-real-recipe", live=False, registry=registry)
    assert result.status == "refused"
    assert result.recipe_id is None


def test_mutating_refused_live_even_when_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RECIPE_EXEC_ENV, "1")
    reg = load_registry(recipes=[MUTATING])
    runner = FakeRunner()
    result = grounded_recipe_go(
        "acme-restart-checkout",
        evidence=["tool:health.checkout-api down"],
        live=True,
        registry=reg,
        runner=runner,
        environ={RECIPE_EXEC_ENV: "1"},
    )
    assert result.status == "refused"
    assert "mutating" in result.detail
    assert runner.calls == []


def test_mutating_shadow_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RECIPE_EXEC_ENV, raising=False)
    reg = load_registry(recipes=[MUTATING])
    result = grounded_recipe_go(
        "acme-restart-checkout",
        evidence=["tool:health.checkout-api down"],
        live=False,
        registry=reg,
    )
    assert result.status == "shadow"


def test_load_registry_from_path(tmp_path: Path) -> None:
    path = tmp_path / "recipes.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "file-echo",
                    "version": "1",
                    "mutating": False,
                    "steps": [{"kind": "echo", "args": {"msg": "from-file"}, "evidence_required": False}],
                }
            ]
        ),
        encoding="utf-8",
    )
    reg = load_registry(path=path)
    assert "file-echo" in reg
    assert reg["file-echo"].steps[0].args["msg"] == "from-file"


def test_fake_runner_failure_refuses(monkeypatch: pytest.MonkeyPatch, registry) -> None:
    monkeypatch.setenv(RECIPE_EXEC_ENV, "1")
    runner = FakeRunner(fail_kinds=frozenset({"echo"}))
    result = grounded_recipe_go(
        "acme-echo-ok",
        live=True,
        registry=registry,
        runner=runner,
        environ={RECIPE_EXEC_ENV: "1"},
    )
    assert result.status == "refused"
    assert "step failure" in result.detail
