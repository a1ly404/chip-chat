"""Slice F / A2: CHIP_RELAY_LEARNINGS gates advisory learnings into relay turns."""

from __future__ import annotations

from pathlib import Path

import pytest

from chip import law, learnings
from chip.learnings import (
    ADVISORY_LABEL,
    MAX_BYTES,
    RELAY_LEARNINGS_ENV,
    advisory_learnings_for_turn,
    load_learnings,
    relay_learnings_enabled,
    render_learnings_block,
)


def _write_learnings(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "learnings.md"
    p.write_text("\n".join(["# test learnings", *lines, ""]) + "\n", encoding="utf-8")
    return p


def test_flag_default_off():
    assert relay_learnings_enabled({}) is False
    assert relay_learnings_enabled({RELAY_LEARNINGS_ENV: ""}) is False
    assert relay_learnings_enabled({RELAY_LEARNINGS_ENV: "0"}) is False
    assert relay_learnings_enabled({RELAY_LEARNINGS_ENV: "false"}) is False


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_flag_truthy(val: str):
    assert relay_learnings_enabled({RELAY_LEARNINGS_ENV: val}) is True


def test_advisory_for_turn_empty_when_flag_off(tmp_path: Path, monkeypatch):
    p = _write_learnings(tmp_path, ["2026-11-05 | relay | webhook one per room | relay"])
    monkeypatch.setattr(learnings, "learnings_path", lambda agent: p)
    assert advisory_learnings_for_turn("pm", environ={}) == ""
    assert advisory_learnings_for_turn("pm", environ={RELAY_LEARNINGS_ENV: "0"}) == ""
    # Pure renderer still returns content (for CLI/solver callers that opt in).
    assert "webhook one per room" in render_learnings_block("pm")


def test_advisory_for_turn_labelled_when_flag_on(tmp_path: Path, monkeypatch):
    p = _write_learnings(tmp_path, ["2026-11-05 | relay | webhook one per room | relay"])
    monkeypatch.setattr(learnings, "learnings_path", lambda agent: p)
    block = advisory_learnings_for_turn("pm", environ={RELAY_LEARNINGS_ENV: "1"})
    assert block.startswith(ADVISORY_LABEL)
    assert "webhook one per room" in block
    assert "never overrides ROOM LAW" in block


def test_compose_system_keeps_room_law_before_learnings():
    out = law.compose_system(
        "you are pm",
        law.RoomLaw(room="dev", path=None, law="be terse", memory=""),
        learnings_block=f"{ADVISORY_LABEL}\n- lesson",
    )
    assert "ROOM LAW" in out
    assert ADVISORY_LABEL in out
    assert out.index("be terse") < out.index(ADVISORY_LABEL)


def test_build_room_messages_omits_learnings_when_flag_off(tmp_path: Path, monkeypatch):
    from chip.cli import build_room_messages

    p = _write_learnings(tmp_path, ["2026-11-05 | relay | hop cap is 3 | relay"])
    monkeypatch.setattr(learnings, "learnings_path", lambda agent: p)
    monkeypatch.delenv(RELAY_LEARNINGS_ENV, raising=False)
    # Isolate room law / history from the live data dir.
    monkeypatch.setattr(law, "load_room_law", lambda room: law.RoomLaw(room=room, path=None, law="obey law", memory=""))
    monkeypatch.setattr("chip.store.load_room_history", lambda room, limit=None: [])
    monkeypatch.setattr("chip.cli._persona_system", lambda persona: f"persona:{persona}")
    monkeypatch.setattr("chip.cli.recall_mined_memory_block", lambda *a, **k: ("", 0))
    monkeypatch.setattr("chip.standing_law.session_boot_system_prefix", lambda *a, **k: "")
    monkeypatch.setattr("chip.boards.wake_block", lambda room: "")

    messages = build_room_messages("dev", "pm", "Status?")
    system = messages[0]["content"]
    assert "obey law" in system
    assert ADVISORY_LABEL not in system
    assert "hop cap is 3" not in system


def test_build_room_messages_includes_budgeted_learnings_when_flag_on(tmp_path: Path, monkeypatch):
    from chip.cli import build_room_messages

    # Over-budget lines: line cap + byte cap must still hold in the injected block.
    fat = ["x" * 200 for _ in range(40)]
    fat.append("2026-11-05 | relay | keep this newest lesson | relay")
    p = _write_learnings(tmp_path, fat)
    monkeypatch.setattr(learnings, "learnings_path", lambda agent: p)
    monkeypatch.setenv(RELAY_LEARNINGS_ENV, "1")
    monkeypatch.setattr(law, "load_room_law", lambda room: law.RoomLaw(room=room, path=None, law="obey law", memory=""))
    monkeypatch.setattr("chip.store.load_room_history", lambda room, limit=None: [])
    monkeypatch.setattr("chip.cli._persona_system", lambda persona: f"persona:{persona}")
    monkeypatch.setattr("chip.cli.recall_mined_memory_block", lambda *a, **k: ("", 0))
    monkeypatch.setattr("chip.standing_law.session_boot_system_prefix", lambda *a, **k: "")
    monkeypatch.setattr("chip.boards.wake_block", lambda room: "")

    messages = build_room_messages("dev", "pm", "Status?")
    system = messages[0]["content"]
    assert "obey law" in system
    assert ADVISORY_LABEL in system
    assert "keep this newest lesson" in system
    # Law binding precedes advisory learnings.
    assert system.index("obey law") < system.index(ADVISORY_LABEL)
    # Budget: injected bullet body stays within MAX_BYTES of loaded lines.
    loaded = load_learnings(p)
    assert len("\n".join(loaded).encode("utf-8")) <= MAX_BYTES
    assert len(loaded) <= learnings.MAX_LINES


def test_advisory_never_writes(tmp_path: Path, monkeypatch):
    p = _write_learnings(tmp_path, ["2026-11-05 | relay | read only | relay"])
    before = p.read_text(encoding="utf-8")
    mtime = p.stat().st_mtime_ns
    monkeypatch.setattr(learnings, "learnings_path", lambda agent: p)
    advisory_learnings_for_turn("pm", environ={RELAY_LEARNINGS_ENV: "1"})
    assert p.read_text(encoding="utf-8") == before
    assert p.stat().st_mtime_ns == mtime
