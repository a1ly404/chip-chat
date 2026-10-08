"""Discord gateway accept/skip rules and inject attribution."""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from chip import system_pack
from chip_relay.config import RelayConfig, RoomBinding
from chip_relay.case_intake import enabled as case_intake_enabled
from chip_relay.monitoring_listen import monitoring_listen_enabled, room_is_monitoring
from chip_relay.delegate import known_persona_ids
from chip_relay.wake import parse_inbound_handoff
from chip_relay.dispatch import Incoming

INJECT_AUTHOR_ID_ENV = "CHIP_RELAY_INJECT_AUTHOR_ID"
INJECT_BOT_IDS_ENV = "CHIP_RELAY_INJECT_BOT_IDS"


def operator_inject_author_id(environ: dict[str, str] | None = None) -> str:
    return system_pack.operator_discord_id(environ)


# Discord splits long posts into message.txt; inline text may be empty or truncated.
ATTACHMENT_TEXT_MAX_BYTES = 64 * 1024
ATTACHMENT_FETCH_TIMEOUT_S = 10.0
SHORT_INLINE_CONTENT_MAX_LEN = 2000


def _attachment_is_allowed_text(att: Any) -> bool:
    size = int(getattr(att, "size", 0) or 0)
    if size <= 0 or size > ATTACHMENT_TEXT_MAX_BYTES:
        return False
    filename = (getattr(att, "filename", None) or "").strip()
    content_type = (getattr(att, "content_type", None) or "").strip().lower()
    if filename == "message.txt":
        return True
    return content_type.startswith("text/plain")


def _decode_attachment_bytes(data: bytes) -> str | None:
    if not data or b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def fetch_attachment_text_from_url(url: str, *, timeout: float = ATTACHMENT_FETCH_TIMEOUT_S) -> str | None:
    """HTTP GET for a Discord attachment URL; fail closed on huge or non-text."""
    wanted = (url or "").strip()
    if not wanted:
        return None
    req = urllib.request.Request(wanted, headers={"User-Agent": "chip-relay"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(ATTACHMENT_TEXT_MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return None
    if len(data) > ATTACHMENT_TEXT_MAX_BYTES:
        return None
    return _decode_attachment_bytes(data)


def _embed_text_parts(embed: Any) -> list[str]:
    parts: list[str] = []
    for attr in ("title", "description"):
        value = getattr(embed, attr, None)
        if value is not None and str(value).strip():
            parts.append(str(value).strip())
    author = getattr(embed, "author", None)
    if author is not None:
        name = getattr(author, "name", None)
        if name is not None and str(name).strip():
            parts.append(str(name).strip())
    footer = getattr(embed, "footer", None)
    if footer is not None:
        text = getattr(footer, "text", None)
        if text is not None and str(text).strip():
            parts.append(str(text).strip())
    for field in getattr(embed, "fields", None) or []:
        name = (getattr(field, "name", None) or "").strip()
        value = (getattr(field, "value", None) or "").strip()
        if name or value:
            parts.append(f"{name}: {value}".strip(": "))
    return parts


def effective_monitoring_content(
    message: Any,
    *,
    fetch_url: Callable[[str], str | None] | None = None,
) -> str:
    """Inline text, attachments, embeds, and author label for monitoring-channel ingest."""
    inline = effective_gateway_content(message, fetch_url=fetch_url).strip()
    chunks: list[str] = []
    if inline:
        chunks.append(inline)
    for embed in getattr(message, "embeds", None) or []:
        chunks.extend(_embed_text_parts(embed))
    author = getattr(message, "author", None)
    author_name = getattr(author, "name", None) if author is not None else None
    if author_name and str(author_name).strip():
        chunks.append(f"source={str(author_name).strip()}")
    return "\n".join(chunks).strip()


def is_pm_monitoring_receipt_echo(message: Any, content: str) -> bool:
    """Ignore chip-relay pm webhook receipts so ingest does not loop."""
    from chip_relay.receipt_gate import receipt_error

    text = (content or "").strip()
    if not text:
        return False
    if text.startswith("[chip-relay] ERROR:"):
        return True
    webhook_id = getattr(message, "webhook_id", None)
    author = getattr(message, "author", None)
    author_name = (getattr(author, "name", None) or "").strip().lower()
    if webhook_id and author_name == "pm":
        return True
    if receipt_error(text) is None and text.lower().startswith("claim:"):
        if "tool:monitoring-skill." in text:
            return True
    if text.startswith("case-intake"):
        return True
    return False


def effective_gateway_content(
    message: Any,
    *,
    fetch_url: Callable[[str], str | None] | None = None,
) -> str:
    """Message content plus allowed text attachments when inline text is missing or short."""
    inline = (message.content or "").strip()
    attachments = getattr(message, "attachments", None) or []
    if not attachments:
        return inline
    if inline and len(inline) >= SHORT_INLINE_CONTENT_MAX_LEN:
        return inline

    fetch = fetch_url if fetch_url is not None else fetch_attachment_text_from_url
    bodies: list[str] = []
    for att in attachments:
        if not _attachment_is_allowed_text(att):
            continue
        url = (getattr(att, "url", None) or "").strip()
        if not url:
            continue
        text = fetch(url)
        if text is None:
            continue
        stripped = text.strip()
        if stripped:
            bodies.append(stripped)
    if not bodies:
        return inline
    if not inline:
        return "\n".join(bodies)
    return f"{inline}\n" + "\n".join(bodies)


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
    MONITORING_ALERT = "monitoring_alert"


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


def _human_incoming(message: Any, room: RoomBinding, content: str) -> Incoming:
    return Incoming(
        channel=room.discord_channel,
        content=content,
        author=str(message.author.name),
        author_id=str(message.author.id),
    )


def _inject_incoming(message: Any, room: RoomBinding, author_id: str, content: str) -> Incoming:
    return Incoming(
        channel=room.discord_channel,
        content=content,
        author="inject",
        author_id=author_id,
    )


def classify_gateway_message(
    message: Any,
    *,
    relay_bot_user_id: int,
    room: RoomBinding,
    environ: dict[str, str] | None = None,
    fetch_attachment: Callable[[str], str | None] | None = None,
) -> RouteDecision | None:
    """Return a routing decision, or None when the message must be ignored."""
    author = message.author
    author_id = getattr(author, "id", None)
    content = effective_gateway_content(message, fetch_url=fetch_attachment).strip()
    monitoring_content = ""
    monitoring_route = room_is_monitoring(room) and (
        case_intake_enabled(environ) or monitoring_listen_enabled(environ)
    )
    if monitoring_route:
        monitoring_content = effective_monitoring_content(message, fetch_url=fetch_attachment).strip()
    if author_id == relay_bot_user_id:
        if is_relay_self_inject_content(content):
            override = inject_author_id(environ)
            if override:
                return RouteDecision(
                    kind=RouteKind.INJECT_BOT,
                    incoming=_inject_incoming(message, room, override, content),
                )
        return None

    if monitoring_route:
        alert_text = monitoring_content or content
        if is_pm_monitoring_receipt_echo(message, alert_text):
            return None
        if alert_text.startswith("[chip-alert-ingest]"):
            return None
        if alert_text:
            return RouteDecision(kind=RouteKind.MONITORING_ALERT, content=alert_text)

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
                incoming=_inject_incoming(message, room, override, content),
            )
        return None

    return RouteDecision(kind=RouteKind.HUMAN, incoming=_human_incoming(message, room, content))


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
