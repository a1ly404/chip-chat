"""Fixed relay config. Rooms come only from config/chip_relay/rooms.json."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from chip.config import REPO_ROOT

ROOMS_FILE = REPO_ROOT / "config" / "chip_relay" / "rooms.json"
TOKEN_ENV = "DISCORD_CHIP_RELAY_TOKEN"
AUTO_ENV = "CHIP_RELAY_AUTO"


@dataclass(frozen=True)
class RoomBinding:
    id: str
    as_agent: str
    webhook_env: str
    discord_channel: str


@dataclass(frozen=True)
class RelayConfig:
    channel: str
    turn_limit: int
    auto: bool
    rooms: tuple[RoomBinding, ...]

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


def auto_enabled(environ: dict[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    raw = source.get(AUTO_ENV, "false").strip().lower()
    return raw in ("1", "true", "yes")


def load_config(path: Path | None = None, environ: dict[str, str] | None = None) -> RelayConfig:
    file = path or ROOMS_FILE
    raw = json.loads(file.read_text(encoding="utf-8"))
    rooms: list[RoomBinding] = []
    for entry in raw.get("rooms") or []:
        rooms.append(
            RoomBinding(
                id=str(entry["id"]),
                as_agent=str(entry["as"]),
                webhook_env=str(entry["webhook_env"]),
                discord_channel=str(entry.get("discord_channel") or entry["id"]),
            )
        )
    return RelayConfig(
        channel=str(raw.get("channel") or "chip-relay"),
        turn_limit=int(raw.get("turn_limit") or 1),
        auto=auto_enabled(environ),
        rooms=tuple(rooms),
    )
