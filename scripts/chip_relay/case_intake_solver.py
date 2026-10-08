"""Solver dispatch skeleton (slice 6 / WI-356).

``CASE_INTAKE_SOLVER_LIVE`` defaults to **0** — the solver never runs live
without operator GO. Every attempt, **including blocked ones**, writes a receipt
row (week law: ledger row on every attempt). Cooldown is per **fingerprint
class**, never per channel. Receipts are appended to the case file on disk
(flywheel learns outcomes) and to a JSONL ledger on the data volume.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from chip_relay.case_intake import CaseFile

SOLVER_LIVE_ENV = "CASE_INTAKE_SOLVER_LIVE"
DEFAULT_COOLDOWN_S = 3600


def solver_live(environ: dict[str, str]) -> bool:
    return environ.get(SOLVER_LIVE_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def receipts_path(data_root: Path) -> Path:
    return data_root / "case_intake" / "solver_receipts.jsonl"


def solver_cooldown_s(limits: dict[str, Any]) -> int:
    try:
        return max(0, int(limits.get("solver_cooldown_s") or DEFAULT_COOLDOWN_S))
    except (TypeError, ValueError):
        return DEFAULT_COOLDOWN_S


def _append_case_receipt(case_path: Path, row: dict[str, Any]) -> None:
    """Append a solver receipt into the case file JSON (solver_receipts list)."""
    try:
        body = json.loads(case_path.read_text(encoding="utf-8")) if case_path.is_file() else {}
    except json.JSONDecodeError:
        body = {}
    receipts = body.setdefault("solver_receipts", [])
    receipts.append(row)
    case_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def attempt_solve(
    case: CaseFile,
    *,
    limits: dict[str, Any],
    environ: dict[str, str],
    state: dict[str, Any],
    save_state: Callable[[], None],
    data_root: Path,
    case_path: Path,
    now: float,
    solver_fn: Callable[[CaseFile], dict[str, Any]] | None = None,
    synthetic: bool = False,
) -> dict[str, Any]:
    """One bounded solver attempt for a case. Never raises; returns a summary."""
    cls = (case.fingerprint_class or "intake-router").strip() or "intake-router"
    live = solver_live(environ)
    row: dict[str, Any] = {
        "ts": now,
        "case_id": case.id,
        "fingerprint_class": cls,
        "solver_live": live,
    }
    if synthetic:
        row["synthetic"] = True  # test-window attempts must not read as real defects

    if not live:
        row["dispatched"] = False
        row["reason"] = "solver_live_off"
    else:
        cooldown = solver_cooldown_s(limits)
        last = state.setdefault("solver_last_attempt", {})
        prev = float(last.get(cls) or 0)
        if prev and cooldown and now - prev < cooldown:
            row["dispatched"] = False
            row["reason"] = "cooldown"
            row["cooldown_s"] = cooldown
            row["since_last_s"] = round(now - prev, 3)
        else:
            last[cls] = now
            save_state()
            if solver_fn is None:
                row["dispatched"] = False
                row["reason"] = "no_solver_wired"
            else:
                try:
                    result = solver_fn(case) or {}
                    row["dispatched"] = True
                    row["reason"] = "dispatched"
                    row["solved"] = bool(result.get("solved"))
                    row["note"] = str(result.get("note") or "")[:200]
                    # additive receipt fields (WI-356 solver_fn slice):
                    # model + tokens ride the per-case receipt so the beat
                    # spend/attempts contract stays derivable from ledger.
                    if result.get("model"):
                        row["model"] = str(result["model"])
                    if result.get("tokens"):
                        row["tokens"] = int(result["tokens"])
                except Exception as exc:  # noqa: BLE001 — solver faults are receipts, not crashes
                    row["dispatched"] = True
                    row["reason"] = "solver_error"
                    row["note"] = f"{exc.__class__.__name__}: {exc}"[:200]

    try:
        _append_case_receipt(case_path, row)
        receipts_path(data_root).parent.mkdir(parents=True, exist_ok=True)
        with receipts_path(data_root).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass  # disk receipt best-effort; the returned summary is the floor
    return row
