"""chip.standup tests — STANDUP_EMISSION_SPEC.md task 1 (receipt schema layer).

Contract under test:
- source=silent renders a STALL report and never confident-health text.
- phaseC_gate computes True ONLY for source==live AND age_h <= 20h; the gate
  is always computed here, a producer-claimed credit can never pass.
- Garbage receipts fail closed (errors collected; zero credit; no crash).
- consume() counts credited receipts only (Phase C credit counter).

Fixture law: synthetic standup names will never collide with the real homelab/
gymlog lanes; no network, no Linear, no webhook.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "chip"))

from chip import standup as su  # noqa: E402


def silent_receipt(age_h=None):
    return {
        "standup": "homelab",
        "issued_at": "2026-09-28 06:00 PT",
        "payload": {"source": "silent", "age_h": age_h, "sha": None, "sections_expected": []},
        "skills_commanded": False,
    }


def live_receipt(age_h):
    return {
        "standup": "gymlog",
        "issued_at": "2026-09-28 06:00 PT",
        "payload": {"source": "live", "age_h": age_h, "sha": "a" * 12, "sections_expected": ["workout"]},
        "skills_commanded": False,
    }


# --- schema validation -------------------------------------------------------

def test_valid_receipts_no_errors():
    assert su.validate_receipt(silent_receipt(30.0)) == []
    assert su.validate_receipt(silent_receipt(None)) == []  # never received
    assert su.validate_receipt(live_receipt(5.0)) == []


def test_garbage_receipts_fail_closed():
    bad = [
        {},
        {"standup": "homelab"},  # missing everything else
        {**silent_receipt(1), "payload": {"source": "weird"}},
        {**live_receipt(1), "payload": {"source": "live", "age_h": 1, "sha": "short"}},  # sha not 12
        {**silent_receipt(None), "payload": {"source": "silent", "age_h": None, "sha": "a" * 12}},
        {**silent_receipt(-5), "payload": {"source": "silent", "age_h": -5}},
    ]
    for r in bad:
        errs = su.validate_receipt(r)
        assert errs, f"expected failure for {r}"


def test_live_receipt_sha_question_back():
    r = live_receipt(2.0)
    r["payload"]["sha"] = None  # live without body fingerprint
    assert any("sha" in e for e in su.validate_receipt(r))


# --- phase C gate ------------------------------------------------------------

def test_gate_live_within_staleness():
    assert su.compute_phase_c_gate(live_receipt(0.0)["payload"]) is True
    assert su.compute_phase_c_gate({"source": "live", "age_h": 20}) is True   # boundary inclusive (<= 20h)
    assert su.compute_phase_c_gate({"source": "live", "age_h": 19.999}) is True


def test_gate_silent_never_never_stale():
    assert su.compute_phase_c_gate(silent_receipt(0.5)["payload"]) is False
    assert su.compute_phase_c_gate(silent_receipt(None)["payload"]) is False  # never received = no credit
    assert su.compute_phase_c_gate({"source": "live", "age_h": None}) is False
    assert su.compute_phase_c_gate({"source": "live", "age_h": 20.000001}) is False
    # STALENESS_MAX_H law pinned (spec rule 3)
    assert su.STALENESS_MAX_H == 20.0


def test_finalize_computes_gate_never_trusts_producer():
    r = live_receipt(2.0)
    r["credit"] = {"phaseC_gate": True, "reason": "trust me"}  # producer lies
    fin = su.finalize_receipt(r)
    assert fin["credit"]["phaseC_gate"] is True  # because numbers DO support it
    assert fin["credit"]["reason"] != "trust me"


def test_finalize_silent_zero_credit_with_reason():
    fin = su.finalize_receipt(silent_receipt(26.0))
    assert fin["credit"]["phaseC_gate"] is False
    assert "silence" in fin["credit"]["reason"]


def test_finalize_stale_live_zero_credit():
    fin = su.finalize_receipt(live_receipt(21.0))
    assert fin["credit"]["phaseC_gate"] is False
    assert "stale" in fin["credit"]["reason"]


def test_finalize_garbage_zero_credit_errors_attached():
    fin = su.finalize_receipt({})
    assert fin["credit"]["phaseC_gate"] is False
    assert "schema errors" in fin["credit"]["reason"]
    assert fin["_schema_errors"], "error inventory attached for receipts"


# --- silent stall rendering (silence can't dress up as work) ------------------

def test_render_stall_facts_without_confidence():
    text = su.render_stall(silent_receipt(26.0))
    assert "source=silent" in text
    assert "age_h=26h" in text
    assert "STALL" in text
    assert su.confident_health_hits(text) == [], "silent render must never claim health"


def test_render_stall_never_received():
    text = su.render_stall(silent_receipt(None))
    assert "never" in text and "source=silent" in text
    assert su.confident_health_hits(text) == []


def test_render_stall_caps_three_lines():
    for age in (None, 5.0, 96.0):
        assert len(su.render_stall(silent_receipt(age)).splitlines()) <= 3


def test_render_stall_rejects_live_receipts():
    try:
        su.render_stall(live_receipt(1.0))
    except su.StandupSchemaError:
        pass
    else:
        raise AssertionError("render_stall must refuse non-silent receipts")


# --- Phase C credit counter (consume) ----------------------------------------

def test_consume_counts_only_credited():
    rows = su.consume([
        live_receipt(2.0),      # credit
        live_receipt(40.0),     # stale — no credit
        silent_receipt(None),   # silent — no credit
        {},                     # garbage — no credit, no crash
    ])
    assert rows == {"received": 4, "credited": 1}


def test_consume_empty():
    assert su.consume([]) == {"received": 0, "credited": 0}


def test_silent_helper_receipt_shape():
    r = su.make_silent_receipt("gymlog", None, issued_at="2026-09-28 06:00 PT")
    assert r["credit"]["phaseC_gate"] is False
    assert r["payload"]["sha"] is None  # silent receipts carry no body fingerprint
    assert su.validate_receipt(r) == []


def test_silent_helper_requires_timestamp():
    try:
        su.make_silent_receipt("gymlog", None, issued_at="")
    except su.StandupSchemaError:
        pass
    else:
        raise AssertionError("silent receipt without issued_at must fail closed")
