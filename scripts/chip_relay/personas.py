"""Map Discord authors to stable relay persona ids."""

from __future__ import annotations

from chip import pack_values
from chip_relay.config import RoomBinding


def _norm_handle(handle: str) -> str:
    return handle.strip().lower()


def _is_real_map_key(key: str) -> bool:
    """Placeholder keys in rooms.json start with TODO (not live Discord ids)."""
    stripped = str(key).strip()
    if not stripped:
        return False
    return not stripped.upper().startswith("TODO")


def speakers_maps_effective(room: RoomBinding) -> bool:
    """True when at least one non-TODO discord id or handle is configured."""
    if any(_is_real_map_key(k) for k in room.speakers_by_id):
        return True
    if any(_is_real_map_key(k) for k in room.speakers_by_handle):
        return True
    return False


def speakers_unconfigured_message(room: RoomBinding) -> str:
    return (
        f"speakers map for room {room.id} has no real Discord ids or handles "
        f"(replace TODO_* entries in {pack_values.rooms_config_hint()}, then restart chip-relay); "
        f"relay will not guess --as {room.as_agent!r}"
    )


def resolve_persona(
    room: RoomBinding,
    *,
    author_id: str = "",
    author_handle: str = "",
) -> str:
    """Return the persona id for a Discord author in this room.

    Resolution order (first match wins):

    1. ``room.speakers_by_id[author_id]`` when ``author_id`` is non-empty.
    2. ``room.speakers_by_handle[normalized_handle]`` when the handle is non-empty
       (normalized with strip + lower-case).
    3. ``room.as_agent`` — the room's configured default speaker; relay never
       posts under the raw Discord username when this fallback applies.
    """
    wanted_id = str(author_id).strip()
    if wanted_id and wanted_id in room.speakers_by_id:
        persona = room.speakers_by_id[wanted_id]
        if _is_real_map_key(wanted_id):
            return persona
    wanted_handle = _norm_handle(author_handle)
    if wanted_handle and wanted_handle in room.speakers_by_handle:
        persona = room.speakers_by_handle[wanted_handle]
        if _is_real_map_key(wanted_handle):
            return persona
    return room.as_agent


def resolve_persona_for_dispatch(
    room: RoomBinding,
    *,
    author_id: str = "",
    author_handle: str = "",
) -> tuple[str, str | None]:
    """Resolve persona for an inbound message, or return a loud alert string.

    When a room declares ``speakers`` in rooms.json but every entry is still a
    TODO placeholder (or the maps are empty), relay must not silently fall back
    to the room ``as`` field.
    """
    if room.speakers_declared and not speakers_maps_effective(room):
        return room.as_agent, speakers_unconfigured_message(room)
    return resolve_persona(room, author_id=author_id, author_handle=author_handle), None
