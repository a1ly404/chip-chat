"""Coverage scoreboard + feed halt checks (sustained live program)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from chip.config import REPO_ROOT, data_dir
from chip.spend import CUTOFF_MONTHLY_USD, ledger_monthly_usd, load_ledger

Outcome = Literal[
    "parsed_clean",
    "pending_clarify",
    "failed_closed",
    "misroute",
    "failed_open",
    "adapter_handled",
    "adapter_failed_closed",
    "llm_parsed_clean",
    "llm_pending_clarify",
]
FailureKind = Literal["closed", "open", "none"]
EscalationMetric = Literal["escalation_evidence", "escalation_diagnose", "escalation_paged"]

GRADUATION_CONSECUTIVE_CLEAN = 200
OPEN_FAIL_KILL_THRESHOLD = 3
APPROACH_CUTOFF_USD = 9.0

_CLEAN_FIXTURE_OUTCOMES = frozenset({"parsed_clean", "llm_parsed_clean", "adapter_handled"})

_SCORE_COLUMNS = (
    "alerts_seen",
    "adapter_handled",
    "adapter_failed_closed",
    "llm_parsed_clean",
    "llm_pending_clarify",
    "parsed_clean",
    "pending_clarify",
    "failed_closed",
    "misroutes",
    "failed_open",
    "consecutive_llm_clean",
    "consecutive_clean",
)


def _path() -> Path:
    root = data_dir() / "case_intake"
    root.mkdir(parents=True, exist_ok=True)
    return root / "coverage.json"


def fixture_index_path() -> Path:
    return REPO_ROOT / "config" / "fixtures" / "case_intake_live" / "index.json"


def load_fixture_index() -> list[dict[str, Any]]:
    path = fixture_index_path()
    if not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return list(rows) if isinstance(rows, list) else []


def classes_repaired_by_fixtures() -> set[str]:
    """Case classes with a committed golden fixture proving clean handling."""
    repaired: set[str] = set()
    for row in load_fixture_index():
        rel = str(row.get("path") or "").strip()
        if not rel:
            continue
        fp = REPO_ROOT / rel
        if not fp.is_file():
            continue
        try:
            fx = json.loads(fp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        expected = fx.get("expected") if isinstance(fx.get("expected"), dict) else {}
        exp_outcome = str(expected.get("outcome") or row.get("outcome") or fx.get("outcome") or "")
        if exp_outcome not in _CLEAN_FIXTURE_OUTCOMES:
            continue
        cls = str(
            expected.get("fingerprint_class")
            or row.get("fingerprint_class")
            or fx.get("fingerprint_class")
            or ""
        ).strip()
        if cls:
            repaired.add(cls)
    return repaired


def sync_killed_classes_from_fixtures(state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Drop class kill when a merged fixture documents clean replay for that class."""
    state = dict(state if state is not None else load())
    repaired = classes_repaired_by_fixtures()
    if not repaired:
        return state
    killed = set(state.get("killed_classes") or [])
    new_killed = killed - repaired
    if new_killed == killed:
        return state
    state["killed_classes"] = sorted(new_killed)
    by_class = state.setdefault("open_failures_by_class", {})
    for cls in repaired:
        by_class.pop(cls, None)
    save(state)
    return state


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
        "adapter_handled": 0,
        "adapter_failed_closed": 0,
        "llm_parsed_clean": 0,
        "llm_pending_clarify": 0,
        "parsed_clean": 0,
        "pending_clarify": 0,
        "failed_closed": 0,
        "misroutes": 0,
        "failed_open": 0,
        "consecutive_llm_clean": 0,
        "consecutive_clean": 0,
        "escalation_evidence": 0,
        "escalation_diagnose": 0,
        "escalation_paged": 0,
        "escalated_diagnosed": 0,
        "escalated_parked_low_conf": 0,
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


def record_escalation(metric: EscalationMetric) -> dict[str, Any]:
    state = load()
    state[metric] = int(state.get(metric) or 0) + 1
    save(state)
    return state


def _bump_llm_graduation(state: dict[str, Any], outcome: Outcome) -> None:
    if outcome == "llm_parsed_clean":
        state["consecutive_llm_clean"] = int(state.get("consecutive_llm_clean") or 0) + 1
        state["consecutive_clean"] = int(state.get("consecutive_clean") or 0) + 1
    elif outcome in ("failed_open", "misroute"):
        state["consecutive_llm_clean"] = 0
        state["consecutive_clean"] = 0


