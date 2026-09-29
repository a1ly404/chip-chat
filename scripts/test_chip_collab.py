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


def _ready():
    goal_id, idea = _opened()
    collab.critique("dev", "a", idea, "on b")
    collab.critique("dev", "b", goal_id, "on a")
    collab.end_critique("dev")
    return goal_id, idea


def test_dissent_survives_convergence_verbatim(data) -> None:
    from chip import boards

    _goal, idea = _ready()
    dissent = [{"author": "b", "text": "keep the port"}]
    board = collab.converge(
        "dev",
        "a",
        "ship it",
        [{"persona": "b", "scope": "health-port"}],
        dissent,
    )
    decision = next(entry for entry in board["entries"] if entry["kind"] == "decision")
    assert decision["dissent"] == dissent
    assert board["decision"]["dissent"] == dissent
    with pytest.raises(PermissionError, match="dissent"):
        boards.edit_entry(board, actor="a", entry_id=decision["id"], body="rewritten")
    assert boards.load("dev")["decision"]["dissent"][0]["text"] == "keep the port"


def test_reject_hypothesis_blocks_resubmission(data) -> None:
    _goal, idea = _opened()
    collab.reject_hypothesis("dev", idea, "dead end")
    # phase is revealed, so brainstorm resubmit is also reveal-blocked.
    # Put the room back into brainstorm without clearing rejected.
    from chip import boards

    board = boards.load("dev")
    board["phase"] = "brainstorm"
    boards.save(board)
    with pytest.raises(PermissionError, match="rejected"):
        collab.submit_hypothesis("dev", "b", "idea")


def test_chair_gets_no_extra_tool_powers(data) -> None:
    from chip.lanes import decide
    from chip.tool import run

    board = collab.begin_brainstorm("dev", "goal", ["a", "b"])
    assert board["chair"] == "a"
    rotated = collab.rotate_chair("dev")
    assert rotated["chair"] == "b"
    assert rotated["chair_history"] == ["a", "b"]
    blob = "python3 -m pytest boards/dev.json"
    assert decide(blob, [("allow", "boards/")]) == "deny"
    with pytest.raises(PermissionError, match="boards/"):
        run("pytest", "boards/dev.json", execute=False, speaker=rotated["chair"], room="dev")


def test_ab_runner_both_arms_cost_accounting(data) -> None:
    from chip.collab_ab import run_ab

    ticks = iter([7])
    board = run_ab(
        "dev",
        "Should the health port stay 48082?",
        ["pm", "chipchatdev", "opsdev"],
        clock=lambda: next(ticks),
    )
    comparison = board["comparison"]
    assert comparison["hypotheses"]["solo"] == 1
    assert comparison["hypotheses"]["trio"] >= 1
    assert comparison["critiques_that_killed"] == 1
    assert comparison["time_ms"] == 7
    assert sum(comparison["tokens_by_persona"].values()) > 0
    assert comparison["grade"] == "pass"

