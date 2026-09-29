"""Collab protocol tests."""

from __future__ import annotations

import pytest

from chip import collab


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_sealed_submission_after_reveal_refused(data) -> None:
    collab.begin_brainstorm("dev", "goal", ["a", "b"])
    sealed = collab.submit_hypothesis("dev", "b", "idea")
    assert sealed["entries"][-1]["sealed"] is True
    collab.close_brainstorm("dev")
    with pytest.raises(PermissionError, match="reveal"):
        collab.submit_hypothesis("dev", "a", "late")


def _opened():
    collab.begin_brainstorm("dev", "goal", ["a", "b"])
    board = collab.submit_hypothesis("dev", "b", "idea")
    idea = board["entries"][-1]["id"]
    goal_id = board["entries"][0]["id"]
    collab.close_brainstorm("dev")
    return goal_id, idea


def test_self_critique_refused(data) -> None:
    goal_id, _idea = _opened()
    with pytest.raises(PermissionError, match="self-critique"):
        collab.critique("dev", "a", goal_id, "mine")


def test_early_convergence_refused_until_each_critiques_another(data) -> None:
    goal_id, idea = _opened()
    collab.critique("dev", "a", idea, "one")
    collab.critique("dev", "a", idea, "two")
    collab.critique("dev", "a", idea, "three")
    with pytest.raises(PermissionError, match="cap"):
        collab.critique("dev", "a", idea, "four")
    with pytest.raises(PermissionError, match="early"):
        collab.end_critique("dev")
    collab.critique("dev", "b", goal_id, "push on a")
    closed = collab.end_critique("dev")
    assert closed["critique_closed"] is True
