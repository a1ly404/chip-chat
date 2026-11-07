"""Relay message construction loads advisory learnings through dispatch (slice F proof).

"Actual relay entrypoint" proof parity row 9 needs: the relay's turn path is
``dispatch.handle_message -> _run_chip_shot -> chip room`` — ``chip room``
then builds the turn prompt via ``chip.cli.build_room_messages``. This test
drives ``handle_message`` with a chip callable that performs the SAME
construction ``bin/chip room`` performs (the real ``build_room_messages``, with
only its external reads stubbed), under the relay's law:

- learnings land ONLY when ``CHIP_RELAY_LEARNINGS`` is truthy (default off);
- the block is budgeted (``MAX_LINES`` / ``MAX_BYTES``);
- ROOM LAW stays authoritative (binding text precedes the advisory label);
- when the flag is off the constructed turn carries no learnings at all.

Offline: no model calls, no webhooks; learnings.md is a temp file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chip import law, learnings
from chip.cli import build_room_messages
from chip_relay.config import RelayConfig, RoomBinding
from chip_relay.dispatch import ChipResult, Incoming, WebhookResult, handle_message

ADVISORY_LINE = "2026-11-06 | relay | webhook one per room | relay"


def _config() -> RelayConfig:
    room = RoomBinding(
        id="dev",
        as_agent="pm",
        webhook_env="CHIP_WEBHOOK_DEV",
        discord_channel="dev-room",
        speakers_by_id={"1": "pm"},
    )
    return RelayConfig(channel="chip-relay", turn_limit=1, auto=False, rooms=(room,))


def _stub_construction_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, flag: str) -> list[list[dict[str, str]]]:
    """Stub the external reads of build_room_messages; keep the real composition."""
    fat = ["x" * 300 for _ in range(50)]
    fat.append(ADVISORY_LINE)  # newest-wins keeps this line inside the budget
    learning_path = tmp_path / "learnings.md"
    learning_path.write_text("\n".join(fat) + "\n", encoding="utf-8")
    monkeypatch.setattr(learnings, "learnings_path", lambda agent: learning_path)
    monkeypatch.setattr(law, "load_room_law", lambda room: law.RoomLaw(room=room, path=None, law="be terse", memory=""))
    monkeypatch.setattr("chip.store.load_room_history", lambda room, limit=None: [])
    monkeypatch.setattr("chip.cli._persona_system", lambda persona: f"persona:{persona}")
    monkeypatch.setattr("chip.cli.recall_mined_memory_block", lambda *a, **k: ("", 0))
    monkeypatch.setattr("chip.standing_law.session_boot_system_prefix", lambda *a, **k: "")
    monkeypatch.setattr("chip.boards.wake_block", lambda room: "")
    if flag:
        monkeypatch.setenv(learnings.RELAY_LEARNINGS_ENV, flag)
    else:
        monkeypatch.delenv(learnings.RELAY_LEARNINGS_ENV, raising=False)
    constructed: list[list[dict[str, str]]] = []
    return constructed


def test_dispatch_turn_carries_budgeted_learnings_when_flag_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chip.learnings import MAX_BYTES

    constructed = _stub_construction_inputs(tmp_path, monkeypatch, flag="1")

    def chip(args: list[str], timeout: int) -> ChipResult:
        del timeout
        room_id, persona, message = args[1], args[3], args[5]
        constructed.append(build_room_messages(room_id, persona, message))
        return ChipResult(returncode=0, stdout=json.dumps({"reply": "ok"}))

    result = handle_message(
        Incoming("dev-room", "Status?", "operator", author_id="1"),
        _config(),
        chip=chip,
        webhook=lambda *_a: WebhookResult(204),
        environ={"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"},
    )
    assert result.ok
    system = constructed[0][0]["content"]
    assert learnings.ADVISORY_LABEL in system
    assert ADVISORY_LINE in system  # budgeted block kept the newest line
    assert "be terse" in system  # ROOM LAW present...
    assert system.index("be terse") < system.index(learnings.ADVISORY_LABEL)  # ...and authoritative
    block = system.split(learnings.ADVISORY_LABEL, 1)[1]
    assert len(block.encode("utf-8")) <= MAX_BYTES


def test_dispatch_turn_has_no_learnings_when_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    constructed = _stub_construction_inputs(tmp_path, monkeypatch, flag="")

    def chip(args: list[str], timeout: int) -> ChipResult:
        del timeout
        room_id, persona, message = args[1], args[3], args[5]
        constructed.append(build_room_messages(room_id, persona, message))
        return ChipResult(returncode=0, stdout=json.dumps({"reply": "ok"}))

    result = handle_message(
        Incoming("dev-room", "Status?", "operator", author_id="1"),
        _config(),
        chip=chip,
        webhook=lambda *_a: WebhookResult(204),
        environ={"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"},  # flag unset
    )
    assert result.ok
    system = constructed[0][0]["content"]
    assert learnings.ADVISORY_LABEL not in system
    assert ADVISORY_LINE not in system
    assert "be terse" in system  # LAW stays without advisory content