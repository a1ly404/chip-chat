"""One inbound Discord message to one chip shot. No retries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from chip import store
from chip_relay.config import RelayConfig, RoomBinding
from chip_relay.delegate import (
    MAX_DELEGATE_HOPS,
    DelegateRequest,
    known_persona_ids,
    next_delegate_chain_hop,
    parse_delegate,
    record_delegate_chain_hop,
    reset_delegate_chain,
)
from chip_relay.personas import resolve_persona_for_dispatch
from chip_relay.wake import apply_wake_to_turn, parse_inbound_handoff

MAX_MESSAGE_CHARS = 2000
CHIP_TIMEOUT_S = 30
STAND_DOWN_ENV = "CHIP_STAND_DOWN"


def stand_down(environ: dict[str, str]) -> bool:
    """Grok-harvest port: CHIP_STAND_DOWN=1 pauses all delegate hops / persona wakes."""
    return environ.get(STAND_DOWN_ENV, "").strip() == "1"


def _stand_down_result(logs: list[str], room: RoomBinding, *, from_persona: str, request: "DelegateRequest", next_hop: int) -> DispatchResult:
    log_delegate_handoff(
        room.id,
        from_persona=from_persona,
        request=request,
        hop=next_hop,
        refused=True,
        reason="stand-down",
    )
    alert = "stand-down: delegate hops paused (CHIP_STAND_DOWN=1)"
    logs.append(alert)
    return DispatchResult(ok=True, alert=alert, logs=logs)

_LEADING_BRACKET = r"\[[a-z0-9_ .-]+\]"
_STACKED_PERSONA_PREFIX = re.compile(rf"^(?:{_LEADING_BRACKET}\s*){{2,}}", re.IGNORECASE)


def strip_stacked_persona_prefixes(reply: str) -> str:
    """Drop stacked leading ``[persona]`` markers echoed from transcript rendering.

    ``store.render`` prefixes history lines with ``[agent]``; models (and mock)
    echo those markers into replies, and each hop stacks another one
    (``[buffrdev] [buffrdev] [buffrdev] task``). The webhook ``username`` already
    identifies the speaker. Only a run of two or more leading bracketed tokens is
    an artifact — a single leading token may be intentional (e.g. ``[mock Chip]``).
    """
    return _STACKED_PERSONA_PREFIX.sub("", reply.lstrip(), count=1).lstrip()


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


def _chip_reply_failure(reply: str | None) -> str:
    """Return a dispatch alert when chip stdout has no postable reply, else \"\"."""
    if reply is None:
        return "chip json malformed"
    if not reply.strip():
        return "empty chip reply"
    return ""


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
    request = parse_inbound_handoff(text)
    if request is None:
        return _fail(logs, "no delegate line")
    if from_persona not in known_persona_ids():
        return _fail(logs, f"unknown webhook persona {from_persona!r}")

    next_hop = next_delegate_chain_hop(room.id)
    if stand_down(environ):
        return _stand_down_result(logs, room, from_persona=from_persona, request=request, next_hop=next_hop)
    refused = next_hop > MAX_DELEGATE_HOPS
    log_delegate_handoff(
        room.id,
        from_persona=from_persona,
        request=request,
        hop=next_hop,
        refused=refused,
        reason="hop cap" if refused else "",
    )
    record_delegate_chain_hop(room.id, next_hop)
    chip_calls = 0
    webhook_calls = 0
    cap_alert = f"delegate hop cap ({MAX_DELEGATE_HOPS})"
    if refused:
        logs.append(cap_alert)
        return DispatchResult(
            ok=True,
            alert=cap_alert,
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
    follow_reply = strip_stacked_persona_prefixes(follow_reply)
    reply_alert = _chip_reply_failure(follow_reply)
    if reply_alert:
        return _fail(logs, reply_alert, chip_calls=chip_calls)

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

    reset_delegate_chain(room.id)

    persona, persona_alert = resolve_persona_for_dispatch(
        room,
        author_id=incoming.author_id,
        author_handle=incoming.author,
    )
    if persona_alert:
        return _fail(logs, persona_alert)
    persona, text = apply_wake_to_turn(text, persona)
    reply, outcome = _run_chip_shot(room.id, persona, text, chip=chip)
    chip_calls = 1
    if outcome.timed_out:
        return _fail(logs, "slow_call_killed", chip_calls=chip_calls)
    if outcome.returncode != 0:
        return _fail(logs, f"chip exit {outcome.returncode}", chip_calls=chip_calls)
    reply = strip_stacked_persona_prefixes(reply)
    reply_alert = _chip_reply_failure(reply)
    if reply_alert:
        return _fail(logs, reply_alert, chip_calls=chip_calls)

    url = environ.get(room.webhook_env, "")
    posted: WebhookResult = webhook(url, persona, reply)
    webhook_calls = 1
    if posted.status >= 400:
        return _fail(logs, f"webhook {posted.status}", chip_calls=chip_calls)
    if record is not None:
        record(room.id, reply, persona)

    delegate_hops = 0
    request = parse_delegate(reply)
    cap_alert = f"delegate hop cap ({MAX_DELEGATE_HOPS})"
    if request is not None:
        next_hop = next_delegate_chain_hop(room.id)
        refused = next_hop > MAX_DELEGATE_HOPS
        log_delegate_handoff(
            room.id,
            from_persona=persona,
            request=request,
            hop=next_hop,
            refused=refused,
            reason="hop cap" if refused else "",
        )
        record_delegate_chain_hop(room.id, next_hop)
        if refused:
            logs.append(cap_alert)
        elif stand_down(environ):
            log_delegate_handoff(
                room.id,
                from_persona=persona,
                request=request,
                hop=next_hop,
                refused=True,
                reason="stand-down",
            )
            logs.append("stand-down: delegate hops paused (CHIP_STAND_DOWN=1)")
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
            follow_reply = strip_stacked_persona_prefixes(follow_reply)
            reply_alert = _chip_reply_failure(follow_reply)
            if reply_alert:
                return _fail(logs, reply_alert, chip_calls=chip_calls)
            posted = webhook(url, request.persona, follow_reply)
            webhook_calls += 1
            if posted.status >= 400:
                return _fail(logs, f"webhook {posted.status}", chip_calls=chip_calls)
            if record is not None:
                record(room.id, follow_reply, request.persona)
            logs.append(f"delegated {persona} -> {request.persona} hop={next_hop}")

    logs.append(f"posted {persona} to {incoming.channel}")
    hop_cap_alert = cap_alert if request is not None and refused else ""
    return DispatchResult(
        ok=True,
        alert=hop_cap_alert,
        chip_calls=chip_calls,
        webhook_calls=webhook_calls,
        logs=logs,
        delegate_hops=delegate_hops,
    )
