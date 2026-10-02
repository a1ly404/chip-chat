"""WIP adversarial coverage extension (post-wave main; draft PR #snapshot).

Contract: pin behaviors before pm's next lock rounds; every test runs against
the landed main at c7b2825 without inventing new law.
"""

from __future__ import annotations

import copy

import pytest

from chip import collab


@pytest.fixture(autouse=True)
def go_env(monkeypatch):
    monkeypatch.setenv("CHIP_LANE_GO_TICKET", "RAZ-335")
    monkeypatch.setenv("CHIP_TOPOLOGY_SWARM_GO", "1")
    yield


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_non_participant_hypothesis_refused(data) -> None:
    """Characterization: submit_hypothesis refuses an actor outside the roster."""
    collab.begin_brainstorm("dev", "goal", ["a", "b"])
    with pytest.raises(PermissionError, match="participant"):
        collab.submit_hypothesis("dev", "intruder", "outside")


def _opened_pair():
    collab.begin_brainstorm("dev", "goal", ["a", "b"])
    id_a = collab.submit_hypothesis("dev", "a", "idea-a")["entries"][-1]["id"]
    id_b = collab.submit_hypothesis("dev", "b", "idea-b")["entries"][-1]["id"]
    collab.close_brainstorm("dev")
    return id_a, id_b


def test_end_critique_after_close_is_idempotent(data) -> None:
    """Characterization: second end_critique leaves the board unchanged."""
    _id_a, _id_b = _opened_pair()
    collab.critique("dev", "a", _id_b, "b's idea")
    collab.critique("dev", "b", _id_a, "a's idea")
    first = collab.end_critique("dev")
    assert first["critique_closed"] is True
    snapshot = copy.deepcopy(first)
    second = collab.end_critique("dev")
    assert second["critique_closed"] is True
    assert second == snapshot


def test_reject_hypothesis_kind_mismatch_keyerror(data) -> None:
    """Characterization: reject refuses missing ids and non-hypothesis kinds."""
    _id_a, _id_b = _opened_pair()
    collab.critique("dev", "a", _id_b, "critiques b")
    board = collab.critique("dev", "b", _id_a, "critiques a")
    critique_ids = [
        entry["id"]
        for entry in board["entries"]
        if entry.get("kind") == "critique"
    ]
    assert critique_ids, "expected critique entries for kind-mismatch coverage"
    collab.end_critique("dev")
    with pytest.raises(KeyError):
        collab.reject_hypothesis("dev", "c999-nonexistent", "nope")
    with pytest.raises(KeyError):
        collab.reject_hypothesis("dev", critique_ids[0], "not a hypothesis")
