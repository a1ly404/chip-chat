"""Selftest persona resolution (no live OpenRouter or Discord)."""

from __future__ import annotations

from pathlib import Path

from chip_relay.selftest import resolve_selftest_persona

ROOMS = Path(__file__).resolve().parent / "fixtures" / "relay_rooms.json"


def test_selftest_resolves_persona_from_speakers_map(monkeypatch) -> None:
    from chip_relay.config import ROOMS_FILE

    monkeypatch.setattr("chip_relay.config.ROOMS_FILE", ROOMS)
    persona, err = resolve_selftest_persona(
        {
            "CHIP_RELAY_SELFTEST_AUTHOR_ID": "900000000000000001",
            "CHIP_RELAY_SELFTEST_ROOM": "dev",
        }
    )
    assert err is None
    assert persona == "pm"


def test_selftest_resolves_chip_selftest_bot_from_production_rooms() -> None:
    persona, err = resolve_selftest_persona(
        {
            "CHIP_RELAY_SELFTEST_AUTHOR_ID": "1553182793041838141",
            "CHIP_RELAY_SELFTEST_ROOM": "selftest",
        }
    )
    assert err is None
    assert persona == "selftest"


def test_selftest_requires_author_identity(monkeypatch) -> None:
    from chip_relay.config import ROOMS_FILE

    monkeypatch.setattr("chip_relay.config.ROOMS_FILE", ROOMS)
    persona, err = resolve_selftest_persona({"CHIP_RELAY_SELFTEST_ROOM": "dev"})
    assert persona == ""
    assert err is not None
    assert "CHIP_RELAY_SELFTEST_AUTHOR_ID" in err
