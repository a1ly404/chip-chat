"""Explicit delegate: handoff (mock chip + transcript only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chip import store
from chip_relay.config import RelayConfig, RoomBinding
from chip_relay.delegate import (
    MAX_DELEGATE_HOPS,
    known_persona_ids,
    parse_delegate,
    parse_delegate_line,
)

_SPECIALIST_PERSONAS = ("buffrdev", "homelabdev", "opsdev", "chipchatdev")
from chip_relay.dispatch import CHIP_TIMEOUT_S, ChipResult, Incoming, WebhookResult, handle_message


def _dev_config() -> RelayConfig:
    room = RoomBinding(
        id="dev",
        as_agent="dev",
        webhook_env="CHIP_WEBHOOK_DEV",
        discord_channel="chip-dev",
    )
    return RelayConfig(channel="chip-relay", turn_limit=1, auto=False, rooms=(room,))


def _env() -> dict[str, str]:
    return {"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"}


def test_known_persona_ids_includes_specialist_lanes() -> None:
    known = known_persona_ids()
    for pid in ("pm", "dev", "tester", *_SPECIALIST_PERSONAS):
        assert pid in known


def test_parse_delegate_line() -> None:
    got = parse_delegate_line("delegate:dev fix the flake in chip-selftest")
    assert got is not None
    assert got.persona == "dev"
    assert got.task == "fix the flake in chip-selftest"
    assert parse_delegate_line("delegate:unknown_persona do work") is None
    assert parse_delegate_line("not a delegate line") is None
    assert parse_delegate("ok\nDELEGATE:pm ship it") is not None
    assert parse_delegate("ok\nDELEGATE:pm ship it").persona == "pm"


def test_parse_delegate_specialist_lanes() -> None:
    samples = {
        "buffrdev": "fix Buffr login on gym-app",
        "homelabdev": "verify remux queue on Plex",
        "opsdev": "patch Oracle fleet-ops envelope",
        "chipchatdev": "add delegate test in chip-relay",
    }
    for persona, task in samples.items():
        line = f"delegate:{persona} {task}"
        got = parse_delegate_line(line)
        assert got is not None
        assert got.persona == persona
        assert got.task == task


def test_pm_to_buffrdev_delegate_hop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock transcript: pm delegates gym-app work; relay runs buffrdev follow-up."""
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    room = RoomBinding(
        id="dev",
        as_agent="pm",
        webhook_env="CHIP_WEBHOOK_DEV",
        discord_channel="chip-dev",
        speakers_by_id={"1": "pm"},
    )
    config = RelayConfig(channel="chip-relay", turn_limit=1, auto=False, rooms=(room,))
    calls: list[list[str]] = []
    posts: list[tuple[str, str, str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del timeout
        calls.append(args)
        if len(calls) == 1:
            body = '{"reply": "PM lane: routing Buffr\\ndelegate:buffrdev fix flaky gym login test"}'
        else:
            body = '{"reply": "BuffrDev lane: noted"}'
        return ChipResult(returncode=0, stdout=body)

    def webhook(url: str, username: str, reply: str) -> WebhookResult:
        posts.append((url, username, reply))
        return WebhookResult(status=204)

    result = handle_message(
        Incoming("chip-dev", "status on Buffr?", "ally", author_id="1"),
        config,
        chip=chip,
        webhook=webhook,
        environ=_env(),
    )
    assert result.ok
    assert result.chip_calls == 2
    assert calls[1][calls[1].index("--as") + 1] == "buffrdev"
    assert posts[0][1] == "pm"
    assert posts[1][1] == "buffrdev"


def test_enqueue_one_delegate_hop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    config = _dev_config()
    calls: list[list[str]] = []
    posts: list[tuple[str, str, str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        assert timeout == CHIP_TIMEOUT_S
        calls.append(args)
        if len(calls) == 1:
            body = '{"reply": "handing off\\ndelegate:tester run checks"}'
        else:
            body = '{"reply": "tests noted"}'
        return ChipResult(returncode=0, stdout=body)

    def webhook(url: str, username: str, reply: str) -> WebhookResult:
        posts.append((url, username, reply))
        return WebhookResult(status=204)

    result = handle_message(
        Incoming("chip-dev", "status?", "member"),
        config,
        chip=chip,
        webhook=webhook,
        environ=_env(),
    )
    assert result.ok
    assert result.chip_calls == 2
    assert result.delegate_hops == 1
    assert calls[1][calls[1].index("--as") + 1] == "tester"
    assert calls[1][calls[1].index("-m") + 1] == "run checks"
    assert posts[0][1] == "dev"
    assert posts[1] == ("https://example.invalid/dev", "tester", "tests noted")
    transcript = store.thread_path("dev").read_text(encoding="utf-8")
    assert "relay_handoff" in transcript


def test_no_delegate_means_single_chip_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    config = _dev_config()
    calls: list[int] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del args, timeout
        calls.append(1)
        return ChipResult(0, '{"reply": "plain answer"}')

    result = handle_message(
        Incoming("chip-dev", "hi", "member"),
        config,
        chip=chip,
        webhook=lambda *a: WebhookResult(204),
        environ=_env(),
    )
    assert result.ok
    assert calls == [1]
    assert result.delegate_hops == 0
    path = store.thread_path("dev")
    assert not path.is_file() or "relay_handoff" not in path.read_text(encoding="utf-8")


def test_hop_cap_refuses_fourth_delegate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    path = store.thread_path("dev")
    path.parent.mkdir(parents=True, exist_ok=True)
    for hop in (1, 2, 3):
        store.log_message(
            "dev",
            "relay",
            f"delegate:dev task{hop}",
            extra={"relay_handoff": {"from": "pm", "to": "dev", "task": f"t{hop}", "hop": hop}},
        )
    config = _dev_config()
    calls: list[int] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del args, timeout
        calls.append(1)
        return ChipResult(0, '{"reply": "delegate:dev one more"}')

    result = handle_message(
        Incoming("chip-dev", "again", "member"),
        config,
        chip=chip,
        webhook=lambda *a: WebhookResult(204),
        environ=_env(),
    )
    assert result.ok
    assert calls == [1]
    assert result.delegate_hops == 0
    assert any("hop cap" in line for line in result.logs)
    last = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["relay_handoff"]["refused"] is True
    assert last["relay_handoff"]["hop"] == MAX_DELEGATE_HOPS + 1


def test_auto_false_does_not_invent_delegate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CHIP_RELAY_AUTO stays off; only an explicit delegate: line enqueues a follow-up."""
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHIP_RELAY_AUTO", "false")
    config = _dev_config()
    assert config.auto is False
    calls: list[int] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del args, timeout
        calls.append(1)
        return ChipResult(0, '{"reply": "I could delegate but did not"}')

    handle_message(
        Incoming("chip-dev", "plan?", "member"),
        config,
        chip=chip,
        webhook=lambda *a: WebhookResult(204),
        environ={"CHIP_RELAY_AUTO": "false", **_env()},
    )
    assert calls == [1]