def record_outcome(
    outcome: Outcome,
    *,
    failure_kind: FailureKind = "none",
    case_class: str = "intake-router",
    fixture_id: str = "",
) -> dict[str, Any]:
    state = load()
    state["alerts_seen"] = int(state.get("alerts_seen") or 0) + 1
    if outcome in _SCORE_COLUMNS:
        state[outcome] = int(state.get(outcome) or 0) + 1

    if outcome == "adapter_handled":
        state["parsed_clean"] = int(state.get("parsed_clean") or 0) + 1
    elif outcome == "llm_parsed_clean":
        state["parsed_clean"] = int(state.get("parsed_clean") or 0) + 1
    elif outcome == "llm_pending_clarify":
        state["pending_clarify"] = int(state.get("pending_clarify") or 0) + 1
    elif outcome == "pending_clarify":
        state["llm_pending_clarify"] = int(state.get("llm_pending_clarify") or 0) + 1

    if outcome in ("failed_open", "misroute"):
        if failure_kind == "open":
            by_class = state.setdefault("open_failures_by_class", {})
            count = int(by_class.get(case_class) or 0) + 1
            by_class[case_class] = count
            if count >= OPEN_FAIL_KILL_THRESHOLD:
                killed = set(state.get("killed_classes") or [])
                killed.add(case_class)
                state["killed_classes"] = sorted(killed)
        _bump_llm_graduation(state, outcome)
    elif outcome == "parsed_clean":
        state["llm_parsed_clean"] = int(state.get("llm_parsed_clean") or 0) + 1
        state["parsed_clean"] = int(state.get("parsed_clean") or 0) + 1
        _bump_llm_graduation(state, "llm_parsed_clean")
    elif outcome == "pending_clarify":
        state["llm_pending_clarify"] = int(state.get("llm_pending_clarify") or 0) + 1
        state["pending_clarify"] = int(state.get("pending_clarify") or 0) + 1
    elif outcome == "llm_parsed_clean":
        state["parsed_clean"] = int(state.get("parsed_clean") or 0) + 1
        _bump_llm_graduation(state, "llm_parsed_clean")
    elif outcome == "llm_pending_clarify":
        state["pending_clarify"] = int(state.get("pending_clarify") or 0) + 1
    elif outcome == "adapter_handled":
        pass

    if fixture_id:
        fixtures = list(state.get("fixtures_captured") or [])
        if fixture_id not in fixtures:
            fixtures.append(fixture_id)
        state["fixtures_captured"] = fixtures[-100:]

    save(state)
    if state.get("killed_classes"):
        state = sync_killed_classes_from_fixtures(state)
    return state


def scoreboard_table(state: dict[str, Any] | None = None) -> str:
    s = state or load()
    rows = [
        ("alerts_seen", s.get("alerts_seen", 0)),
        ("adapter_handled", s.get("adapter_handled", 0)),
        ("adapter_failed_closed", s.get("adapter_failed_closed", 0)),
        ("llm_parsed_clean", s.get("llm_parsed_clean", 0)),
        ("llm_pending_clarify", s.get("llm_pending_clarify", 0)),
        ("failed_closed", s.get("failed_closed", 0)),
        ("misroutes", s.get("misroutes", 0)),
        ("failed_open", s.get("failed_open", 0)),
        ("consecutive_llm_clean", s.get("consecutive_llm_clean", 0)),
        ("killed_classes", ", ".join(s.get("killed_classes") or []) or "—"),
        ("escalation_evidence", s.get("escalation_evidence", 0)),
        ("escalation_diagnose", s.get("escalation_diagnose", 0)),
        ("escalation_paged", s.get("escalation_paged", 0)),
        ("escalated_diagnosed", s.get("escalated_diagnosed", 0)),
        ("escalated_parked_low_conf", s.get("escalated_parked_low_conf", 0)),
        ("graduation_bar", GRADUATION_CONSECUTIVE_CLEAN),
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
    state = sync_killed_classes_from_fixtures()
    return case_class in set(state.get("killed_classes") or [])


def graduated() -> bool:
    return int(load().get("consecutive_llm_clean") or 0) >= GRADUATION_CONSECUTIVE_CLEAN
