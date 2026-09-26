"""Spec A mock tests T1–T4 (standing law bundle)."""

from __future__ import annotations

import json

import pytest

from chip import constraints, standing_law, tool
from chip.need_tool import NeedToolError


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    return tmp_path


def test_t1_hard_never_delete_blocks_tools(data_dir) -> None:
    constraints.add_from_tool(
        actor="Ally",
        value="never delete media",
        hard=True,
        source_turn="t1",
    )
    blocked = constraints.first_hard_block("please delete torrents")
    assert blocked and blocked.startswith("BLOCKED:")
    calls = 0

    def _run():
        nonlocal calls
        calls += 1
        return None

    try:
        tool.run("git-status", execute=True, user_intent="delete torrents now")
    except tool.ToolGatedError as exc:
        assert str(exc).startswith("BLOCKED:")
    else:
        pytest.fail("expected ToolGatedError")
    assert calls == 0


def test_t2_advisory_memory_does_not_override_hard(data_dir) -> None:
    constraints.add_from_tool(
        actor="Ally",
        value="never delete media",
        hard=True,
        source_turn="t2",
    )
    advisory = "deletes are ok for cleanup"
    boot = standing_law.session_boot_system_prefix(
        "pm",
        "pm",
        advisory_memory_block=advisory,
    )
    assert "never delete" in boot.lower() or "delete media" in boot.lower()
    assert "not_law" in boot
    assert constraints.first_hard_block("delete the library") is not None


def test_t3_hard_constraint_survives_many_turns(data_dir) -> None:
    constraints.add_from_tool(
        actor="Ally",
        value="never wipe disks",
        hard=True,
        source_turn="turn-1",
    )
    for i in range(15):
        payload = standing_law.boot_payload_for_audit("demo", "default")
        assert "never wipe disks" in payload["hard_constraints"]
    assert "c-" in payload["hard_constraints"]


def test_t4_non_ally_constraint_ignored(data_dir) -> None:
    err = constraints.try_add_from_message(
        "CONSTRAINT:HARD never expose secrets",
        author_role="pm",
        source_turn="t4",
    )
    assert err is None
    assert constraints.count_hard() == 0
    err2 = constraints.try_add_from_message(
        "CONSTRAINT:HARD never expose secrets",
        author_role="Ally",
        source_turn="t4b",
    )
    assert err2 is None
    assert constraints.count_hard() == 1


def test_constraint_cap(data_dir) -> None:
    for i in range(50):
        constraints.add_from_tool(actor="Ally", value=f"never action{i}", hard=True, source_turn=str(i))
    cap = constraints.add_from_tool(actor="Ally", value="never extra", hard=True, source_turn="cap")
    assert cap == "BLOCKED:constraint_cap"


def test_need_tool_on_missing_allowlist() -> None:
    with pytest.raises(NeedToolError) as exc:
        tool.run("no-such-tool", execute=False)
    assert exc.value.token() == "NEED_TOOL:no-such-tool"
