"""Counter-registry tests (JEV_INTEGRATION_PROPOSAL v4 fixture corpus law)."""

import json

import pytest

from chip import counter_registry as reg

ROW1 = {
    "task_id": "RAZ-199",
    "task_type": "done-gate",
    "status": "Done",
    "statusType": "completed",
    "checked_boxes": 4,
    "receipts_bound": 0,
    "unbound_done": True,
}


def test_registry_loads_and_pins_row1_fixture(tmp_path):
    reg_data = reg.load_registry()
    assert reg_data["fixtures"][0]["fixture_id"] == "row1-unbound-done"
    assert reg_data["counters"][0]["id"] == "done-gate-unbound-done"


def test_row1_shape_is_a_counter_hit():
    result = reg.precheck(ROW1)
    assert result.counter_id == "hit:done-gate-unbound-done"
    assert result.hit is True


def test_row1_shape_without_flags_still_counts(tmp_path):
    # same shape, no explicit unbound flag — derived from boxes vs receipts
    state = dict(ROW1)
    state.pop("unbound_done")
    result = reg.precheck(state)
    assert result.hit is True


def test_gray_state_scores_none():
    result = reg.precheck({"task_type": "wake-gray", "status": "In Progress",
                           "checked_boxes": 0})
    assert result.counter_id == "none" and result.hit is False


def test_registry_heals_never_match_by_accident(tmp_path):
    reg_data = {
        "version": 1,
        "counters": [{"id": "bad-shape", "matches": {"condition": "nonsense"}}],
    }
    f = tmp_path / "r.json"
    f.write_text(json.dumps(reg_data), encoding="utf-8")
    result = reg.precheck(ROW1, f)
    assert result.counter_id == "none"  # unknown entries fail quiet, never invent matches


def test_registry_garbage_refused():
    from chip.config import REPO_ROOT
    with pytest.raises(ValueError):
        reg.load_registry(path=(REPO_ROOT / "config" / "chip_tools.json"))


def test_duplicate_registry_keys_are_not_silent():
    data = reg.load_registry()
    ids = [c["id"] for c in data["counters"]]
    assert len(ids) == len(set(ids))
