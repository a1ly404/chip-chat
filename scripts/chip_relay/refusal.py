"""Canned refusal for dead-image propose-only runbooks.

A human who asks "can you fix it?" gets HUMAN_FOLLOWUP and nothing else.
"""

from __future__ import annotations

from chip import pack_values


def fingerprint() -> str:
    return pack_values.dead_image_fingerprint()


def refusal_body() -> str:
    return "\n".join(pack_values.dead_image_refusal_lines("refusal"))


def human_followup_body() -> str:
    return "\n".join(pack_values.dead_image_refusal_lines("human_followup"))


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
    body = gate_channel_body(refusal_body())
    if body != refusal_body():
        raise RuntimeError(body)
    posted = post_webhook(url, "pm", body)
    if posted.status >= 400:
        raise RuntimeError(f"webhook {posted.status}")
    store.log_message(room_id, "assistant", body, agent="pm")
    return body
