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
    record_delegate_chain_hop,
)

_SPECIALIST_PERSONAS = ("buffrdev", "homelabdev", "opsdev", "chipchatdev")
from chip_relay.dispatch import (
    CHIP_TIMEOUT_S,
    ChipResult,
    Incoming,
    WebhookResult,
    handle_inbound_delegate,
    handle_message,
)


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
    assert posts[1][0] == "https://example.invalid/dev"
    assert posts[1][1] == "tester"
    assert posts[1][2].startswith("[chip-relay] ERROR: receipt schema:")
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


def test_lifetime_transcript_peak_does_not_block_new_human_delegate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old JSONL hop peaks must not cap delegates on a fresh human/inject turn."""
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    path = store.thread_path("dev")
    path.parent.mkdir(parents=True, exist_ok=True)
    store.log_message(
        "dev",
        "relay",
        "delegate:chipchatdev old chain",
        extra={
            "relay_handoff": {
                "from": "pm",
                "to": "chipchatdev",
                "task": "old",
                "hop": 11,
            }
        },
    )
    config = _dev_config()
    calls: list[list[str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del timeout
        calls.append(args)
        if len(calls) == 1:
            body = '{"reply": "routing\\ndelegate:chipchatdev add relay test"}'
        else:
            body = '{"reply": "chip lane ok"}'
        return ChipResult(0, body)

    result = handle_message(
        Incoming("chip-dev", "please delegate", "member"),
        config,
        chip=chip,
        webhook=lambda *a: WebhookResult(204),
        environ=_env(),
    )
    assert result.ok
    assert result.chip_calls == 2
    assert result.delegate_hops == 1
    assert calls[1][calls[1].index("--as") + 1] == "chipchatdev"
    last = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["relay_handoff"]["hop"] == 1
    assert not last["relay_handoff"].get("refused")


def test_hop_cap_refuses_fourth_delegate_in_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    room = RoomBinding(
        id="dev",
        as_agent="pm",
        webhook_env="CHIP_WEBHOOK_DEV",
        discord_channel="chip-dev",
        speakers_by_id={"1": "pm"},
    )
    config = RelayConfig(channel="chip-relay", turn_limit=1, auto=False, rooms=(room,))
    path = store.thread_path("dev")
    chip_calls: list[int] = []
    personas = iter(["tester", "dev", "tester", "dev"])

    def chip(args: list[str], timeout: int) -> ChipResult:
        del timeout
        chip_calls.append(1)
        if len(chip_calls) == 1:
            body = '{"reply": "chain start\\ndelegate:tester hop 1"}'
        else:
            body = '{"reply": "done"}'
        return ChipResult(0, body)

    def webhook(*_a) -> WebhookResult:
        return WebhookResult(204)

    result = handle_message(
        Incoming("chip-dev", "kickoff", "ally", author_id="1"),
        config,
        chip=chip,
        webhook=webhook,
        environ=_env(),
    )
    assert result.ok
    assert chip_calls == [1, 1]
    assert result.delegate_hops == 1

    for expected_hop in (2, 3):
        persona = next(personas)
        got = handle_inbound_delegate(
            room,
            from_persona="pm",
            content=f"delegate:{persona} hop {expected_hop}",
            chip=chip,
            webhook=webhook,
            environ=_env(),
        )
        assert got.ok
        assert got.delegate_hops == 1
        assert len(chip_calls) == expected_hop + 1

    refused_persona = next(personas)
    capped = handle_inbound_delegate(
        room,
        from_persona="pm",
        content=f"delegate:{refused_persona} one too many",
        chip=chip,
        webhook=webhook,
        environ=_env(),
    )
    assert capped.ok
    assert capped.delegate_hops == 0
    assert capped.alert == f"delegate hop cap ({MAX_DELEGATE_HOPS})"
    assert len(chip_calls) == MAX_DELEGATE_HOPS + 1
    last = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["relay_handoff"]["refused"] is True
    assert last["relay_handoff"]["hop"] == MAX_DELEGATE_HOPS + 1


def test_inject_does_not_reset_delegate_hop_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    room = RoomBinding(
        id="dev",
        as_agent="pm",
        webhook_env="CHIP_WEBHOOK_DEV",
        discord_channel="chip-dev",
        speakers_by_id={"1": "pm"},
    )
    config = RelayConfig(channel="chip-relay", turn_limit=1, auto=False, rooms=(room,))
    record_delegate_chain_hop("dev", MAX_DELEGATE_HOPS)
    calls: list[int] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del args, timeout
        calls.append(1)
        return ChipResult(0, '{"reply": "delegate:dev again"}')

    result = handle_message(
        Incoming("chip-dev", "again", "inject", author_id="1"),
        config,
        chip=chip,
        webhook=lambda *_a: WebhookResult(204),
        environ=_env(),
    )
    assert result.delegate_hops == 0
    assert result.alert == f"delegate hop cap ({MAX_DELEGATE_HOPS})"
    assert calls == [1]


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
