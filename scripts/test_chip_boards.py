"""Board mutation law: sealed edits, unlink/truncate, tool.run, refresh, schema 1."""

from __future__ import annotations

import json

import pytest

from chip import boards
from chip.lanes import decide
from chip.tool import run


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_sealed_edit_deny_beats_allow(data) -> None:
    board = boards.append_entry(boards.load("dev"), actor="b", body="claim")
    entry_id = board["entries"][0]["id"]
    board = boards.seal_entry(board, actor="b", entry_id=entry_id)
    blob = f"edit {entry_id} author=b"
    assert decide(blob, [("allow", "edit"), ("deny", "author=b")]) == "deny"
    with pytest.raises(PermissionError, match="author=b"):
        boards.edit_entry(board, actor="a", entry_id=entry_id, body="stolen")
    reloaded = boards.load("dev")
    assert reloaded["entries"][0]["body"] == "claim"


def test_board_unlink_and_truncate_refused(data, tmp_path) -> None:
    path = tmp_path / "boards" / "dev.json"
    path.parent.mkdir()
    path.write_text("{}\n", encoding="utf-8")
    for op in ("unlink", "truncate"):
        with pytest.raises(PermissionError, match="boards/"):
            boards.refuse_board_mutation(path, op)
    assert path.read_text(encoding="utf-8") == "{}\n"


def test_tool_run_persona_allow_cannot_reach_boards(data) -> None:
    argv_blob = "python3 -m pytest boards/dev.json"
    assert decide(argv_blob, [("allow", "boards/")]) == "deny"
    with pytest.raises(PermissionError, match="boards/"):
        run("pytest", "boards/dev.json", execute=False)


def test_protocol_turn_refreshes_on_version_conflict(data) -> None:
    first = boards.load("dev")
    stale = json.loads(json.dumps(first))
    boards.protocol_turn(first, actor="a", body="one")
    out = boards.protocol_turn(stale, actor="b", body="two")
    assert [e["body"] for e in out["entries"]] == ["one", "two"]
    assert out["version"] == 2
    assert boards.load("dev")["version"] == 2


def test_concurrent_writers_flock_keeps_both_entries(data) -> None:
    import threading

    errors: list[BaseException] = []

    def go(actor: str, body: str) -> None:
        try:
            boards.protocol_turn(boards.load("dev"), actor=actor, body=body)
        except BaseException as exc:  # noqa: BLE001 — surface any racer
            errors.append(exc)

    threads = [
        threading.Thread(target=go, args=("a", "one")),
        threading.Thread(target=go, args=("b", "two")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    board = boards.load("dev")
    assert board["version"] == 2
    assert sorted(entry["body"] for entry in board["entries"]) == ["one", "two"]
    assert boards.lock_path("dev").is_file()


def test_reveal_all_makes_sealed_readable(data) -> None:
    board = boards.append_entry(boards.load("dev"), actor="b", body="hidden")
    board = boards.seal_entry(board, actor="b", entry_id=board["entries"][0]["id"])
    revealed = boards.reveal_all("dev")
    assert revealed["entries"][0]["sealed"] is True
    assert revealed["entries"][0]["readable"] is True
    assert revealed["version"] == board["version"] + 1


def test_list_open_and_sealed_partition(data) -> None:
    board = boards.append_entry(boards.load("dev"), actor="a", body="open")
    board = boards.append_entry(board, actor="b", body="shut")
    board = boards.seal_entry(board, actor="b", entry_id=board["entries"][1]["id"])
    assert [e["body"] for e in boards.list_open(board)] == ["open"]
    assert [e["body"] for e in boards.list_sealed(board)] == ["shut"]


def test_board_schema_is_1(data) -> None:
    board = boards.load("dev")
    boards.save(board)
    assert boards.load("dev")["schema"] == 1
    path = boards.board_path("dev")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["schema"] = 2
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        boards.load("dev")


def test_refresh_same_version_does_not_drop_entries(data) -> None:
    board = boards.protocol_turn(boards.load("dev"), actor="a", body="keep")
    again = boards.refresh(board)
    assert again["entries"][0]["body"] == "keep"
    assert again["version"] == board["version"]
    assert len(boards.load("dev")["entries"]) == 1


def test_cli_board_show_schema_1(data, capsys) -> None:
    from chip.cli import main

    boards.save(boards.load("dev"))
    assert main(["board", "show", "dev"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["schema"] == 1


def test_cli_board_append_bumps_version(data, capsys) -> None:
    from chip.cli import main

    assert main(["board", "append", "dev", "--as", "a", "-m", "via cli"]) == 0
    assert boards.load("dev")["version"] == 1
    assert boards.load("dev")["entries"][0]["body"] == "via cli"


def test_protocol_turn_writes_thread_note(data) -> None:
    from chip import store

    out = boards.protocol_turn(boards.load("dev"), actor="a", body="noted")
    lines = store.thread_path("dev").read_text(encoding="utf-8").splitlines()
    note = json.loads(lines[-1])
    assert note == {"kind": "board", "version": out["version"]}
    path = boards.board_path("dev")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["schema"] = 2
    path.write_text(json.dumps(doc), encoding="utf-8")
    before = store.thread_path("dev").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        boards.protocol_turn(boards.load("dev"), actor="a", body="nope")
    assert store.thread_path("dev").read_text(encoding="utf-8") == before
