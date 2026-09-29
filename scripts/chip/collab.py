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


def _entry(board: dict[str, Any], entry_id: str) -> dict[str, Any] | None:
    for entry in board["entries"]:
        if entry.get("id") == entry_id:
            return entry
    return None


def critique(room: str, actor: str, target_id: str, body: str) -> dict[str, Any]:
    with boards.board_lock(room):
        board = _board(room)
        if board.get("phase") != "revealed" or board.get("critique_closed"):
            raise PermissionError("phase")
        if actor not in (board.get("participants") or []):
            raise PermissionError("participant")
        target = _entry(board, target_id)
        if target is None or target.get("kind") != "hypothesis":
            raise KeyError(target_id)
        if target.get("author") == actor:
            raise PermissionError("self-critique")
        posted = [
            entry
            for entry in board["entries"]
            if entry.get("kind") == "critique" and entry.get("author") == actor
        ]
        if len(posted) >= 3:
            raise PermissionError("cap")
        version = int(board["version"]) + 1
        board["entries"] = list(board["entries"]) + [
            {
                "id": f"c{version}",
                "author": actor,
                "kind": "critique",
                "body": body,
                "target_id": target_id,
                "sealed": False,
                "version": version,
            }
        ]
        board["version"] = version
        boards.save(board)
        return board


def end_critique(room: str) -> dict[str, Any]:
    with boards.board_lock(room):
        board = _board(room)
        participants = list(board.get("participants") or [])
        for persona in participants:
            covered = False
            for entry in board["entries"]:
                if entry.get("kind") != "critique" or entry.get("author") != persona:
                    continue
                target = _entry(board, str(entry.get("target_id")))
                author = (target or {}).get("author")
                if author and author != persona and author in participants:
                    covered = True
            if not covered:
                raise PermissionError("early")
        board["critique_closed"] = True
        boards.save(board)
        return board


def converge(
    room: str,
    chair: str,
    plan_body: str,
    assignments: list[dict[str, str]],
    dissent: list[dict[str, str]],
) -> dict[str, Any]:
    from chip import taskflow

    board = _board(room)
    if not board.get("critique_closed"):
        raise PermissionError("early")
    if chair != board.get("chair"):
        raise PermissionError("chair")
    for row in assignments:
        taskflow.claim_if_free(room, row["persona"], row["scope"])
    with boards.board_lock(room):
        board = _board(room)
        stored = [{"author": row["author"], "text": row["text"]} for row in dissent]
        board["decision"] = {
            "plan": plan_body,
            "assignments": list(assignments),
            "dissent": stored,
        }
        board["phase"] = "converged"
        version = int(board["version"]) + 1
        board["entries"] = list(board["entries"]) + [
            {
                "id": f"d{version}",
                "author": chair,
                "kind": "decision",
                "body": plan_body,
                "sealed": True,
                "version": version,
                "dissent": stored,
            }
        ]
        board["version"] = version
        boards.save(board)
        return board


def reject_hypothesis(room: str, entry_id: str, reason: str) -> dict[str, Any]:
    with boards.board_lock(room):
        board = _board(room)
        entry = _entry(board, entry_id)
        if entry is None or entry.get("kind") != "hypothesis":
            raise KeyError(entry_id)
        board["rejected"] = list(board.get("rejected") or []) + [
            {
                "id": entry_id,
                "body": entry.get("body"),
                "reason": reason,
                "author": entry.get("author"),
            }
        ]
        board["entries"] = [row for row in board["entries"] if row.get("id") != entry_id]
        board["version"] = int(board["version"]) + 1
        boards.save(board)
        return board


def rotate_chair(room: str) -> dict[str, Any]:
    with boards.board_lock(room):
        board = _board(room)
        people = list(board.get("participants") or [])
        if not people:
            raise ValueError("participants required")
        current = board.get("chair")
        index = people.index(current) if current in people else -1
        nxt = people[(index + 1) % len(people)]
        board["chair"] = nxt
        history = list(board.get("chair_history") or [])
        history.append(nxt)
        board["chair_history"] = history
        boards.save(board)
        return board
