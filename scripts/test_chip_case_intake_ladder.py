from __future__ import annotations

import json
from pathlib import Path

from chip_relay import case_intake_ladder as ladder

CASE = {"id": "synthetic-t1", "description": "checkout-api restart loop on 8096", "repro_context": "x"}


def _run(tmp_path: Path, fn, **kw):
    cp = tmp_path / "case.json"
    return ladder.run_ladder(CASE, stage_fn=fn, data_root=tmp_path, repo_root=tmp_path,
                             case_path=cp, synthetic=True, **kw), cp


def test_flag_default_off():
    assert not ladder.ladder_enabled({})
    assert ladder.ladder_enabled({"CASE_INTAKE_LADDER": "1"})


def test_clean_case_verifies_with_per_tier_rows(tmp_path):
    out, cp = _run(tmp_path, ladder.make_mock_stage_fn())
    assert out["status"] == "verified" and out["verdict"] == "needs-repro"
    rows = json.loads(cp.read_text())["ladder_receipts"]
    assert {r["tier"] for r in rows} >= {"pm", "executor", "verifier"}
    assert all(r.get("synthetic") for r in rows)
    ledger = (tmp_path / "case_intake" / "ladder_receipts.jsonl").read_text().splitlines()
    assert len(ledger) == len(rows)


def test_planted_missing_receipt_bounces_then_recovers(tmp_path):
    out, cp = _run(tmp_path, ladder.make_mock_stage_fn(plant_defect=True))
    rows = json.loads(cp.read_text())["ladder_receipts"]
    first = next(r for r in rows if r["tier"] == "verifier")
    assert first["passed"] is False and any("missing_receipt" in d for d in first["defects"])
    assert out["bounces"] == 1 and out["status"] == "verified"


def test_bounce_limit_pages_dry_run_only(tmp_path):
    def always_bad(tier, payload, cfg):
        base = ladder.make_mock_stage_fn()(tier, payload, cfg)
        if tier == "executor":
            base["claims"] = [{"claim": "no receipt"}]
        return base

    out, cp = _run(tmp_path, always_bad, bounce_limit=2)
    assert out["status"] == "paged_dry_run" and out["bounces"] == 3
    page = [r for r in json.loads(cp.read_text())["ladder_receipts"] if r["tier"] == "page"]
    from chip import pack_values

    assert page and page[0]["sent"] is False and page[0]["escalate_to"] == pack_values.escalate_to_label()


def test_verdict_outside_frozen_band_rejected():
    defects = ladder.structural_check(
        {"verdict": "auto-fix", "claims": [{"claim": "c", "receipt": {"kind": "case_field", "ref": "description", "excerpt": "checkout-api"}}]},
        case=CASE, repo_root=Path("."))
    assert any("frozen_band" in d for d in defects)


def test_case_field_excerpt_must_match():
    defects = ladder.structural_check(
        {"verdict": "needs-repro", "claims": [{"claim": "c", "receipt": {"kind": "case_field", "ref": "description", "excerpt": "payments-api is down"}}]},
        case=CASE, repo_root=Path("."))
    assert defects == ["claim[0]:case_field_excerpt_mismatch:description"]


def test_tier_error_is_receipt_not_crash(tmp_path):
    def boom(tier, payload, cfg):
        raise RuntimeError("upstream 500")

    out, _ = _run(tmp_path, boom)
    assert out["status"] == "error"

