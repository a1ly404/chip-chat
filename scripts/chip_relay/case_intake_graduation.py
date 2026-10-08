"""Graduation watcher (slice 6).

At ``consecutive_llm_clean >= 200`` **and** ``CASE_INTAKE_GRADUATION_ARMED=1``
the watcher disables case intake (coverage flag ``graduated_disabled``) and
posts one summary to the primary relay room. It **never** auto-restores legacy
monitoring skills — that is an operator manual compose flip after review.

The disable lives in ``coverage.json`` on the data volume so it survives
restarts; the compose ``CASE_INTAKE_ENABLED`` env is left untouched.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from chip_relay.case_intake_coverage import GRADUATION_CONSECUTIVE_CLEAN, load as load_coverage, save as save_coverage

GRADUATION_ARMED_ENV = "CASE_INTAKE_GRADUATION_ARMED"
GRADUATION_MIN_ENV = "CASE_INTAKE_GRADUATION_MIN"


def graduation_min(environ: dict[str, str]) -> int:
    """Effective graduation threshold. Law default 200; ``CASE_INTAKE_GRADUATION_MIN``
    may lower it for a bounded test window (operator GO). Garbage/out-of-range values
    fail safe to the law default."""
    raw = (environ.get(GRADUATION_MIN_ENV) or "").strip()
    if not raw:
        return GRADUATION_CONSECUTIVE_CLEAN
    try:
        value = int(raw)
    except ValueError:
        return GRADUATION_CONSECUTIVE_CLEAN
    if value <= 0:
        return GRADUATION_CONSECUTIVE_CLEAN
    return min(value, GRADUATION_CONSECUTIVE_CLEAN)


def graduation_armed(environ: dict[str, str]) -> bool:
    return environ.get(GRADUATION_ARMED_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def intake_disabled_by_graduation(environ: dict[str, str] | None = None) -> bool:
    """True once graduation fired (persisted; survives restarts)."""
    del environ  # kept for call-site symmetry; state is on the volume
    return bool(load_coverage().get("graduated_disabled"))


def summary_text(streak: int) -> str:
    return (
        f"case-intake graduated: consecutive_llm_clean={streak}/{GRADUATION_CONSECUTIVE_CLEAN} "
        f"— intake disabled (graduated_disabled=1). No auto-restore of legacy monitoring "
        f"skills; operator manual compose flip after review. (slice 6)"
    )


def check_graduation(
    environ: dict[str, str],
    *,
    post_summary: Callable[[str], bool] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Fire the graduation watcher if earned and armed. Idempotent."""
    state = load_coverage()
    streak = int(state.get("consecutive_llm_clean") or 0)
    minimum = graduation_min(environ)
    if state.get("graduated_disabled"):
        return {"fired": False, "reason": "already_disabled", "streak": streak, "min": minimum}
    if streak < minimum:
        return {"fired": False, "reason": "streak", "streak": streak, "min": minimum}
    if not graduation_armed(environ):
        return {"fired": False, "reason": "not_armed", "streak": streak, "min": minimum}

    ts = time.time() if now is None else now
    state["graduated_disabled"] = True
    state["graduation_applied_at"] = ts
    save_coverage(state)

    posted = False
    if post_summary is not None:
        try:
            posted = bool(post_summary(summary_text(streak)))
        except Exception:  # noqa: BLE001 — posting failure must not un-disable intake
            posted = False
    state = load_coverage()
    state["graduation_summary_posted"] = posted
    save_coverage(state)
    return {"fired": True, "streak": streak, "posted": posted, "at": ts}
