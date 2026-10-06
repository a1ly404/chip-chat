"""Coverage scoreboard + feed halt checks (sustained live program)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from chip.config import data_dir
from chip.spend import CUTOFF_MONTHLY_USD, ledger_monthly_usd, load_ledger

Outcome = Literal["parsed_clean", "pending_clarify", "failed_closed", "misroute", "failed_open"]
FailureKind = Literal["closed", "open", "none"]

GRADUATION_CONSECUTIVE_CLEAN = 200
OPEN_FAIL_KILL_THRESHOLD = 3
APPROACH_CUTOFF_USD = 9.0


def _path() -> Path:
    root = data_dir() / "case_intake"
    root.mkdir(parents=True, exist_ok=True)
    return root / "coverage.json"


def load() -> dict[str, Any]:
    path = _path()
    if not path.is_file():
        return _default()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _default()
    base = _default()
    if isinstance(raw, dict):
        base.update(raw)
    return base


def _default() -> dict[str, Any]:
    return {
        "alerts_seen": 0,
        "parsed_clean": 0,
        "pending_clarify": 0,
        "failed_closed": 0,
        "misroutes": 0,
        "failed_open": 0,
        "consecutive_clean": 0,
        "open_failures_by_class": {},
        "killed_classes": [],
        "feed_halted": False,
        "halt_reason": "",
        "fixtures_captured": [],
        "last_updated": 0.0,
    }


def save(state: dict[str, Any]) -> None:
    state["last_updated"] = time.time()
    _path().write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def record_outcome(
    outcome: Outcome,
    *,
    failure_kind: FailureKind = "none",
    case_class: str = "intake-router",
    fixture_id: str = "",
) -> dict[str, Any]:
    state = load()
    state["alerts_seen"] = int(state.get("alerts_seen") or 0) + 1
    state[outcome] = int(state.get(outcome) or 0) + 1

    if outcome in ("failed_open", "misroute"):
        state["consecutive_clean"] = 0
        if failure_kind == "open":
            by_class = state.setdefault("open_failures_by_class", {})
            count = int(by_class.get(case_class) or 0) + 1
            by_class[case_class] = count
            if count >= OPEN_FAIL_KILL_THRESHOLD:
                killed = set(state.get("killed_classes") or [])
                killed.add(case_class)
                state["killed_classes"] = sorted(killed)
    elif outcome in ("failed_closed", "pending_clarify", "parsed_clean"):
        if outcome == "parsed_clean":
            state["consecutive_clean"] = int(state.get("consecutive_clean") or 0) + 1
        # (a) closed failures do not break the graduation streak

    if fixture_id:
        fixtures = list(state.get("fixtures_captured") or [])
        if fixture_id not in fixtures:
            fixtures.append(fixture_id)
        state["fixtures_captured"] = fixtures[-100:]

    save(state)
    return state


def scoreboard_table(state: dict[str, Any] | None = None) -> str:
    s = state or load()
    rows = [
        ("alerts_seen", s.get("alerts_seen", 0)),
        ("parsed_clean", s.get("parsed_clean", 0)),
        ("pending_clarify", s.get("pending_clarify", 0)),
        ("failed_closed", s.get("failed_closed", 0)),
        ("misroutes", s.get("misroutes", 0)),
        ("failed_open", s.get("failed_open", 0)),
        ("consecutive_clean", s.get("consecutive_clean", 0)),
        (f"graduation_bar", GRADUATION_CONSECUTIVE_CLEAN),
    ]
    lines = ["| metric | count |", "| --- | ---: |"]
    for name, val in rows:
        lines.append(f"| {name} | {val} |")
    return "\n".join(lines)


def check_feed_halt(environ: dict[str, str]) -> tuple[bool, str]:
    state = load()
    if state.get("feed_halted"):
        return True, str(state.get("halt_reason") or "feed halted")

    monthly = ledger_monthly_usd(load_ledger())
    if monthly >= CUTOFF_MONTHLY_USD:
        reason = f"monthly_usd={monthly} >= cutoff {CUTOFF_MONTHLY_USD}"
        state["feed_halted"] = True
        state["halt_reason"] = reason
        save(state)
        return True, reason
    if monthly >= APPROACH_CUTOFF_USD:
        return False, f"warn monthly_usd={monthly} approaching cutoff"

    return False, ""


def class_killed(case_class: str) -> bool:
    return case_class in set(load().get("killed_classes") or [])


def graduated() -> bool:
    return int(load().get("consecutive_clean") or 0) >= GRADUATION_CONSECUTIVE_CLEAN
