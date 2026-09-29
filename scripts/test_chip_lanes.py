"""Lane pins, global-deny escalation, and receipt-before-write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chip import lanes
from chip import store


def _claim(persona: str, scope: str, room: str = "dev") -> None:
    path = store.thread_path(room)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"agent": persona, "content": f"claim: {persona} {scope}"}) + "\n")


@pytest.fixture()
def pins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CHIP_LANE_GO_TICKET", raising=False)
    monkeypatch.delenv("CHIP_LANE_RETRO_LIVE", raising=False)
    roots = {}
    for persona, env in (
        ("homelabdev", "CHIP_LANE_HOMELAB_ROOT"),
        ("opsdev", "CHIP_LANE_OPS_ROOT"),
        ("buffrdev", "CHIP_LANE_BUFFR_ROOT"),
        ("chipchatdev", "CHIP_LANE_CHIP_ROOT"),
    ):
        root = tmp_path / persona
        root.mkdir()
        monkeypatch.setenv(env, str(root))
        roots[persona] = root
    return roots


def test_missing_pin_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CHIP_LANE_HOMELAB_ROOT", raising=False)
    with pytest.raises(FileNotFoundError):
        lanes.read_text(persona="homelabdev", relpath="a.txt")


def test_write_then_read_roundtrip(pins: dict[str, Path]) -> None:
    _claim("opsdev", "raz-298")
    lanes.write_text(
        persona="opsdev",
        relpath="notes/a.txt",
        content="oracle\n",
        ticket_id="RAZ-298",
        room="dev",
        scope="raz-298",
    )
    assert lanes.read_text(persona="opsdev", relpath="notes/a.txt") == "oracle\n"
    lines = [
        json.loads(line)
        for line in lanes.receipt_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    writes = [row for row in lines if row["op"] == "write"]
    assert writes[0]["phase"] == "start"
    assert writes[1]["phase"] == "complete"
    assert writes[0]["sha256"]


def test_write_without_ticket_refused(pins: dict[str, Path]) -> None:
    _claim("chipchatdev", "scope-a")
    with pytest.raises(PermissionError):
        lanes.write_text(
            persona="chipchatdev",
            relpath="a.txt",
            content="x",
            ticket_id="",
            room="dev",
            scope="scope-a",
        )


def test_write_without_claim_refused(pins: dict[str, Path]) -> None:
    with pytest.raises(PermissionError, match="claim_owner"):
        lanes.write_text(
            persona="homelabdev",
            relpath="a.txt",
            content="x",
            ticket_id="RAZ-278",
            room="dev",
            scope="raz-278",
        )


def test_media_deny_beats_homelab_root(pins: dict[str, Path]) -> None:
    _claim("homelabdev", "raz-278")
    rel = Path("mnt/nas/media/movie.mkv")
    with pytest.raises(PermissionError, match="global deny"):
        lanes.write_text(
            persona="homelabdev",
            relpath=str(rel),
            content="nope",
            ticket_id="RAZ-278",
            room="dev",
            scope="raz-278",
        )
    assert not (pins["homelabdev"] / rel).exists()


def test_receipt_log_cannot_be_unlinked_via_write(pins: dict[str, Path]) -> None:
    _claim("buffrdev", "raz-1")
    with pytest.raises(PermissionError, match="tool_receipts.jsonl"):
        lanes.write_text(
            persona="buffrdev",
            relpath="audit/tool_receipts.jsonl",
            content="",
            ticket_id="RAZ-1",
            room="dev",
            scope="raz-1",
        )


def test_infisical_refused_without_go(pins: dict[str, Path]) -> None:
    _claim("opsdev", "raz-94")
    with pytest.raises(PermissionError, match="infisical"):
        lanes.write_text(
            persona="opsdev",
            relpath="infisical/token.txt",
            content="nope",
            ticket_id="RAZ-94",
            room="dev",
            scope="raz-94",
        )


def test_env_file_hard_deny(pins: dict[str, Path]) -> None:
    with pytest.raises(PermissionError, match="global deny"):
        lanes.read_text(persona="chipchatdev", relpath=".env")


def test_path_escape_refused(pins: dict[str, Path]) -> None:
    _claim("buffrdev", "raz-1b")
    with pytest.raises(ValueError):
        lanes.write_text(
            persona="buffrdev",
            relpath="../outside.txt",
            content="x",
            ticket_id="RAZ-1",
            room="dev",
            scope="raz-1b",
        )


def test_live_retro_records_refusal(pins: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_LANE_RETRO_LIVE", "1")
    _claim("homelabdev", "raz-278b")
    with pytest.raises(PermissionError):
        lanes.write_text(
            persona="homelabdev",
            relpath="mnt/nas/media/x.mkv",
            content="x",
            ticket_id="RAZ-278",
            room="dev",
            scope="raz-278b",
        )
    retro = lanes.retro_path().read_text(encoding="utf-8")
    assert "global deny" in retro
    assert "RAZ-278" in retro
