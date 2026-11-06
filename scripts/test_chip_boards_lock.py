"""Portable board sidecar lock (POSIX fcntl / Windows msvcrt)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest


def test_boards_imports_without_top_level_fcntl() -> None:
    """Windows has no fcntl; boards must not import fcntl at module load."""
    import chip.boards as boards

    src = Path(boards.__file__).read_text(encoding="utf-8")
    head, _, _ = src.partition("def _lock_exclusive")
    assert "import fcntl" not in head
    assert hasattr(boards, "board_lock")
    assert hasattr(boards, "_lock_exclusive")
    assert hasattr(boards, "_unlock")


def test_board_lock_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import boards

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    room = "lock-smoke"
    order: list[str] = []
    hold = threading.Event()
    entered = threading.Event()

    def holder() -> None:
        with boards.board_lock(room):
            entered.set()
            hold.wait(timeout=5)
            order.append("holder-done")

    def waiter() -> None:
        with boards.board_lock(room):
            order.append("waiter-done")

    th = threading.Thread(target=holder)
    tw = threading.Thread(target=waiter)
    th.start()
    assert entered.wait(timeout=2)
    tw.start()
    time.sleep(0.15)
    # Waiter must still be blocked while holder holds the lock.
    assert order == []
    hold.set()
    th.join(timeout=5)
    tw.join(timeout=5)
    assert order == ["holder-done", "waiter-done"]
    assert boards.lock_path(room).is_file()


def test_wake_block_no_board_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import boards

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    assert boards.wake_block("no-such-room") == ""


def test_append_entry_under_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import boards

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    room = "append-room"
    board = boards.load(room)
    board = boards.append_entry(board, actor="pm", body="hypothesis one")
    assert board["version"] == 1
    assert len(board["entries"]) == 1
    reloaded = boards.load(room)
    assert reloaded["version"] == 1
    assert reloaded["entries"][0]["body"] == "hypothesis one"
