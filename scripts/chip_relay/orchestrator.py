"""PM orchestrator tick — one pm follow-up after specialist receipt (flag default off)."""

from __future__ import annotations

import re
from typing import Callable

from chip import jobs, topology
from chip_relay.config import RoomBinding
from chip_relay.receipt_gate import gate_channel_body

ORCHESTRATOR_TICK_MARKER = "orchestrator-tick:"
_PM_STATUS = re.compile(r"^status:\s*(\S+)", re.IGNORECASE | re.MULTILINE)


def orchestrator_tick_enabled(environ: dict[str, str]) -> bool:
    block = topology.load().get("pm_orchestrator") or {}
    if not block.get("tick_after_specialist_receipt", True):
        return False
    key = str(block.get("unlock_env") or "CHIP_RELAY_PM_ORCHESTRATOR")
    val = (environ.get(key) or "").strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "off", "no"}:
        return False
    return bool(block.get("enabled_default", False))


def specialist_persona_ids() -> frozenset[str]:
    spec = topology.load().get("specialists") or {}
    ids = spec.get("persona_ids") or []
    out = {str(p).lower() for p in ids if str(p).strip()}
    out.add("dev")  # legacy hop smokes
    return frozenset(out)


def should_tick_after_specialist(specialist: str) -> bool:
    return specialist.lower() in specialist_persona_ids()


def build_tick_message(room_id: str, specialist: str, receipt: str) -> str:
    job = jobs.active_job(room_id)
    header = [
        f"{ORCHESTRATOR_TICK_MARKER} specialist={specialist} posted receipt.",
        "Close the GO: post a four-line receipt (status done|blocked|refused), "
        "or escalate operator. Optional: job:done <id> when acceptance met.",
    ]
    if job:
        header.append(
            f"Active job {job['id']}: goal={job['goal'][:240]} acceptance={job['acceptance'][:160]}"
        )
    header.extend(["", "--- specialist receipt ---", receipt.strip()[:1800]])
    return "\n".join(header)


def _pm_status_from_reply(reply: str) -> str | None:
    match = _PM_STATUS.search(reply or "")
    return match.group(1).lower() if match else None


def run_orchestrator_tick(
    room: RoomBinding,
    *,
    specialist: str,
    specialist_receipt: str,
    chip: Callable[..., object],
    webhook: Callable[..., object],
    environ: dict[str, str],
    url: str,
    record=None,
    chip_timeout: int = 30,
) -> tuple[bool, str, int, int]:
    """Run one pm shot + webhook. Returns (ok, alert, chip_calls, webhook_calls)."""
    from chip import orchestrator_metrics
    from chip_relay.dispatch import (
        ChipResult,
        WebhookResult,
        _chip_reply_failure,
        _chip_room_args,
        _parse_chip_reply,
        strip_stacked_persona_prefixes,
    )

    tick_text = build_tick_message(room.id, specialist, specialist_receipt)
    outcome: ChipResult = chip(
        _chip_room_args(room.id, "pm", tick_text),
        timeout=chip_timeout,
    )
    chip_calls = 1
    if outcome.timed_out or outcome.returncode != 0:
        return False, "orchestrator tick chip failed", chip_calls, 0
    reply = strip_stacked_persona_prefixes(_parse_chip_reply(outcome.stdout))
    reply_alert = _chip_reply_failure(reply)
    if reply_alert:
        return False, f"orchestrator tick {reply_alert}", chip_calls, 0

    posted_body = gate_channel_body(reply)
    posted: WebhookResult = webhook(url, "pm", posted_body)
    webhook_calls = 1
    if posted.status >= 400:
        return False, f"webhook {posted.status}", chip_calls, webhook_calls
    if record is not None:
        record(room.id, posted_body, "pm")

    done_id = jobs.parse_job_done_line(reply or "")
    if done_id:
        try:
            jobs.set_job_status(room.id, done_id, "done", note="orchestrator tick")
        except (FileNotFoundError, ValueError):
            pass
    status = _pm_status_from_reply(reply or "")
    if status == "done" and jobs.active_job(room.id):
        job = jobs.active_job(room.id)
        if job:
            try:
                jobs.set_job_status(room.id, str(job["id"]), "done", note="pm status done")
            except (FileNotFoundError, ValueError):
                pass

    orchestrator_metrics.log_metric(
        "orchestrator_tick",
        room=room.id,
        specialist=specialist,
        pm_status=status or "",
    )
    return True, "", chip_calls, webhook_calls
