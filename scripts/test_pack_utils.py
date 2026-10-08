"""Pack-aware helpers for generic engine tests (acme or private operator pack)."""


import os
from pathlib import Path

from chip import system_pack
from chip_relay import case_intake


def active_pack_name() -> str:
    try:
        return str(system_pack.load_pack().get("name") or system_pack.pack_name())
    except (OSError, ValueError):
        return system_pack.pack_name()


def is_private_pack_active() -> bool:
    name = active_pack_name()
    public_packs = frozenset({system_pack.DEFAULT_PACK, "acme-example"})
    if name in public_packs:
        return False
    if os.environ.get("CHIP_PACK_DIR", "").strip():
        return True
    return name not in public_packs


def intake_channels() -> list[str]:
    path = system_pack.optional_surface("case_intake")
    if path is None:
        return []
    limits = case_intake.load_limits(path)
    raw = str(limits.get("intake_channels") or "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def primary_intake_channel() -> str:
    channels = intake_channels()
    return channels[0] if channels else "alerts"


def dev_room_channel() -> str:
    from chip_relay.config import load_config

    for binding in load_config().rooms:
        if binding.id == "dev":
            return binding.discord_channel
    rooms = load_config().rooms
    return rooms[0].discord_channel if rooms else "dev"


def operator_discord_id() -> str:
    pack = system_pack.load_pack()
    return str(pack.get("identity", {}).get("operator_discord_id") or "")


def mock_specialists() -> frozenset[str]:
    from chip import mock

    return frozenset(mock._SPECIALISTS)


def first_specialist_persona() -> str:
    from chip import config

    personas = config.load_personas()
    for candidate in ("dev", "ops", "chipchatdev", *sorted(mock_specialists())):
        if candidate in personas and candidate in mock_specialists() and candidate != "pm":
            return candidate
    return "dev"


def channel_with_law() -> str:
    """Room id that has a channel law markdown file in the active pack."""
    from chip.law import CHANNELS_DIR

    for path in sorted(CHANNELS_DIR.glob("*.md")):
        return path.stem
    return "dev"


def channel_law_relative_label(room: str) -> str:
    from chip.law import load_room_law
    from chip.paths import REPO_ROOT

    loaded = load_room_law(room)
    if loaded.path is None:
        return "law=(none)"
    try:
        rel = loaded.path.relative_to(REPO_ROOT)
        return f"law={rel.as_posix()}"
    except ValueError:
        return f"law={loaded.path.as_posix()}"
