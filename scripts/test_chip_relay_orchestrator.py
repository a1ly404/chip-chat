from __future__ import annotations
"""PM orchestrator tick after specialist delegate (flag default off)."""


import pytest

from chip import jobs
from chip_relay.config import RelayConfig, RoomBinding
from chip_relay.dispatch import (
    ChipResult,
    Incoming,
    WebhookResult,
    handle_inbound_delegate,
    handle_message,
)
from chip_relay.orchestrator import orchestrator_tick_enabled


def _env(flag: str = "") -> dict[str, str]:
    base = {"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"}
    if flag:
        base["CHIP_RELAY_PM_ORCHESTRATOR"] = flag
    return base


def _pm_room() -> RelayConfig:
    room = RoomBinding(
        id="dev",
        as_agent="pm",
        webhook_env="CHIP_WEBHOOK_DEV",
        discord_channel="dev-room",
        speakers_by_id={"1": "pm"},
    )
    return RelayConfig(channel="chip-relay", turn_limit=1, auto=False, rooms=(room,))


def test_orchestrator_flag_default_off() -> None:
    assert orchestrator_tick_enabled({}) is False


def test_orchestrator_flag_on_via_env() -> None:
    assert orchestrator_tick_enabled({"CHIP_RELAY_PM_ORCHESTRATOR": "1"}) is True


def test_delegate_without_flag_stays_two_chip_calls(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    config = _pm_room()
    calls: list[list[str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del timeout
        calls.append(args)
        if len(calls) == 1:
            body = '{"reply": "claim: route\\nstatus: done\\nevidence: TICKET-5001\\nnext: none\\ndelegate:chipchatdev run pytest"}'
        else:
            body = '{"reply": "claim: specialist\\nstatus: done\\nevidence: TICKET-5001\\nnext: none"}'
        return ChipResult(returncode=0, stdout=body)

    result = handle_message(
        Incoming("dev-room", "GO: slice", "operator", author_id="1"),
        config,
        chip=chip,
        webhook=lambda *_a: WebhookResult(204),
        environ=_env(),
    )
    assert result.ok
    assert result.chip_calls == 2
    assert result.orchestrator_ticks == 0
    assert len(calls) == 2


def test_orchestrator_tick_adds_pm_closure(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    jobs.create_job(
        "dev",
        owner_persona="pm",
        goal="relay orchestrator",
        acceptance="pm posts done",
        stop="mock inject",
        job_id="orc1",
    )
    config = _pm_room()
    calls: list[list[str]] = []
    posts: list[tuple[str, str]] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del timeout
        calls.append(args)
        persona = args[args.index("--as") + 1]
        if persona == "pm" and len(calls) == 1:
            body = '{"reply": "claim: plan\\nstatus: done\\nevidence: TICKET-5001\\nnext: none\\ndelegate:chipchatdev hop-ok-chipchatdev"}'
        elif persona == "chipchatdev":
            body = '{"reply": "claim: specialist receipt\\nstatus: done\\nevidence: TICKET-5001\\nnext: none"}'
        else:
            body = (
                '{"reply": "claim: orchestrator closed\\nstatus: done\\n'
                'evidence: TICKET-5001\\nnext: none\\njob:done orc1"}'
            )
        return ChipResult(returncode=0, stdout=body)

    def webhook(url: str, username: str, reply: str) -> WebhookResult:
        posts.append((username, reply))
        return WebhookResult(status=204)

    result = handle_message(
        Incoming("dev-room", "GO: orchestrator mock", "operator", author_id="1"),
        config,
        chip=chip,
        webhook=webhook,
        environ=_env("1"),
    )
    assert result.ok
    assert result.chip_calls == 3
    assert result.delegate_hops == 1
    assert result.orchestrator_ticks == 1
    assert calls[2][calls[2].index("--as") + 1] == "pm"
    assert posts[0][0] == "pm"
    assert posts[1][0] == "chipchatdev"
    assert posts[2][0] == "pm"
    assert jobs.load_job("dev", "orc1")["status"] == "done"


def test_inbound_delegate_orchestrator_tick(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    room = RoomBinding(
        id="dev",
        as_agent="pm",
        webhook_env="CHIP_WEBHOOK_DEV",
        discord_channel="dev-room",
    )
    calls: list[int] = []

    def chip(args: list[str], timeout: int) -> ChipResult:
        del timeout
        calls.append(1)
        persona = args[args.index("--as") + 1]
        if persona == "chipchatdev":
            body = '{"reply": "claim: hop\\nstatus: done\\nevidence: TICKET-5001\\nnext: none"}'
        else:
            body = '{"reply": "claim: pm close\\nstatus: done\\nevidence: TICKET-5001\\nnext: none"}'
        return ChipResult(returncode=0, stdout=body)

    result = handle_inbound_delegate(
        room,
        from_persona="pm",
        content="delegate:chipchatdev hop-ok-chipchatdev",
        chip=chip,
        webhook=lambda *_a: WebhookResult(204),
        environ=_env("1"),
    )
    assert result.ok
    assert result.chip_calls == 2
    assert result.orchestrator_ticks == 1
    assert len(calls) == 2
