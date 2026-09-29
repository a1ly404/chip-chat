"""Lane amendment GO test (RAZ-298-lane-amendment, ocgo-applied; expires midnight PT).

Contract: second pinned root for chipchatdev (razzorstack checkout) inherits the
same global denies — the extra root cannot reach /mnt/nas/media, the receipts
ledger, .env, or Infisical paths, and diagnose-only stays baked in the tool row.
"""

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_extra_root_inherits_global_denies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chip import config, lanes

    raz_root = tmp_path / "razzorstack"
    (raz_root / "scripts").mkdir(parents=True)
    chip_root = tmp_path / "chip"
    chip_root.mkdir()
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHIP_LANE_CHIP_ROOT", str(chip_root))
    monkeypatch.setenv("CHIP_LANE_RAZZORSTACK_ROOT", str(raz_root))

    # both roots resolvable; razzorstack is the extra lane-table row
    roots = lanes.persona_roots("chipchatdev")
    assert raz_root in roots and chip_root in roots

    # relative read inside the razzorstack root passes containment + deny check
    (raz_root / "scripts" / "airvpn_port_health.py").write_text("PROBE_OK\n", encoding="utf-8")
    assert lanes.read_text(persona="chipchatdev", relpath="scripts/airvpn_port_health.py").strip() == "PROBE_OK"

    # global hard deny #1: media path (above-root escape raises ValueError per lane contract)
    with pytest.raises(ValueError, match="escapes"):
        lanes.read_text(persona="chipchatdev", relpath="../media/movie.mkv")
    with pytest.raises(PermissionError):
        lanes.read_text(persona="chipchatdev", relpath="mnt/nas/media/movie.mkv")

    # global hard deny #2: the receipts ledger
    with pytest.raises(PermissionError):
        lanes.write_text(
            persona="chipchatdev",
            relpath="audit/tool_receipts.jsonl",
            content='{"tamper": true}\n',
            ticket_id="RAZ-298",
            room="dev",
            scope="rw-pass/lane-amendment",
        )

    # global hard deny #3: .env writes
    with pytest.raises(PermissionError):
        lanes.read_text(persona="chipchatdev", relpath="docker/.env")

    # go deny: infisical without a GO naming the ticket
    monkeypatch.delenv(config.GO_ENV if hasattr(config, "GO_ENV") else "CHIP_LANE_GO_TICKET", raising=False)
    with pytest.raises(PermissionError):
        lanes.read_text(persona="chipchatdev", relpath="infisical/token.txt")


def test_diagnose_only_is_baked_in_tool_row() -> None:
    """The widened lane exposes razzorstack scripts ONLY in baked diagnose-only mode."""
    spec = json.loads((REPO / "config/chip_tools.json").read_text())
    row = next(t for t in spec["tools"] if t["id"] == "razzorstack-diagnose")
    assert "--diagnose-only" in row["argv"]
    assert row["allow_extra_args"] is False  # no fix-mode args can be smuggled


def test_tier1_rows_baked_and_envelope_contracted() -> None:
    """GO: RAZ-335-tier1-plumbing — tier-1 executor rows exist, baked (no smuggling),
    refuse-and-page class untouched, every description carries the skill-envelope- +
    sub-scope-GO contract."""
    spec = json.loads((REPO / "config/chip_tools.json").read_text())
    by_id = {t["id"]: t for t in spec["tools"]}

    tier1 = {
        "gluetun-restart": ["docker", "restart", "gluetun"],
        "qb-restart": ["docker", "restart", "qbittorrent"],
        "airvpn-port-regab": ["python3", "scripts/airvpn_port_grab.py"],
        "fw-rule-reload": ["ufw", "reload"],
        "qb-port-update": ["python3", "scripts/qb_port_set.py"],
    }
    forbidden_tokens = ["delete", "rm ", "compose", "secret", "media"]
    for tid, argv in tier1.items():
        row = by_id[tid]
        assert row["argv"] == argv, (tid, row["argv"])
        assert row["allow_extra_args"] is False
        assert row["risk"] == "high"
        assert "TIER-1 (RAZ-335 only)" in row["description"]
        assert "skill envelope REQUIRED" in row["description"]
        assert "scope:RAZ-335-t1-" in row["description"]  # per-run named GO contract
        assert "Refuse-and-page class untouched" in row["description"]
        blob = " ".join(row["argv"]).lower()
        assert not any(f in blob for f in forbidden_tokens), f"{tid}: refuse-class leak in argv"
