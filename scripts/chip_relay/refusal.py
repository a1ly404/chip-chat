"""Canned refusal for al-9501. The first public act is this limitation, not a fix.

A human who asks "can you fix it?" gets HUMAN_FOLLOWUP and nothing else.
"""

from __future__ import annotations

FINGERPRINT = "al-9501-dead-image"

REFUSAL = "\n".join(
    [
        "claim: al-9501-dead-image is propose-only",
        "status: blocked",
        "evidence: none",
        "next: hold for the 4h soak; a pass is one data point",
    ]
)

HUMAN_FOLLOWUP = "\n".join(
    [
        "claim: al-9501-dead-image stays propose-only",
        "status: blocked",
        "evidence: none",
        "next: no fix from this channel; soak is read-only",
    ]
)

SOAK_NOT_BEFORE = "2026-10-01T02:11:00Z"


def post_refusal(*, room_id: str = "dev") -> str:
    """Post REFUSAL through the webhook and store that same gated body."""
    from chip import store
    from chip_relay.config import load_config
    from chip_relay.gateway import post_webhook
    from chip_relay.receipt_gate import gate_channel_body

    room = load_config().room(room_id)
    import os

    url = os.environ.get(room.webhook_env, "")
    body = gate_channel_body(REFUSAL)
    if body != REFUSAL:
        raise RuntimeError(body)
    posted = post_webhook(url, "pm", body)
    if posted.status >= 400:
        raise RuntimeError(f"webhook {posted.status}")
    store.log_message(room_id, "assistant", body, agent="pm")
    return body
