"""CHIP_RECIPE_EXEC gate + grounded_recipe_go.

Flag off (default): refuse live recipe exec; shadow/would-act still works.
Flag on: registry-only; non-mutating recipes may run through an injectable runner;
mutating recipes stay shadow-only in A1 (live refused).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from chip.recipes.registry import load_registry, recipe_by_id
from chip.recipes.schema import Recipe, RecipeStep

RECIPE_EXEC_ENV = "CHIP_RECIPE_EXEC"


class RecipeExecRefused(RuntimeError):
    """Live recipe execution refused (flag off, unknown id, missing evidence, mutating)."""


@dataclass(frozen=True)
class StepResult:
    kind: str
    ok: bool
    detail: str
    mutated: bool = False


@dataclass(frozen=True)
class RecipeGoResult:
    status: str  # refused | shadow | executed
    recipe_id: str | None
    detail: str
    receipt: str
    step_results: tuple[StepResult, ...] = ()


class Runner(Protocol):
    def run_step(self, step: RecipeStep, *, recipe: Recipe) -> StepResult: ...


@dataclass
class FakeRunner:
    """CI / unit fake: records steps; never touches the host."""

    calls: list[tuple[str, str]] = field(default_factory=list)
    fail_kinds: frozenset[str] = field(default_factory=frozenset)

    def run_step(self, step: RecipeStep, *, recipe: Recipe) -> StepResult:
        self.calls.append((recipe.id, step.kind))
        if step.kind in self.fail_kinds:
            return StepResult(kind=step.kind, ok=False, detail="fake fail", mutated=False)
        # Restart-class kinds would mutate; FakeRunner still marks them.
        mutated = step.kind in {"restart", "compose_recreate"}
        return StepResult(
            kind=step.kind,
            ok=True,
            detail=f"fake:{step.kind}:{step.args.get('target') or step.args.get('msg') or 'ok'}",
            mutated=mutated,
        )


def recipe_exec_enabled(environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return env.get(RECIPE_EXEC_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _receipt(*, recipe_id: str, status: str, detail: str, next_hint: str) -> str:
    return (
        f"claim: recipe {recipe_id}\n"
        f"status: {status}\n"
        f"evidence: {detail}\n"
        f"next: {next_hint}"
    )


def _evidence_ok(recipe: Recipe, evidence: list[str] | None) -> bool:
    needed = any(s.evidence_required for s in recipe.steps)
    if not needed:
        return True
    lines = [e.strip() for e in (evidence or []) if str(e).strip()]
    return bool(lines)


def grounded_recipe_go(
    recipe_id: str,
    *,
    evidence: list[str] | None = None,
    live: bool = False,
    registry: dict[str, Recipe] | None = None,
    runner: Runner | None = None,
    environ: dict[str, str] | None = None,
) -> RecipeGoResult:
    """Grounded GO for a registry recipe.

    - Unknown id → refused (never invents a recipe).
    - Missing required evidence → refused.
    - ``live=False`` (shadow): returns would-act receipt; does not call runner.
    - ``live=True`` requires ``CHIP_RECIPE_EXEC`` on; mutating recipes refused live in A1.
    """
    rid = (recipe_id or "").strip()
    reg = registry if registry is not None else load_registry()
    recipe = recipe_by_id(rid, reg)
    if recipe is None:
        detail = f"unknown recipe id: {rid!r}"
        return RecipeGoResult(
            status="refused",
            recipe_id=None,
            detail=detail,
            receipt=_receipt(recipe_id=rid or "?", status="refused", detail=detail, next_hint="hold"),
        )

    if not _evidence_ok(recipe, evidence):
        detail = "evidence_required: no evidence lines"
        return RecipeGoResult(
            status="refused",
            recipe_id=recipe.id,
            detail=detail,
            receipt=_receipt(
                recipe_id=recipe.id,
                status="refused",
                detail=detail,
                next_hint="attach tool evidence then retry",
            ),
        )

    if not live:
        detail = "shadow would-act: " + "; ".join(
            f"{s.kind}({s.args})" for s in recipe.steps
        )
        return RecipeGoResult(
            status="shadow",
            recipe_id=recipe.id,
            detail=detail,
            receipt=_receipt(
                recipe_id=recipe.id,
                status="shadow",
                detail=detail,
                next_hint="set CHIP_RECIPE_EXEC=1 and live=True for non-mutating only",
            ),
        )

    # live path
    if not recipe_exec_enabled(environ):
        detail = f"{RECIPE_EXEC_ENV} is off — live recipe exec refused"
        return RecipeGoResult(
            status="refused",
            recipe_id=recipe.id,
            detail=detail,
            receipt=_receipt(
                recipe_id=recipe.id,
                status="refused",
                detail=detail,
                next_hint="enable CHIP_RECIPE_EXEC on engine-local only, or use shadow",
            ),
        )

    if recipe.mutating:
        detail = "mutating recipe refused live in A1 (shadow only)"
        return RecipeGoResult(
            status="refused",
            recipe_id=recipe.id,
            detail=detail,
            receipt=_receipt(
                recipe_id=recipe.id,
                status="refused",
                detail=detail,
                next_hint="use shadow; mutating live needs a later slice + Ally GO",
            ),
        )

    active_runner: Runner = runner if runner is not None else FakeRunner()
    results: list[StepResult] = []
    for step in recipe.steps:
        results.append(active_runner.run_step(step, recipe=recipe))
    if not all(r.ok for r in results):
        detail = "step failure: " + "; ".join(f"{r.kind}={r.detail}" for r in results)
        return RecipeGoResult(
            status="refused",
            recipe_id=recipe.id,
            detail=detail,
            receipt=_receipt(recipe_id=recipe.id, status="refused", detail=detail, next_hint="hold"),
            step_results=tuple(results),
        )
    if any(r.mutated for r in results):
        # Safety net if a runner claims mutation on a non-mutating recipe.
        detail = "runner reported mutation on non-mutating recipe — refused"
        return RecipeGoResult(
            status="refused",
            recipe_id=recipe.id,
            detail=detail,
            receipt=_receipt(recipe_id=recipe.id, status="refused", detail=detail, next_hint="hold"),
            step_results=tuple(results),
        )

    detail = "executed: " + "; ".join(r.detail for r in results)
    return RecipeGoResult(
        status="executed",
        recipe_id=recipe.id,
        detail=detail,
        receipt=_receipt(
            recipe_id=recipe.id,
            status="executed",
            detail=detail,
            next_hint="done",
        ),
        step_results=tuple(results),
    )
