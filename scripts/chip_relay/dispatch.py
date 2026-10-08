"""One inbound Discord message to one chip shot. No retries."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from chip import config, store
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
from chip_relay.receipt_gate import gate_channel_body
from chip_relay.wake import apply_wake_to_turn, parse_inbound_handoff

MAX_MESSAGE_CHARS = 2000
CHIP_TIMEOUT_S = 30
STAND_DOWN_ENV = "CHIP_STAND_DOWN"


def _flag_file() -> Path:
    return config.data_dir() / "chip_state" / "stand_down"


def stand_down(environ: dict[str, str] | None = None, *, _alert: list[str] | None = None) -> bool:
    """Grok-harvest port, round-5 hardened — fail-closed.

    Sources (any → frozen): durable flag file ``<data_dir>/chip_state/stand_down``
    (the SoT — every process must open it), or env exactly ``1``/``true``/``yes``/``on``.
    Env ``0``/``false``/``off``/absent does NOT override a present flag file.
    Garbage non-empty env values freeze too (fail-closed) and alert — never silently off.
    """
    import sys

    val = (environ or {}).get(STAND_DOWN_ENV, "").strip()
    if val == "1" or val.lower() in {"true", "yes", "on"}:
        return True
    if val and val.lower() not in {"0", "false", "off", "no"}:
        if _alert is not None:
            _alert.append(f"CHIP_STAND_DOWN garbage value {val!r} — fail-closed frozen")
        print(f"ERROR: CHIP_STAND_DOWN garbage value {val!r} — fail-closed frozen", file=sys.stderr, flush=True)
        return True
    return _flag_file().is_file()


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
    (``[chipchatdev] [chipchatdev] [chipchatdev] task``). The webhook ``username`` already
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
    orchestrator_ticks: int = 0


PROSE_RETRY_SUFFIX = (
    "\n\n(Answer the human in plain prose. Do not use claim:/status:/evidence:/next: lines.)"
)


def human_safe_body(reply: str, persona: str) -> str:
    """Prose left after dropping receipt-shaped lines; never a partial sitrep or bare ERROR."""
    from chip_relay.receipt_gate import _FIELD, receipt_error

    prose = "\n".join(l for l in reply.splitlines() if not _FIELD.match(l.strip())).strip()
    if prose and receipt_error(prose) is None:
        return prose
    return f"{persona} here — I couldn't put that answer into a clean reply. Please re-ask and I'll answer in plain prose."


_LAST_PM_BODY: dict[str, str] = {}


def same_state_pm_ack(room_id: str, persona: str, body: str) -> bool:
    """True when a non-human-turn pm post repeats the last pm post in the room
    (SPEC_VOICE_POLISH slice 3). Human asks are never suppressed by the caller."""
    if persona != "pm":
        return False
    key = hashlib.sha256(" ".join(body.lower().split()).encode()).hexdigest()
    if _LAST_PM_BODY.get(room_id) == key:
        return True
    _LAST_PM_BODY[room_id] = key
    return False


def _maybe_apply_scoped_go(incoming: Incoming, room, text: str, environ: dict[str, str]) -> str | None:
    from chip.go_scope import is_scoped_go_line

    if not is_scoped_go_line(text):
        return None
    from chip_relay.case_intake_pipeline import _operator_ids

    if str(incoming.author_id or "") not in _operator_ids(room, environ):
        return None
    from chip import go_write

    try:
        _verdict, receipt = go_write.try_apply_scoped_go(
            text,
            persona="pm",
            room=room.id,
            environ=environ,
        )
    except go_write.GoWriteNotWiredError:
        return "scoped GO apply disabled (CHIP_SCOPED_GO_ENABLED=0)"
    return receipt


def _maybe_record_ladder_outcome(incoming: Incoming, room, text: str, environ: dict[str, str]) -> str | None:
    """Operator ``outcome: <case_id> <verdict>`` → outcome row. Returns ack body, or None if not one."""
    from chip import system_pack
    from chip_relay import ladder_telemetry as tel
    from chip_relay.case_intake_pipeline import _operator_ids

    parsed = tel.parse_outcome_line(text)
    if parsed is None or str(incoming.author_id or "") not in _operator_ids(room, environ):
        return None
    from chip.config import data_dir

    source = system_pack.pack_identity_string("inject_discord_telemetry_source", default="operator_discord")
    res = tel.confirm_outcome(data_dir(), parsed[0], parsed[1], source=source)
    if not res["ok"]:
        return f"outcome not recorded: {parsed[0]} ({res['error']})"
    return (f"outcome recorded: {parsed[0]} confirmed={parsed[1]} ladder={res['ladder_verdict']} "
            f"correct={'yes' if res['correct'] else 'no'}")


def _fail(logs: list[str], alert: str, chip_calls: int = 0) -> DispatchResult:
    logs.append(alert)
    return DispatchResult(ok=False, alert=alert, chip_calls=chip_calls, logs=logs)


def _chip_room_args(room_id: str, persona: str, text: str, *, task_brief_opts: dict | None = None) -> list[str]:
    args = [
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
    if task_brief_opts:
        args += ["--task-id", str(task_brief_opts["task_id"])]
        if task_brief_opts.get("task_type"):
            args += ["--task-type", str(task_brief_opts["task_type"])]
    return args


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


def _maybe_pm_orchestrator_tick(
    room: RoomBinding,
    *,
    specialist: str,
    specialist_receipt: str,
    chip,
    webhook,
    environ: dict[str, str],
    url: str,
    record,
    logs: list[str],
) -> tuple[int, int, int, str]:
    """Returns (chip_calls, webhook_calls, orchestrator_ticks, alert)."""
    from chip_relay.orchestrator import (
        orchestrator_tick_enabled,
        run_orchestrator_tick,
        should_tick_after_specialist,
    )

    if not orchestrator_tick_enabled(environ):
        return 0, 0, 0, ""
    if not should_tick_after_specialist(specialist):
        return 0, 0, 0, ""
    ok, alert, extra_chip, extra_webhook = run_orchestrator_tick(
        room,
        specialist=specialist,
        specialist_receipt=specialist_receipt,
        chip=chip,
        webhook=webhook,
        environ=environ,
        url=url,
        record=record,
        chip_timeout=CHIP_TIMEOUT_S,
    )
    if not ok:
        logs.append(alert or "orchestrator tick failed")
        return extra_chip, extra_webhook, 0, alert
    logs.append(f"orchestrator tick pm after {specialist}")
    return extra_chip, extra_webhook, 1, ""


def _run_chip_shot(
    room_id: str,
    persona: str,
    text: str,
    *,
    chip: Callable[..., ChipResult],
    task_brief_opts: dict | None = None,
) -> tuple[str | None, ChipResult]:
    outcome: ChipResult = chip(
        _chip_room_args(room_id, persona, text, task_brief_opts=task_brief_opts),
        timeout=CHIP_TIMEOUT_S,
    )
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
    from chip import taskflow as _taskflow
    from chip_relay import delegate as _dl
    import os as _os

    brief_opts: dict = {}
    if (_os.environ.get("CHIP_TASKBRIEF", environ.get("CHIP_TASKBRIEF", "")) or "").strip().lower() in {"1", "true", "yes"}:
        try:
            brief_opts = _dl.task_brief_opts(text, request.persona, environ)
        except _taskflow.TaskPalaceUnavailable as exc:
            # fail-visible: REFUSE the delegation, log-only alert, no silent downgrade
            alert = f"delegation refused: {exc}"
            log_delegate_handoff(room.id, from_persona=from_persona, request=request,
                                 hop=next_delegate_chain_hop(room.id), refused=True,
                                 reason="palace-gate-fail-visible")
            record_delegate_chain_hop(room.id, next_delegate_chain_hop(room.id))
            logs.append(alert)
            return DispatchResult(ok=False, alert=alert, logs=logs)
        if brief_opts:
            text = f"{_dl.brief_text_for(request.persona, room.id, text, environ)}\n\n(channel task line: {text})"

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
        text if brief_opts else request.task,
        chip=chip,
        task_brief_opts=brief_opts or None,
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
    posted_body = gate_channel_body(follow_reply)
    posted: WebhookResult = webhook(url, request.persona, posted_body)
    webhook_calls = 1
    if posted.status >= 400:
        return _fail(logs, f"webhook {posted.status}", chip_calls=chip_calls)
    if record is not None:
        record(room.id, posted_body, request.persona)
    logs.append(f"delegated {from_persona} -> {request.persona} hop={next_hop}")
    logs.append(f"posted {request.persona} to {room.discord_channel}")
    orchestrator_ticks = 0
    extra_chip, extra_webhook, orchestrator_ticks, tick_alert = _maybe_pm_orchestrator_tick(
        room,
        specialist=request.persona,
        specialist_receipt=follow_reply or "",
        chip=chip,
        webhook=webhook,
        environ=environ,
        url=url,
        record=record,
        logs=logs,
    )
    chip_calls += extra_chip
    webhook_calls += extra_webhook
    return DispatchResult(
        ok=tick_alert == "",
        alert=tick_alert,
        chip_calls=chip_calls,
        webhook_calls=webhook_calls,
        logs=logs,
        delegate_hops=delegate_hops,
        orchestrator_ticks=orchestrator_ticks,
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

    from chip_relay.case_intake_pipeline import channel_intake_active, handle_case_intake

    if channel_intake_active(incoming.channel, environ):
        return handle_case_intake(incoming, room, webhook=webhook, environ=environ)

    outcome_ack = _maybe_record_ladder_outcome(incoming, room, text, environ)
    if outcome_ack is not None:
        posted = webhook(environ.get(room.webhook_env, ""), "pm", outcome_ack)
        if posted.status >= 400:
            return _fail(logs, f"webhook {posted.status}")
        logs.append("ladder outcome recorded")
        return DispatchResult(ok=True, webhook_calls=1, logs=logs)

    go_ack = _maybe_apply_scoped_go(incoming, room, text, environ)
    if go_ack is not None:
        posted = webhook(environ.get(room.webhook_env, ""), "pm", go_ack)
        if posted.status >= 400:
            return _fail(logs, f"webhook {posted.status}")
        logs.append("scoped GO handled")
        return DispatchResult(ok=True, webhook_calls=1, logs=logs)

    if incoming.author != "inject":
        reset_delegate_chain(room.id)

    persona, persona_alert = resolve_persona_for_dispatch(
        room,
        author_id=incoming.author_id,
        author_handle=incoming.author,
    )
    if persona_alert:
        return _fail(logs, persona_alert)
    persona, text = apply_wake_to_turn(text, persona)
    if persona == "chipchatdev":
        from chip import lanes as _lanes

        allowed, wake_reason = _lanes.chipchatdev_wake_allowed(text, room.id)
        if not allowed:
            return _fail(logs, wake_reason)
    prior_speaker = environ.get("CHIP_TURN_SPEAKER")
    environ["CHIP_TURN_SPEAKER"] = persona
    os.environ["CHIP_TURN_SPEAKER"] = persona
    try:
        reply, outcome = _run_chip_shot(room.id, persona, text, chip=chip)
    finally:
        if prior_speaker is None:
            environ.pop("CHIP_TURN_SPEAKER", None)
            os.environ.pop("CHIP_TURN_SPEAKER", None)
        else:
            environ["CHIP_TURN_SPEAKER"] = prior_speaker
            os.environ["CHIP_TURN_SPEAKER"] = prior_speaker
    chip_calls = 1
    if outcome.timed_out:
        return _fail(logs, "slow_call_killed", chip_calls=chip_calls)
    if outcome.returncode != 0:
        return _fail(logs, f"chip exit {outcome.returncode}", chip_calls=chip_calls)
    reply = strip_stacked_persona_prefixes(reply)
    reply_alert = _chip_reply_failure(reply)
    if reply_alert:
        return _fail(logs, reply_alert, chip_calls=chip_calls)

    human_turn = incoming.author != "inject"
    if human_turn and reply.lstrip().startswith("[mock "):
        return _fail(logs, "mock reply refused on human turn", chip_calls=chip_calls)
    url = environ.get(room.webhook_env, "")
    posted_body = gate_channel_body(reply)
    if human_turn and posted_body != reply:
        retry, retry_outcome = _run_chip_shot(room.id, persona, text + PROSE_RETRY_SUFFIX, chip=chip)
        chip_calls += 1
        retry = strip_stacked_persona_prefixes(retry) if retry_outcome.returncode == 0 and retry else ""
        if retry.strip() and not retry.lstrip().startswith("[mock ") and gate_channel_body(retry) == retry:
            reply = posted_body = retry
        else:
            posted_body = human_safe_body(reply, persona)
        logs.append("receipt gate on human turn: prose retry")
    if not human_turn and same_state_pm_ack(room.id, persona, posted_body):
        logs.append("pm same-state ack suppressed")
        return DispatchResult(ok=True, chip_calls=chip_calls, logs=logs)
    posted: WebhookResult = webhook(url, persona, posted_body)
    webhook_calls = 1
    if posted.status >= 400:
        return _fail(logs, f"webhook {posted.status}", chip_calls=chip_calls)
    if record is not None:
        record(room.id, posted_body, persona)

    delegate_hops = 0
    orchestrator_ticks = 0
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
            posted_body = gate_channel_body(follow_reply)
            posted = webhook(url, request.persona, posted_body)
            webhook_calls += 1
            if posted.status >= 400:
                return _fail(logs, f"webhook {posted.status}", chip_calls=chip_calls)
            if record is not None:
                record(room.id, posted_body, request.persona)
            logs.append(f"delegated {persona} -> {request.persona} hop={next_hop}")
            extra_chip, extra_webhook, orchestrator_ticks, tick_alert = _maybe_pm_orchestrator_tick(
                room,
                specialist=request.persona,
                specialist_receipt=follow_reply or "",
                chip=chip,
                webhook=webhook,
                environ=environ,
                url=url,
                record=record,
                logs=logs,
            )
            chip_calls += extra_chip
            webhook_calls += extra_webhook
            if tick_alert:
                return DispatchResult(
                    ok=False,
                    alert=tick_alert,
                    chip_calls=chip_calls,
                    webhook_calls=webhook_calls,
                    logs=logs,
                    delegate_hops=delegate_hops,
                    orchestrator_ticks=orchestrator_ticks,
                )

    logs.append(f"posted {persona} to {incoming.channel}")
    hop_cap_alert = cap_alert if request is not None and refused else ""
    return DispatchResult(
        ok=True,
        alert=hop_cap_alert,
        chip_calls=chip_calls,
        webhook_calls=webhook_calls,
        logs=logs,
        delegate_hops=delegate_hops,
        orchestrator_ticks=orchestrator_ticks,
    )
