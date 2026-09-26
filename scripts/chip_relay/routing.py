"""Discord gateway accept/skip rules and inject attribution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

from chip_relay.config import RelayConfig, RoomBinding
from chip_relay.delegate import known_persona_ids
from chip_relay.wake import parse_inbound_handoff
from chip_relay.dispatch import Incoming

INJECT_AUTHOR_ID_ENV = "CHIP_RELAY_INJECT_AUTHOR_ID"
INJECT_BOT_IDS_ENV = "CHIP_RELAY_INJECT_BOT_IDS"
ALLY_INJECT_AUTHOR_ID = "187044496872046592"


def is_relay_self_inject_content(text: str) -> bool:
    """Markers for chip-relay posting its own GO/inject lines via CreateMessage."""
    wanted = (text or "").strip()
    if not wanted:
        return False
    if wanted.startswith("--inject"):
        return True
    if wanted.upper().startswith("GO:"):
        return True
    low = wanted.lower()
    return low.startswith("@pm") or low.startswith("@dev")


class RouteKind(str, Enum):
    HUMAN = "human"
    INJECT_BOT = "inject_bot"
    WEBHOOK_DELEGATE = "webhook_delegate"


@dataclass(frozen=True)
class RouteDecision:
    kind: RouteKind
    incoming: Incoming | None = None
    from_persona: str = ""
    content: str = ""


def _env(environ: dict[str, str] | None) -> dict[str, str]:
    return os.environ if environ is None else environ


def parse_inject_bot_ids(environ: dict[str, str] | None = None) -> frozenset[str]:
    raw = _env(environ).get(INJECT_BOT_IDS_ENV, "").strip()
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def inject_author_id(environ: dict[str, str] | None = None) -> str:
    return _env(environ).get(INJECT_AUTHOR_ID_ENV, "").strip()


def persona_from_webhook_username(name: str) -> str | None:
    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    known = known_persona_ids()
    if wanted in known:
        return wanted
    return None


def _human_incoming(message: Any, room: RoomBinding) -> Incoming:
    return Incoming(
        channel=room.discord_channel,
        content=message.content or "",
        author=str(message.author.name),
        author_id=str(message.author.id),
    )


def _inject_incoming(message: Any, room: RoomBinding, author_id: str) -> Incoming:
    return Incoming(
        channel=room.discord_channel,
        content=message.content or "",
        author="inject",
        author_id=author_id,
    )


def classify_gateway_message(
    message: Any,
    *,
    relay_bot_user_id: int,
    room: RoomBinding,
    environ: dict[str, str] | None = None,
) -> RouteDecision | None:
    """Return a routing decision, or None when the message must be ignored."""
    author = message.author
    author_id = getattr(author, "id", None)
    content = (message.content or "").strip()
    if author_id == relay_bot_user_id:
        if is_relay_self_inject_content(content):
            override = inject_author_id(environ)
            if override:
                return RouteDecision(
                    kind=RouteKind.INJECT_BOT,
                    incoming=_inject_incoming(message, room, override),
                )
        return None

    if not content:
        return None

    webhook_id = getattr(message, "webhook_id", None)
    if webhook_id:
        from_persona = persona_from_webhook_username(str(getattr(author, "name", "")))
        if from_persona and parse_inbound_handoff(content) is not None:
            return RouteDecision(
                kind=RouteKind.WEBHOOK_DELEGATE,
                from_persona=from_persona,
                content=content,
            )
        return None

    if getattr(author, "bot", False):
        bot_id = str(getattr(author, "id", ""))
        if bot_id and bot_id in parse_inject_bot_ids(environ):
            override = inject_author_id(environ)
            if not override:
                return None
            return RouteDecision(
                kind=RouteKind.INJECT_BOT,
                incoming=_inject_incoming(message, room, override),
            )
        return None

    return RouteDecision(kind=RouteKind.HUMAN, incoming=_human_incoming(message, room))


def inject_incoming_for_room(
    room: RoomBinding,
    *,
    content: str,
    author_id: str,
    author_handle: str = "inject",
) -> Incoming:
    return Incoming(
        channel=room.discord_channel,
        content=content,
        author=author_handle,
        author_id=author_id,
    )
