"""Map Discord authors to stable relay persona ids."""

from __future__ import annotations

from chip_relay.config import RoomBinding


def _norm_handle(handle: str) -> str:
    return handle.strip().lower()


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
        return room.speakers_by_id[wanted_id]
    wanted_handle = _norm_handle(author_handle)
    if wanted_handle and wanted_handle in room.speakers_by_handle:
        return room.speakers_by_handle[wanted_handle]
    return room.as_agent
