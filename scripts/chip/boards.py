"""Room boards: schema 1, sealed hypotheses, refresh-before-append."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from chip import config
from chip.lanes import decide

SCHEMA = 1


def board_path(room: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in room.strip()) or "room"
    return config.data_dir() / "boards" / f"{safe}.json"


def _empty(room: str) -> dict[str, Any]:
    return {"schema": SCHEMA, "room": room, "version": 0, "entries": []}


def _check(doc: dict[str, Any]) -> dict[str, Any]:
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"board schema must be {SCHEMA}")
    if not isinstance(doc.get("entries"), list):
        raise ValueError("board entries must be a list")
    if not isinstance(doc.get("version"), int):
        raise ValueError("board version must be an int")
    return doc


def load(room: str) -> dict[str, Any]:
    path = board_path(room)
    if not path.is_file():
        return _empty(room)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("board schema must be 1")
    return _check(doc)


def save(doc: dict[str, Any]) -> None:
    _check(doc)
    path = board_path(str(doc["room"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def refresh(board: dict[str, Any]) -> dict[str, Any]:
    """Version-check. Conflict reloads the file; same version keeps memory."""
    disk = load(str(board["room"]))
    if disk["version"] != board["version"]:
        return disk
    return board


def append_entry(board: dict[str, Any], *, actor: str, body: str) -> dict[str, Any]:
    board = refresh(board)
    version = int(board["version"]) + 1
    entry_id = f"h{version}"
    board["entries"] = list(board["entries"]) + [
        {
            "id": entry_id,
            "author": actor,
            "kind": "hypothesis",
            "body": body,
            "sealed": False,
            "version": version,
        }
    ]
    board["version"] = version
    save(board)
    return board


def seal_entry(board: dict[str, Any], *, actor: str, entry_id: str) -> dict[str, Any]:
    board = refresh(board)
    found = None
    for entry in board["entries"]:
        if entry["id"] == entry_id:
            found = entry
            break
    if found is None:
        raise KeyError(entry_id)
    if found["author"] != actor:
        raise PermissionError(f"deny seal author={found['author']}")
    if not found["sealed"]:
        found["sealed"] = True
        board["version"] = int(board["version"]) + 1
        save(board)
    return board


def edit_entry(board: dict[str, Any], *, actor: str, entry_id: str, body: str) -> dict[str, Any]:
    board = refresh(board)
    found = None
    for entry in board["entries"]:
        if entry["id"] == entry_id:
            found = entry
            break
    if found is None:
        raise KeyError(entry_id)
    if found["sealed"] and actor != found["author"]:
        decision = decide(
            f"edit {entry_id} author={found['author']}",
            [("allow", "edit"), ("deny", f"author={found['author']}")],
        )
        if decision == "deny":
            raise PermissionError(f"deny edit author={found['author']}")
    elif not found["sealed"]:
        decision = decide(f"edit {entry_id}", [("allow", "edit")])
        if decision != "allow":
            raise PermissionError("deny edit")
    found["body"] = body
    board["version"] = int(board["version"]) + 1
    save(board)
    return board


def list_open(board: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in board["entries"] if not e.get("sealed")]


def list_sealed(board: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in board["entries"] if e.get("sealed")]


def protocol_turn(board: dict[str, Any], *, actor: str, body: str) -> dict[str, Any]:
    from chip import store

    board = refresh(board)
    board = append_entry(board, actor=actor, body=body)
    store.append_jsonl(
        store.thread_path(str(board["room"])),
        {"kind": "board", "version": board["version"]},
    )
    return board


def refuse_board_mutation(path: Path, op: str) -> None:
    """Unlink and truncate of boards/ lose to a trailing deny."""
    blob = f"{op} {path}"
    if decide(blob, [("allow", "boards/"), ("allow", op)]) == "deny":
        raise PermissionError("global deny boards/")
    raise PermissionError(f"unsupported board op {op}")


def assert_tool_cannot_touch_boards(argv: list[str]) -> None:
    blob = " ".join(argv)
    if re.search(r"boards/", blob.replace("\\", "/"), re.IGNORECASE):
        decision = decide(blob, [("allow", "boards/")])
        if decision == "deny":
            raise PermissionError("global deny boards/")
