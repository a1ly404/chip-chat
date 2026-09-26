"""One inbound Discord message to one chip shot. No retries."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from chip import store
from chip_relay.config import RelayConfig, RoomBinding
from chip_relay.delegate import (
    MAX_DELEGATE_HOPS,
    DelegateRequest,
    known_persona_ids,
    max_logged_delegate_hop,
    parse_delegate,
)
from chip_relay.personas import resolve_persona_for_dispatch

MAX_MESSAGE_CHARS = 2000
CHIP_TIMEOUT_S = 30


@dataclass
class Incoming:
    channel: str
    content: str
    author: str
    author_id: str = ""


@dataclass
class ChipResult:
    returncode: int
    stdout: str
    timed_out: bool = False


@dataclass
class WebhookResult:
    status: int


@dataclass
class DispatchResult:
    ok: bool
    alert: str = ""
    chip_calls: int = 0
    webhook_calls: int = 0
    logs: list[str] = field(default_factory=list)
    delegate_hops: int = 0


def _fail(logs: list[str], alert: str, chip_calls: int = 0) -> DispatchResult:
    logs.append(alert)
    return DispatchResult(ok=False, alert=alert, chip_calls=chip_calls, logs=logs)


def _chip_room_args(room_id: str, persona: str, text: str) -> list[str]:
    return [
        "room",
        room_id,
        "-m",
        text,
        "--as",
        persona,
        "--json",
        "--turn-limit",
        "1",
    ]


def _parse_chip_reply(stdout: str) -> str | None:
    try:
        payload = json.loads(stdout.strip().splitlines()[-1])
        return str(payload["reply"])
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def log_delegate_handoff(
    room_id: str,
    *,
    from_persona: str,
    request: DelegateRequest,
    hop: int,
    refused: bool = False,
    reason: str = "",
) -> None:
    """Record an explicit handoff in the room JSONL transcript."""
    content = f"delegate:{request.persona} {request.task}"
    extra: dict = {
        "relay_handoff": {
            "from": from_persona,
            "to": request.persona,
            "task": request.task,
            "hop": hop,
        }
    }
    if refused:
        extra["relay_handoff"]["refused"] = True
        if reason:
            extra["relay_handoff"]["reason"] = reason
    store.log_message(room_id, "relay", content, agent=from_persona, extra=extra)


def _run_chip_shot(
    room_id: str,
    persona: str,
    text: str,
    *,
    chip: Callable[..., ChipResult],
) -> tuple[str | None, ChipResult]:
    outcome: ChipResult = chip(_chip_room_args(room_id, persona, text), timeout=CHIP_TIMEOUT_S)
    if outcome.timed_out or outcome.returncode != 0:
        return None, outcome
    reply = _parse_chip_reply(outcome.stdout)
    return reply, outcome


def handle_inbound_delegate(
    room: RoomBinding,
    *,
    from_persona: str,
    content: str,
    chip,
    webhook,
    environ: dict[str, str],
    record=None,
) -> DispatchResult:
    """Webhook persona posted ``delegate:<target> <task>``; wake the target without a human turn."""
    logs: list[str] = []
    text = content.strip()
    if not text:
        return _fail(logs, "malformed message")
    if len(text) > MAX_MESSAGE_CHARS:
        return _fail(logs, "oversized message")
    request = parse_delegate(text)
    if request is None:
        return _fail(logs, "no delegate line")
    if from_persona not in known_persona_ids():
        return _fail(logs, f"unknown webhook persona {from_persona!r}")

    next_hop = max_logged_delegate_hop(room.id) + 1
    refused = next_hop > MAX_DELEGATE_HOPS
    log_delegate_handoff(
        room.id,
        from_persona=from_persona,
        request=request,
        hop=next_hop,
        refused=refused,
        reason="hop cap" if refused else "",
    )
    chip_calls = 0
    webhook_calls = 0
    if refused:
        logs.append(f"delegate hop cap ({MAX_DELEGATE_HOPS})")
        return DispatchResult(
            ok=True,
            chip_calls=chip_calls,
            webhook_calls=webhook_calls,
            logs=logs,
            delegate_hops=0,
        )

    follow_reply, follow_outcome = _run_chip_shot(
        room.id,
        request.persona,
        request.task,
        chip=chip,
    )
    chip_calls = 1
    delegate_hops = 1
    if follow_outcome.timed_out:
        return _fail(logs, "slow_call_killed", chip_calls=chip_calls)
    if follow_outcome.returncode != 0:
        return _fail(logs, f"chip exit {follow_outcome.returncode}", chip_calls=chip_calls)
    if follow_reply is None:
        return _fail(logs, "chip json malformed", chip_calls=chip_calls)

    url = environ.get(room.webhook_env, "")
    posted: WebhookResult = webhook(url, request.persona, follow_reply)
    webhook_calls = 1
    if posted.status >= 400:
        return _fail(logs, f"webhook {posted.status}", chip_calls=chip_calls)
    if record is not None:
        record(room.id, follow_reply, request.persona)
    logs.append(f"delegated {from_persona} -> {request.persona} hop={next_hop}")
    logs.append(f"posted {request.persona} to {room.discord_channel}")
    return DispatchResult(
        ok=True,
        chip_calls=chip_calls,
        webhook_calls=webhook_calls,
        logs=logs,
        delegate_hops=delegate_hops,
    )


def handle_message(
    incoming: Incoming,
    config: RelayConfig,
    *,
    chip,
    webhook,
    environ: dict[str, str],
    record=None,
) -> DispatchResult:
    logs: list[str] = []
    if not incoming.author.strip() or not isinstance(incoming.content, str):
        return _fail(logs, "malformed message")
    text = incoming.content.strip()
    if not text:
        return _fail(logs, "malformed message")
    if len(text) > MAX_MESSAGE_CHARS:
        return _fail(logs, "oversized message")
    try:
        room = config.room_for_channel(incoming.channel)
    except KeyError:
        return _fail(logs, f"unknown channel {incoming.channel}")

    persona, persona_alert = resolve_persona_for_dispatch(
        room,
        author_id=incoming.author_id,
        author_handle=incoming.author,
    )
    if persona_alert:
        return _fail(logs, persona_alert)
    reply, outcome = _run_chip_shot(room.id, persona, text, chip=chip)
    chip_calls = 1
    if outcome.timed_out:
        return _fail(logs, "slow_call_killed", chip_calls=chip_calls)
    if outcome.returncode != 0:
        return _fail(logs, f"chip exit {outcome.returncode}", chip_calls=chip_calls)
    if reply is None:
        return _fail(logs, "chip json malformed", chip_calls=chip_calls)

    url = environ.get(room.webhook_env, "")
    posted: WebhookResult = webhook(url, persona, reply)
    webhook_calls = 1
    if posted.status >= 400:
        return _fail(logs, f"webhook {posted.status}", chip_calls=chip_calls)
    if record is not None:
        record(room.id, reply, persona)

    delegate_hops = 0
    request = parse_delegate(reply)
    if request is not None:
        next_hop = max_logged_delegate_hop(room.id) + 1
        refused = next_hop > MAX_DELEGATE_HOPS
        log_delegate_handoff(
            room.id,
            from_persona=persona,
            request=request,
            hop=next_hop,
            refused=refused,
            reason="hop cap" if refused else "",
        )
        if refused:
            logs.append(f"delegate hop cap ({MAX_DELEGATE_HOPS})")
        else:
            follow_reply, follow_outcome = _run_chip_shot(
                room.id,
                request.persona,
                request.task,
                chip=chip,
            )
            chip_calls += 1
            delegate_hops = 1
            if follow_outcome.timed_out:
                return _fail(logs, "slow_call_killed", chip_calls=chip_calls)
            if follow_outcome.returncode != 0:
                return _fail(logs, f"chip exit {follow_outcome.returncode}", chip_calls=chip_calls)
            if follow_reply is None:
                return _fail(logs, "chip json malformed", chip_calls=chip_calls)
            posted = webhook(url, request.persona, follow_reply)
            webhook_calls += 1
            if posted.status >= 400:
                return _fail(logs, f"webhook {posted.status}", chip_calls=chip_calls)
            if record is not None:
                record(room.id, follow_reply, request.persona)
            logs.append(f"delegated {persona} -> {request.persona} hop={next_hop}")

    logs.append(f"posted {persona} to {incoming.channel}")
    return DispatchResult(
        ok=True,
        chip_calls=chip_calls,
        webhook_calls=webhook_calls,
        logs=logs,
        delegate_hops=delegate_hops,
    )
