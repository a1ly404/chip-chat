"""Room boards: schema 1, sealed hypotheses, refresh-before-append."""

from __future__ import annotations

import fcntl
import json
import os
import re
import socket
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from chip import config
from chip.lanes import decide

SCHEMA = 1
# Slice 6 sets this false. Until then a missing phase is stamped, not a permanent bypass.
ALLOW_PHASE_MIGRATION = False


def local_host() -> str:
    return socket.gethostname()


def _log_board(event: str, **fields: Any) -> None:
    from chip import store

    store.append_jsonl(
        config.data_dir() / "audit" / "board_events.jsonl",
        {"event": event, **fields},
    )


def board_path(room: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in room.strip()) or "room"
    return config.data_dir() / "boards" / f"{safe}.json"


def lock_path(room: str) -> Path:
    """Sidecar lock. Writers flock this file for the whole read-modify-write."""
    path = board_path(room)
    return path.with_name(path.name + ".lock")


@contextmanager
def board_lock(room: str) -> Iterator[None]:
    path = lock_path(room)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def _empty(room: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "room": room,
        "version": 0,
        "phase": "brainstorm",
        "entries": [],
    }


def _check(doc: dict[str, Any]) -> dict[str, Any]:
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"board schema must be {SCHEMA}")
    if not isinstance(doc.get("entries"), list):
        raise ValueError("board entries must be a list")
    if not isinstance(doc.get("version"), int):
        raise ValueError("board version must be an int")
    return doc


def _read_board_file(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("board schema must be 1")
    return _check(doc)


def _read_board_room(room: str, *, under_lock: bool = False) -> dict[str, Any]:
    path = board_path(room)
    if not path.is_file():
        return _empty(room)
    if under_lock:
        return _read_board_file(path)
    with board_lock(room):
        return _read_board_file(path)


def load(room: str, *, under_lock: bool = False) -> dict[str, Any]:
    doc = _read_board_room(room, under_lock=under_lock)
    if not doc.get("phase"):
        if not ALLOW_PHASE_MIGRATION:
            raise ValueError("board phase required")
        doc["phase"] = "brainstorm"
        save(doc)
        _log_board("board_phase_migrated", room=str(doc.get("room")))
    return doc


def save(doc: dict[str, Any]) -> None:
    _check(doc)
    path = board_path(str(doc["room"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def refresh(board: dict[str, Any], *, under_lock: bool = False) -> dict[str, Any]:
    """Version-check. Conflict reloads the file; same version keeps memory."""
    disk = _read_board_room(str(board["room"]), under_lock=under_lock)
    if disk["version"] != board["version"]:
        return disk
    return board


def append_entry(board: dict[str, Any], *, actor: str, body: str) -> dict[str, Any]:
    room = str(board["room"])
    with board_lock(room):
        # CAS: under the flock, a stale version reloads before the append.
        board = refresh(board, under_lock=True)
        version = int(board["version"]) + 1
        entry_id = f"h{version}"
        board["entries"] = list(board["entries"]) + [
            {
                "id": entry_id,
                "author": actor,
                "kind": "hypothesis",
                "body": body,
                "sealed": False,
                "readable": False,
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
    if found.get("kind") == "decision":
        raise PermissionError("dissent")
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


def reveal_all(room: str) -> dict[str, Any]:
    """Mark every sealed entry readable for a later round. Sealed stays true."""
    with board_lock(room):
        board = _read_board_room(room, under_lock=True)
        changed = False
        for entry in board["entries"]:
            if entry.get("sealed") and not entry.get("readable"):
                entry["readable"] = True
                changed = True
        if changed:
            board["version"] = int(board["version"]) + 1
            save(board)
        return board


def list_open(board: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in board["entries"] if not e.get("sealed")]


def list_sealed(board: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in board["entries"] if e.get("sealed")]


def wake_block(room: str) -> str:
    if not board_path(room).is_file():
        return ""
    board = load(room)
    try:
        assert_local_board(board)
    except PermissionError:
        return "board_foreign_host"
    lines = [
        f"round_id={board.get('round_id')}",
        f"phase={board.get('phase')}",
        f"version={board.get('version')}",
        f"host={board.get('host')}",
    ]
    for entry in (board.get("entries") or [])[-5:]:
        lines.append(f"{entry.get('id')}: {str(entry.get('body') or '')[:80]}")
    return "\n".join(lines)


def assert_local_board(board: dict[str, Any]) -> None:
    host = str(board.get("host") or "")
    data = str(board.get("data_dir") or "")
    if host and host != local_host():
        _log_board("board_foreign_host", room=str(board.get("room")), host=host)
        raise PermissionError("board_foreign_host")
    if data and data != str(config.data_dir()):
        _log_board("board_foreign_host", room=str(board.get("room")), data_dir=data)
        raise PermissionError("board_foreign_host")


def _refuse_spend_if_due(version: int) -> None:
    if version % 3 != 0:
        return
    from chip import spend

    spend.enforce_cutoff(spend.ledger_monthly_usd(spend.load_ledger()))


def protocol_turn(board: dict[str, Any], *, actor: str, body: str) -> dict[str, Any]:
    from chip import store

    board = refresh(board)
    phase = board.get("phase")
    if phase == "revealed":
        raise PermissionError("reveal")
    if phase == "converged":
        decision = board.get("decision") or {}
        if not str(decision.get("verified-as-of") or "").strip():
            raise PermissionError("verified-as-of")
    elif phase not in (None, "", "brainstorm", "idle"):
        raise PermissionError("reveal")
    assert_local_board(board)
    _refuse_spend_if_due(int(board.get("version") or 0) + 1)
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
