"""Razzorstack standup key contract (mirror of python/standup_format.py — RAZ-329).

Chip Chat digest output MUST use these field names for Homelab + GymLog sections.
"""

from __future__ import annotations

from typing import Any

_GYMLOG_KEYS = (
    "gate",
    "deploy",
    "autodev_qa",
    "local_exec",
    "alerts",
    "discord_digest",
    "blockers",
)

_HOMELAB_KEYS = (
    "window_compliance",
    "remux",
    "nancy",
    "execute",
    "heartbeat",
    "host_slept",
    "clean_gate",
    "blockers",
)


def _text(value: Any, fallback: str = "unknown") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def format_gymlog_standup(day: str, payload: dict[str, Any]) -> str:
    lines = [
        f"**GymLog standup ({day})**",
        f"- **Gate:** {_text(payload.get('gate'))}",
        f"- **Deploy:** {_text(payload.get('deploy'))}",
        f"- **AutoDev/QA:** {_text(payload.get('autodev_qa'))}",
        f"- **local-exec (RAZ-167):** {_text(payload.get('local_exec'))}",
        f"- **Alerts:** {_text(payload.get('alerts'))}",
        f"- **Discord digest:** {_text(payload.get('discord_digest'))}",
        f"- **Blockers:** {_text(payload.get('blockers'))}",
    ]
    return "\n".join(lines)


def format_homelab_standup(day: str, payload: dict[str, Any]) -> str:
    lines = [
        f"**Homelab standup ({day})**",
        f"- **Window compliance:** {_text(payload.get('window_compliance'))}",
        f"- **Remux:** {_text(payload.get('remux'))}",
        f"- **Nancy:** {_text(payload.get('nancy'))}",
        f"- **Execute:** {_text(payload.get('execute'), 'FROZEN')}",
        f"- **Heartbeat:** {_text(payload.get('heartbeat'))}",
        f"- **Host slept:** {_text(payload.get('host_slept'))}",
        f"- **Counts toward 3-clean gate:** {_text(payload.get('clean_gate'))}",
        f"- **Blockers:** {_text(payload.get('blockers'))}",
    ]
    return "\n".join(lines)


def gymlog_stub_payload() -> dict[str, str]:
    return {k: "unknown" for k in _GYMLOG_KEYS} | {
        "blockers": "No agent payload received before standup window."
    }
