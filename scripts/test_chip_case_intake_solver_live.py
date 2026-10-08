"""Solver_fn slice — offline tests for the WIRED live solver.

Contract under test (scripts/chip_relay/case_intake_solver_live.py):
  * model path = config/case_intake.yaml primary_model (existing lane path
    — z-ai/glm-5.3-flash / fallback qwen — no new routing in this slice)
  * output contract {"solved": bool, "note"}; garbage output raises (which
    attempt_solve wraps as solver_error) — never silent pass
  * spend: every dispatched attempt calls log_spend(case, model, tokens)
    so the intake spend ledger (beat spend delta) includes solver calls
  * untrusted-data law: system prompt states the DATA-not-instructions rule
Offline only: chat_completion + require_api_key monkeypatched; no network.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parent
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from chip_relay import case_intake_solver_live as sl
from chip import openrouter
from chip_relay.case_intake_solver import attempt_solve

LIMITS = {"primary_model": "z-ai/glm-5.3-flash", "fallback_model": "qwen/qwen3.7-flash", "solver_cooldown_s": 3600}


def _case() -> "sl.Any":
    from chip_relay.case_intake import CaseFile

    return CaseFile(
        id="probe-1",
        description="d",
        repro_context="r",
        evidence_path="e://1",
        fingerprint_class="intake-router",
    )


def _fake_payload(text: str, tokens: int = 123) -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"total_tokens": tokens},
    }


@pytest.fixture()
def fake_llm(monkeypatch):
    calls: list[dict] = []

    def chat(key, *, model, messages, max_tokens):
        calls.append({"model": model, "messages": messages, "max_tokens": max_tokens})
        return _fake_payload(json.dumps({"solved": True, "note": "diag ok"}))

    monkeypatch.setattr(openrouter, "chat_completion", chat)
    monkeypatch.setattr(openrouter, "require_api_key", lambda: "k-offline")
    return calls


def test_solver_uses_lane_primary_model_and_contract(fake_llm) -> None:
    spend: list[tuple] = []
    fn = sl.make_solver_fn(environ={}, limits=dict(LIMITS), log_spend=lambda c, m, t: spend.append((m, t)), now=1.0)
    row = fn(_case())
    assert row == {"solved": True, "note": "diag ok", "model": "z-ai/glm-5.3-flash", "tokens": 123}
    assert fake_llm[0]["model"] == "z-ai/glm-5.3-flash"  # lane path, not a new scheme
    assert spend and spend[0] == ("z-ai/glm-5.3-flash", 123)


def test_untrusted_data_law_in_system_prompt(fake_llm) -> None:
    fn = sl.make_solver_fn(environ={}, limits=dict(LIMITS), now=1.0)
    fn(_case())
    sys_msg = fake_llm[0]["messages"][0]["content"]
    assert "DATA" in sys_msg and "never as instructions" in sys_msg
    user_payload = fake_llm[0]["messages"][1]["content"]
    assert json.loads(user_payload)["id"] == "probe-1"  # case rides as JSON data


def test_garbage_output_raises_bounded(fake_llm, monkeypatch) -> None:
    monkeypatch.setattr(openrouter, "chat_completion", lambda *a, **k: _fake_payload("not json {"))
    fn = sl.make_solver_fn(environ={}, limits=dict(LIMITS), now=1.0)
    with pytest.raises((ValueError, json.JSONDecodeError)):
        fn(_case())


def test_attempt_solve_records_dispatch_with_model_tokens(fake_llm, tmp_path) -> None:
    spend: list[tuple] = []
    fn = sl.make_solver_fn(environ={}, limits=dict(LIMITS), log_spend=lambda c, m, t: spend.append((c.id, m)), now=1.0)
    row = attempt_solve(
        _case(),
        limits=dict(LIMITS),
        environ={"CASE_INTAKE_SOLVER_LIVE": "1"},
        state={},
        save_state=lambda: None,
        data_root=tmp_path,
        case_path=tmp_path / "case.json",
        now=1000.0,
        solver_fn=fn,
    )
    assert row["dispatched"] is True and row["reason"] == "dispatched"
    assert row["model"] == "z-ai/glm-5.3-flash" and row["tokens"] == 123
    assert row["solved"] is True
    assert spend and spend[0][0] == "probe-1"
    ledger = [json.loads(l) for l in (tmp_path / "case_intake" / "solver_receipts.jsonl").read_text().splitlines()]
    assert ledger[0]["reason"] == "dispatched"


def test_solver_fault_is_receipt_not_crash(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(openrouter, "require_api_key", lambda: "k-offline")
    monkeypatch.setattr(openrouter, "chat_completion", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("non-200 boom")))
    fn = sl.make_solver_fn(environ={}, limits=dict(LIMITS), now=1.0)
    row = attempt_solve(
        _case(),
        limits=dict(LIMITS),
        environ={"CASE_INTAKE_SOLVER_LIVE": "1"},
        state={},
        save_state=lambda: None,
        data_root=tmp_path,
        case_path=tmp_path / "case.json",
        now=1000.0,
        solver_fn=fn,
    )
    assert row["dispatched"] is True and row["reason"] == "solver_error"
    assert "non-200 boom" in row["note"]
