"""Config-pack v3 prep: the engine runs from the acme pack alone.

``acme-example`` is driven entirely by mock adapters over synthetic alerts — no
OpenRouter, no Discord, and no private operator pack on this path.
"""

from __future__ import annotations

import json

import pytest

from chip import config, system_pack
from chip_relay import case_intake
from chip_relay.case_intake_ladder import make_mock_stage_fn, run_ladder

PACK = system_pack.load_pack("acme-example")


def _alerts():
    path = system_pack.surface("alert_fixtures", PACK)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _mock_parser(alert: dict):
    def parse(_msg: str, _model: str) -> dict:
        return {
            "id": alert["id"],
            "description": alert["text"],
            "repro_context": alert["context"],
            "evidence_path": f"fixtures/{alert['id']}.log",
            "fingerprint_class": alert["class"],
            "_tokens": 0,
        }

    return parse


def test_pack_keeps_budget_gates():
    limits = case_intake.load_limits(system_pack.surface("case_intake", PACK))
    assert (limits["budget_warn_usd"], limits["budget_hard_usd"]) == (5, 10)
    assert limits["intake_channels"] == "ops-alerts"


def test_pack_allowlists_cannot_widen_engine_service_defaults():
    engine_defaults = frozenset({"payments-worker", "inventory-api"})
    assert system_pack.allowlist("service", engine_defaults, PACK) == frozenset()


@pytest.mark.parametrize("alert", _alerts(), ids=lambda a: a["id"])
def test_engine_runs_acme_case_end_to_end(alert, tmp_path):
    assert alert["synthetic"] is True
    limits = case_intake.load_limits(system_pack.surface("case_intake", PACK))
    classes = case_intake.load_taxonomy(system_pack.surface("case_taxonomy", PACK))
    res = case_intake.parse_freeform(alert["text"], parser=_mock_parser(alert), limits=limits, monthly_usd=0.0)
    assert res.kind == "case"
    assert case_intake.match_taxonomy(res.case, classes) == "known_class"

    case = {**res.case.as_dict(), "synthetic": True}
    case_path = tmp_path / "cases" / f"{alert['id']}.json"
    out = run_ladder(case, stage_fn=make_mock_stage_fn(), data_root=tmp_path, repo_root=config.REPO_ROOT,
                     case_path=case_path, evidence=alert["context"], synthetic=True)
    assert out["status"] == "verified"
    assert sum(out["spend_by_tier"].values()) == 0.0
    data = json.loads(case_path.read_text())
    assert all(r.get("synthetic") for r in data["ladder_receipts"])
    assert data["ladder_context"]["evidence"] == alert["context"]


def test_planted_defect_bounces_on_acme_case(tmp_path):
    alert = _alerts()[0]
    res = case_intake.parse_freeform(alert["text"], parser=_mock_parser(alert),
                                     limits=case_intake.load_limits(system_pack.surface("case_intake", PACK)))
    out = run_ladder({**res.case.as_dict(), "synthetic": True}, stage_fn=make_mock_stage_fn(plant_defect=True),
                     data_root=tmp_path, repo_root=config.REPO_ROOT, synthetic=True)
    verifier = [r for r in out["rows"] if r["tier"] == "verifier"]
    assert verifier[0]["passed"] is False and "missing_receipt" in verifier[0]["reason"]
    assert out["status"] == "verified" and out["bounces"] == 1
