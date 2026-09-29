"""Collaboration rounds on the room board. protocol_turn stays unchanged."""

from __future__ import annotations

from typing import Any

from chip import boards


def _board(room: str) -> dict[str, Any]:
    return boards.load(room)


def begin_brainstorm(room: str, goal: str, participants: list[str]) -> dict[str, Any]:
    if not participants:
        raise ValueError("participants required")
    board = _board(room)
    board["phase"] = "brainstorm"
    board["goal"] = goal
    board["participants"] = list(participants)
    board["chair"] = participants[0]
    board["chair_history"] = [participants[0]]
    board["rejected"] = []
    board["decision"] = None
    board["critique_closed"] = False
    boards.save(board)
    return boards.protocol_turn(board, actor=participants[0], body=goal)


def submit_hypothesis(room: str, actor: str, body: str) -> dict[str, Any]:
    board = _board(room)
    if board.get("phase") != "brainstorm":
        raise PermissionError("reveal")
    if actor not in (board.get("participants") or []):
        raise PermissionError("participant")
    for row in board.get("rejected") or []:
        if row.get("body") == body:
            raise PermissionError("rejected")
    board = boards.protocol_turn(board, actor=actor, body=body)
    entry_id = board["entries"][-1]["id"]
    return boards.seal_entry(board, actor=actor, entry_id=entry_id)


def close_brainstorm(room: str) -> dict[str, Any]:
    boards.reveal_all(room)
    board = _board(room)
    board["phase"] = "revealed"
    boards.save(board)
    return board
