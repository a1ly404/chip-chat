"""Solver v2 — jev infra agent with the full kit.

Kit law: skills + learnings + tracker gate + mempalace as receipted skips
until provisioned → bounded read-diagnostics → one
registry-first jev Decisions call → jev-owned next_action executed ONLY
through the action law module (case_intake_solver_actions.py) with pre/post
receipts. Bands stay hypothetical; allowlist law gates execution, never
bands. Kill-switches: CASE_INTAKE_SOLVER_LIVE (master) and
CASE_INTAKE_SOLVER_KIT (this path; unset/0 = solver-v1 raw flash path).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from chip import config as chip_config
from chip import system_pack
from chip.learnings import load_learnings
from chip_relay.case_intake_solver import CaseFile
from chip_relay.case_intake_solver_actions import (
    docker_inspect,
    docker_restart,
    fix_script,
    run_probe,
    unknown_action,
    write_action_rows,
)

SOLVER_KIT_ENV = "CASE_INTAKE_SOLVER_KIT"

_SKILL_BODY_CAP = 1500
_SKILL_FILE_CAP = 3
_URL = re.compile(r"https?://[\w.-]+(?:/[^\s\"']*)?", re.IGNORECASE)

_QUESTIONS = {
    "case_solved": {
        "type": "noul",
        "instructions": "PROBABILITY THE CASE IS RESOLVED/UNDERSTOOD GIVEN STATE+TOOL OUTPUT. Low when evidence is missing.",
    },
    "restart_warranted": {
        "type": "noul",
        "instructions": "PROBABILITY THAT A SANCTIONED restart:<allowed-container> IS THE CORRECT NEXT STEP. Any op still passes the action-law allowlist; media/destructive targets are always refused.",
    },
    "rerun_warranted": {
        "type": "noul",
        "instructions": "PROBABILITY THAT THE SANCTIONED rerun:<allowed-script> IS THE CORRECT NEXT STEP. Scripts are always allowlist-gated via the action law.",
    },
}

_BANDS = {(0.95, "propose-auto"), (0.80, "propose-in-room")}
SKILL_DIR = chip_config.REPO_ROOT / "docs" / "skill-bodies"


def kit_enabled(environ: dict[str, str]) -> bool:
    return environ.get(SOLVER_KIT_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def _skill_bodies(case_class: str) -> list[str]:
    """Bounded skill bodies from the baked docs/skill-bodies tree."""
    try:
        manifest = (SKILL_DIR / "MANIFEST.md").read_text(encoding="utf-8")
    except OSError:
        return []
    names = [n for n in re.findall(r"(?im)^\s*[-*]?\s*`?([\w-]+)", manifest)]
    names = [n for n in names if n and n.lower() not in {"manifest", "md"}]
    tokens = [t for t in re.split(r"[-_\s]+", (case_class or "").lower()) if t]
    scored = [n for n in names if any(t in n.lower() for t in tokens) and len(tokens) > 0]
    picked = (scored or names)[:_SKILL_FILE_CAP]
    out: list[str] = []
    for n in picked:
        try:
            out.append((SKILL_DIR / f"{n}.body.md").read_text(encoding="utf-8")[:_SKILL_BODY_CAP])
        except OSError:
            return out  # partial kit is legal; the receipt chain records what ran
    return out


def _kit(case: CaseFile) -> dict[str, Any]:
    skills = _skill_bodies(case.fingerprint_class or "")
    learnings: list[str] = []
    pdir = system_pack.surface("persona_dir", system_pack.load_pack())
    for persona in system_pack.solver_kit_personas():
        f = pdir / persona / "learnings.md"
        if f.is_file():
            learnings.extend(load_learnings(f, max_lines=8))
    from chip_relay.linear_key_gate import check as linear_key_check

    gate = linear_key_check(dict(os.environ), data_root=chip_config.data_dir())
    pfx = system_pack.pack_identity_string("tracker_gate_receipt_prefix", default="tracker")
    tracker_rows: tuple[str, ...] = {
        "absent": (f"{pfx}:no-provisioned-key",),
        "valid": (f"{pfx}:key-valid-learnings-not-wired",),
    }.get(gate["state"], (f"{pfx}:key-{gate['state']}-fail-closed",))
    return {
        "skills": skills,
        "learnings": learnings[:10],
        "kit_skipped": (*tracker_rows, "mempalace:not-reachable-from-container"),
    }


def _diagnose(case: CaseFile, *, case_id: str, case_path: Path, cap: int = 5) -> list[dict[str, Any]]:
    """Bounded deterministic read-diagnostics from case-declared targets."""
    rows: list[dict[str, Any]] = []
    urls = _URL.findall(f"{case.repro_context} {case.evidence_path}")[:cap]
    for u in urls:
        rows.append(run_probe(u, case_id=case_id, case_path=case_path))
    return rows


def _container_candidates(case: CaseFile) -> list[str]:
    """Script-built container-name candidates from case fields (bounded, deduped)."""
    text = f"{case.description} {case.repro_context} {case.evidence_path}"
    seen, out = set(), []
    for hit in re.finditer(r"(?i)\b(?:container|service|svc)\b[ =:\"]+([\w][\w.-]{1,63})", text):
        name = hit.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out[:3]


def _warranted_actions(answers: dict[str, Any], *, case: CaseFile, environ: dict[str, str], case_id: str, case_path: Path) -> list[dict[str, Any]]:
    """Map warranted-noul answers to sanctioned ops; the action LAW still gates
    every op (bands/answers only pick WHICH already-legal op to attempt)."""
    def prob(qid: str) -> float:
        entry = answers.get(qid) if isinstance(answers.get(qid), dict) else {}
        p = entry.get("noul")
        return float(p) if isinstance(p, (int, float)) else 0.0

    rows: list[dict[str, Any]] = []
    if prob("restart_warranted") > 0.5:
        candidates = _container_candidates(case)
        rows.append(
            docker_restart(candidates[0] if candidates else "", environ=environ,
                           case_id=case_id, case_path=case_path)
            if candidates else
            unknown_action("restart", "<no-container-declared-in-case>", case_id=case_id, case_path=case_path)
        )
    if prob("rerun_warranted") > 0.5:
        rows.append(unknown_action("rerun", "<no-script-whitelisted-in-lane>", case_id=case_id, case_path=case_path))
    return rows


def _warranted_targets(state: dict[str, Any]) -> list[str]:
    """Derive sanctioned op targets from the kit diagnostics (script-built)."""
    targets: list[str] = []
    for row in state.get("diagnostic_rows", []):
        for key in ("detail", "target"):
            blob = str(row.get(key) or "")
            hit = re.search(r"(?i)container[ =:\"]+([\w.-]+)", blob)
            if hit:
                targets.append(hit.group(1))
    # dedupe, bounded
    seen, out = set(), []
    for t in targets:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:3]


def _band_of(answers: dict[str, Any]) -> str | None:
    """Band label from the first numeric answer — HYPOTHETICAL only (jev law):
    a band never chooses behavior; the action law does that."""
    for entry in answers.values():
        if not isinstance(entry, dict):
            continue
        prob = entry.get("noul")
        if not isinstance(prob, (int, float)) and isinstance(entry.get("confidence"), bool | int | float):
            prob = entry.get("confidence")
        if isinstance(prob, (int, float)):
            prob = float(prob)
            if prob >= 0.95:
                return "propose-auto"
            if prob >= 0.80:
                return "propose-in-room"
            return "needs-repro"
    return None


def make_solver_fn_jev(
    *,
    environ: dict[str, str],
    limits: dict[str, Any],
    log_spend: Callable[[Any, str, int], None] | None = None,
    now: float = 0.0,
    case_path_for: Callable[[CaseFile], Path],
) -> Callable[[CaseFile], dict[str, Any]]:
    """Build the kit solver_fn passed to attempt_solve(solver_fn=...)."""
    from chip import jev_router

    def solve(case: CaseFile) -> dict[str, Any]:
        case_path = case_path_for(case)
        kit = _kit(case)
        diagnostics = _diagnose(case, case_id=case.id, case_path=case_path)

        state = {
            "case": case.as_dict(),
            "kit_skills": kit["skills"],
            "kit_learnings": kit["learnings"],
            "kit_skipped": kit["kit_skipped"],
            "diagnostic_rows": [
                {k: r.get(k) for k in ("action", "target", "phase", "ok", "reason", "detail")}
                for r in diagnostics
            ],
        }

        data = jev_router.ask(state, _QUESTIONS, call_site="case-intake-solver-jev", environ=environ)
        answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
        band = _band_of(answers)
        executed = _warranted_actions(answers, case=case, environ=environ, case_id=case.id, case_path=case_path)

        cost = float((data.get("usage") or {}).get("cost") or 0.0)
        model = str(data.get("model") or "")
        if log_spend is not None and data.get("status") not in ("fallback", "conservative") and not model.startswith("mock/"):
            try:
                log_spend(case, model or "typesafe/jev-1.13", 0)
            except Exception:  # noqa: BLE001 — ledger fail never masks the receipt chain
                pass

        write_action_rows(case_path, [{
            "ts": now or time.time(),
            "case_id": case.id,
            "event": "jev_attempt",
            "call_site": "case-intake-solver-jev",
            "model_used": model,
            "jev_cost_usd": cost,
            "band_hypothetical": band,
            "counter_id": data.get("counter_id"),
            "status": data.get("status"),
            "actions_executed": len(executed),
            "synthetic": bool(getattr(case, "id", "").startswith("synthetic-")),
        }])

        solved = bool(band == "propose-auto")
        note = "no-op"
        if executed:
            last = executed[-1]
            note = str(last.get("reason") or last.get("detail") or "no-op")[:200]
        elif answers and isinstance(next(iter(answers.values()), None), dict):
            note = f"band={band}" if band else "jev-answered-no-band"
        return {
            "solved": solved,
            "note": note,
            "band_hypothetical": band,
            "counter_id": data.get("counter_id"),
            "jev_cost_usd": cost,
            "actions": len(executed),
            "kit_skipped": list(kit["kit_skipped"]),
        }

    return solve
