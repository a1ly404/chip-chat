"""Fixed relay config. Rooms come only from config/chip_relay/rooms.json."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from chip import system_pack

TOKEN_ENV = "DISCORD_CHIP_RELAY_TOKEN"


def _rooms_file() -> Path | None:
    override = globals().get("ROOMS_FILE")
    if isinstance(override, Path):
        return override
    return system_pack.optional_surface("rooms")


def __getattr__(name: str):
    if name == "ROOMS_FILE":
        path = _rooms_file()
        if path is None:
            raise FileNotFoundError("rooms surface missing")
        return path
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
AUTO_ENV = "CHIP_RELAY_AUTO"


def _speaker_maps(entry: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Parse optional ``speakers`` block: discord_ids and handles → persona id."""
    raw = entry.get("speakers")
    if not isinstance(raw, dict):
        return {}, {}
    by_id: dict[str, str] = {}
    ids = raw.get("discord_ids")
    if isinstance(ids, dict):
        for key, persona in ids.items():
            if key is not None and persona is not None:
                by_id[str(key).strip()] = str(persona).strip()
    by_handle: dict[str, str] = {}
    handles = raw.get("handles")
    if isinstance(handles, dict):
        for key, persona in handles.items():
            if key is not None and persona is not None:
                by_handle[str(key).strip().lower()] = str(persona).strip()
    return by_id, by_handle


@dataclass(frozen=True)
class RoomBinding:
    id: str
    as_agent: str
    webhook_env: str
    discord_channel: str
    channel_id: str = ""
    speakers_declared: bool = False
    speakers_by_id: dict[str, str] = field(default_factory=dict)
    speakers_by_handle: dict[str, str] = field(default_factory=dict)
    monitoring_listen: bool = False


@dataclass(frozen=True)
class RelayConfig:
    channel: str
    turn_limit: int
    auto: bool
    rooms: tuple[RoomBinding, ...]
    heartbeat_channel_id: str = ""

    def room(self, room_id: str) -> RoomBinding:
        for binding in self.rooms:
            if binding.id == room_id:
                return binding
        known = ", ".join(binding.id for binding in self.rooms)
        raise KeyError(f"room {room_id!r} is not configured ({known}); relay will not create rooms")

    def room_for_channel(self, discord_channel: str) -> RoomBinding:
        for binding in self.rooms:
            if binding.discord_channel == discord_channel:
                return binding
        known = ", ".join(binding.discord_channel for binding in self.rooms)
        raise KeyError(f"channel {discord_channel!r} is not configured ({known})")

    def room_for_channel_id(self, channel_id: str) -> RoomBinding:
        wanted = str(channel_id)
        for binding in self.rooms:
            if binding.channel_id == wanted:
                return binding
        raise KeyError(f"unknown channel {wanted}")


def auto_enabled(environ: dict[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    raw = source.get(AUTO_ENV, "false").strip().lower()
    return raw in ("1", "true", "yes")


def load_config(path: Path | None = None, environ: dict[str, str] | None = None) -> RelayConfig:
    file = path or _rooms_file()
    if file is None or not file.is_file():
        return RelayConfig(
            channel="chip-relay",
            turn_limit=1,
            auto=auto_enabled(environ),
            rooms=(),
        )
    raw = json.loads(file.read_text(encoding="utf-8"))
    rooms: list[RoomBinding] = []
    for entry in raw.get("rooms") or []:
        by_id, by_handle = _speaker_maps(entry)
        rooms.append(
            RoomBinding(
                id=str(entry["id"]),
                as_agent=str(entry["as"]),
                webhook_env=str(entry["webhook_env"]),
                discord_channel=str(entry.get("discord_channel") or entry["id"]),
                channel_id=str(entry.get("channel_id") or ""),
                speakers_declared="speakers" in entry,
                speakers_by_id=by_id,
                speakers_by_handle=by_handle,
                monitoring_listen=bool(entry.get("monitoring_listen")),
            )
        )
    return RelayConfig(
        channel=str(raw.get("channel") or "chip-relay"),
        turn_limit=int(raw.get("turn_limit") or 1),
        auto=auto_enabled(environ),
        rooms=tuple(rooms),
        heartbeat_channel_id=str(raw.get("heartbeat_channel_id") or ""),
    )
