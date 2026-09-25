"""One inbound Discord message to one chip shot. No retries."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from chip_relay.config import RelayConfig

MAX_MESSAGE_CHARS = 2000
CHIP_TIMEOUT_S = 30


@dataclass
class Incoming:
    channel: str
    content: str
    author: str


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


def _fail(logs: list[str], alert: str, chip_calls: int = 0) -> DispatchResult:
    logs.append(alert)
    return DispatchResult(ok=False, alert=alert, chip_calls=chip_calls, logs=logs)


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

    args = [
        "room",
        room.id,
        "-m",
        text,
        "--as",
        room.as_agent,
        "--json",
        "--turn-limit",
        "1",
    ]
    outcome: ChipResult = chip(args, timeout=CHIP_TIMEOUT_S)
    if outcome.timed_out:
        return _fail(logs, "slow_call_killed", chip_calls=1)
    if outcome.returncode != 0:
        return _fail(logs, f"chip exit {outcome.returncode}", chip_calls=1)
    try:
        payload = json.loads(outcome.stdout.strip().splitlines()[-1])
        reply = str(payload["reply"])
    except (json.JSONDecodeError, KeyError, IndexError):
        return _fail(logs, "chip json malformed", chip_calls=1)

    url = environ.get(room.webhook_env, "")
    posted: WebhookResult = webhook(url, room.as_agent, reply)
    if posted.status >= 400:
        return _fail(logs, f"webhook {posted.status}", chip_calls=1)
    if record is not None:
        record(room.id, reply, room.as_agent)
    logs.append(f"posted {room.as_agent} to {incoming.channel}")
    return DispatchResult(ok=True, chip_calls=1, webhook_calls=1, logs=logs)
