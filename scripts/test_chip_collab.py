"""Collab protocol tests."""

from __future__ import annotations

import pytest

from chip import collab


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHIP_LANE_GO_TICKET", "RAZ-335")
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


def test_post_reveal_append_as_chair_refused(data) -> None:
    collab.begin_brainstorm("dev", "goal", ["a", "b"])
    collab.close_brainstorm("dev")
    from chip import boards

    board = boards.load("dev")
    assert board["chair"] == "a"
    with pytest.raises(PermissionError, match="reveal"):
        boards.protocol_turn(board, actor="a", body="late as chair")


def test_rephrased_dead_end_resubmission_refused(data) -> None:
    from chip import boards

    _goal, idea = _opened()
    body = boards.load("dev")["entries"]
    idea_body = next(entry["body"] for entry in body if entry["id"] == idea)
    collab.reject_hypothesis("dev", idea, "dead end")
    board = boards.load("dev")
    board["phase"] = "brainstorm"
    boards.save(board)
    with pytest.raises(PermissionError, match="rejected"):
        collab.submit_hypothesis("dev", "b", idea_body.upper())


def test_foreign_host_refuses_append(data) -> None:
    from chip import boards

    board = collab.begin_brainstorm("dev", "goal", ["a"])
    board["host"] = "not-this-host"
    boards.save(board)
    with pytest.raises(PermissionError, match="board_foreign_host"):
        boards.protocol_turn(boards.load("dev"), actor="a", body="nope")


def test_missing_phase_hard_fails(data) -> None:
    import json

    from chip import boards

    path = boards.board_path("dev")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "room": "dev", "version": 0, "entries": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="phase"):
        boards.load("dev")


def test_fake_chair_and_lane_denied_converge(data, monkeypatch) -> None:
    from chip import store

    _ready()
    monkeypatch.setenv("CHIP_TURN_SPEAKER", "b")
    with pytest.raises(PermissionError, match="speaker"):
        collab.converge("dev", "a", "stolen", [], [])
    monkeypatch.setenv("CHIP_TURN_SPEAKER", "a")
    board = collab.converge(
        "dev",
        "a",
        "plan",
        [{"persona": "b", "scope": "/mnt/nas/media/movie"}],
        [],
    )
    assert board["decision"]["status"] == "aborted"
    assert board["decision"]["denied_scope"] == "/mnt/nas/media/movie"
    thread = store.thread_path("dev")
    text = thread.read_text(encoding="utf-8") if thread.is_file() else ""
    assert "claim:" not in text


def test_wake_includes_board_and_hides_foreign_body(data) -> None:
    from chip import boards
    from chip.cli import build_room_messages

    collab.begin_brainstorm("dev", "visible-goal", ["a"])
    messages = build_room_messages("dev", "pm", "hi")
    joined = "\n".join(message["content"] for message in messages)
    assert "visible-goal" in joined
    assert "round_id=" in joined
    board = boards.load("dev")
    board["host"] = "other-host"
    boards.save(board)
    hidden = "\n".join(message["content"] for message in build_room_messages("dev", "pm", "hi"))
    assert "board_foreign_host" in hidden
    assert "visible-goal" not in hidden


def test_empty_critique_refused_and_abstain_satisfies(data, monkeypatch) -> None:
    goal_id, idea = _opened()
    with pytest.raises(PermissionError, match="substantive"):
        collab.critique("dev", "a", idea, "   ")
    with pytest.raises(PermissionError, match="early"):
        collab.end_critique("dev")
    with pytest.raises(PermissionError, match="reason"):
        collab.abstain("dev", "a", "vibes")
    monkeypatch.setenv("CHIP_TURN_SPEAKER", "a")
    collab.abstain("dev", "a", "no_overlap")
    monkeypatch.setenv("CHIP_TURN_SPEAKER", "b")
    collab.critique("dev", "b", goal_id, "on the goal")
    closed = collab.end_critique("dev")
    assert closed["critique_closed"] is True


def test_dark_gate_blocks_critique_and_ab_without_ticket(data, monkeypatch) -> None:
    from chip.collab_ab import run_ab

    monkeypatch.delenv("CHIP_LANE_GO_TICKET", raising=False)
    with pytest.raises(PermissionError, match="go"):
        collab.critique("dev", "a", "h1", "x")
    with pytest.raises(PermissionError, match="go"):
        run_ab("dev", "problem", ["a", "b", "c"])
    board = collab.begin_brainstorm("dev", "goal", ["a"], go_ticket="RAZ-335")
    from chip import spend

    monkeypatch.setattr(spend, "ledger_monthly_usd", lambda _ledger: 10.0)
    with pytest.raises(spend.SpendCutoffError):
        collab.submit_hypothesis("dev", "a", "third")


def test_rotate_chair_require_protocol_go_blocked(data, monkeypatch) -> None:
    monkeypatch.delenv("CHIP_LANE_GO_TICKET", raising=False)
    with pytest.raises(PermissionError, match="go"):
        collab.rotate_chair("dev")

