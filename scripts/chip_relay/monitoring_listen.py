"""Discord #monitoring-logs ingest — pm skill match/run/learnings (no standup secrets).

Enabled when ``CHIP_RELAY_MONITORING_LOGS=1`` (default off at runtime; room is
declared in ``rooms.json`` so deploy config is visible before the flag is flipped).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable

from chip.spend import CUTOFF_MONTHLY_USD, WARN_MONTHLY_USD, ledger_monthly_usd, load_ledger
from chip_relay.config import RoomBinding
from chip_relay.dispatch import DispatchResult, WebhookResult
from chip_relay.monitoring_skills import (
    parse_monitoring_message,
    post_target_allowed,
    run_skill_for_alert,
)
from chip_relay.receipt_gate import gate_channel_body

MONITORING_LISTEN_ENV = "CHIP_RELAY_MONITORING_LOGS"
_TRUE = {"1", "true", "yes", "on"}


def monitoring_listen_enabled(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(MONITORING_LISTEN_ENV, "").strip().lower() in _TRUE


def room_is_monitoring(room: RoomBinding) -> bool:
    return bool(getattr(room, "monitoring_listen", False)) or room.id == "monitoring-logs"


def monitoring_webhook_required(room: RoomBinding, environ: dict[str, str] | None = None) -> bool:
    return room_is_monitoring(room) and monitoring_listen_enabled(environ)


def spend_allows_live(environ: dict[str, str] | None = None) -> tuple[bool, str]:
    monthly = ledger_monthly_usd(load_ledger())
    if monthly >= CUTOFF_MONTHLY_USD:
        return False, f"monthly_usd={monthly} >= cutoff {CUTOFF_MONTHLY_USD}"
    if monthly >= WARN_MONTHLY_USD:
        return True, f"warn monthly_usd={monthly} >= {WARN_MONTHLY_USD}"
    return True, ""


@dataclass(frozen=True)
class MonitoringTurn:
    alert: dict[str, str]
    skill_id: str
    created_skill: bool
    receipt: str
    learning_line: str


def build_turn(content: str) -> MonitoringTurn:
    alert = parse_monitoring_message(content)
    run = run_skill_for_alert(alert)
    return MonitoringTurn(
        alert=alert,
        skill_id=run.skill_id,
        created_skill=run.created,
        receipt=run.receipt,
        learning_line=run.learning_line,
    )


def handle_monitoring_alert(
    room: RoomBinding,
    *,
    content: str,
    channel_id: str,
    webhook: Callable[[str, str, str], WebhookResult],
    environ: dict[str, str],
    record=None,
) -> DispatchResult:
    logs: list[str] = []
    if not monitoring_listen_enabled(environ):
        return DispatchResult(ok=True, logs=["monitoring listen disabled"])
    if not room_is_monitoring(room):
        return DispatchResult(ok=False, alert="not a monitoring room", logs=logs)

    allowed, reason = post_target_allowed(
        webhook_env=room.webhook_env,
        channel_id=str(channel_id),
        webhook_url=environ.get(room.webhook_env, ""),
    )
    if not allowed:
        logs.append(reason)
        return DispatchResult(ok=False, alert=reason, logs=logs)

    ok_spend, spend_note = spend_allows_live(environ)
    if not ok_spend:
        logs.append(spend_note)
        return DispatchResult(ok=False, alert=spend_note, logs=logs)
    if spend_note:
        logs.append(spend_note)

    turn = build_turn(content)
    url = (environ.get(room.webhook_env) or "").strip()
    if not url:
        return DispatchResult(ok=False, alert=f"{room.webhook_env} unset", logs=logs)

    body = gate_channel_body(turn.receipt)
    posted = webhook(url, "pm", body)
    if posted.status >= 400:
        return DispatchResult(ok=False, alert=f"webhook {posted.status}", logs=logs)
    if record is not None:
        record(room.id, body, "pm")

    log_row = {
        "room": room.id,
        "skill_id": turn.skill_id,
        "created_skill": turn.created_skill,
        "alert": turn.alert,
        "learning": turn.learning_line,
    }
    log_path = _dispatch_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(log_row, ensure_ascii=False, sort_keys=True) + "\n")

    logs.append(f"monitoring skill={turn.skill_id or 'none'} created={turn.created_skill}")
    return DispatchResult(ok=True, webhook_calls=1, logs=logs)


def _dispatch_log_path() -> Any:
    from chip import config

    return config.data_dir() / "chip_state" / "monitoring_dispatch.jsonl"
