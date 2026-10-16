"""Notice/beat webhook resolution.

System lines (budget notices, dispatcher beats, health tells, graduation
summaries) go to the #chip-relay webhook (``CHIP_WEBHOOK_RELAY``) and fall
back to the legacy #chip-dev webhook (``CHIP_WEBHOOK_DEV``) when RELAY is unset.
Never logs values.
"""

from __future__ import annotations

import os
from typing import Mapping

RELAY_ENV = "CHIP_WEBHOOK_RELAY"
DEV_ENV = "CHIP_WEBHOOK_DEV"


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def notice_webhook_env(environ: Mapping[str, str] | None = None) -> str:
    """Name of the env var that will be used (RELAY if set, else DEV)."""
    env = _env(environ)
    return RELAY_ENV if (env.get(RELAY_ENV) or "").strip() else DEV_ENV


def notice_webhook_url(environ: Mapping[str, str] | None = None) -> str:
    env = _env(environ)
    return (env.get(notice_webhook_env(env)) or "").strip()


def resolve_room_url(room_env: str, environ: Mapping[str, str] | None = None) -> str:
    """URL for an explicit room env; RELAY falls back to DEV when unset."""
    env = _env(environ)
    url = (env.get(room_env) or "").strip()
    if not url and room_env == RELAY_ENV:
        url = (env.get(DEV_ENV) or "").strip()
    return url
