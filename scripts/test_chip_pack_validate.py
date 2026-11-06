"""A3: chip pack validate + fixture pack."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from chip import system_pack
from chip.cli import main
from chip.pack_validate import (
    KNOWN_SURFACES,
    PUBLIC_NO_LEAK_TOKENS,
    format_result,
    validate_pack,
    validate_pack_data,
)
from chip.paths import REPO_ROOT

FIXTURE_PACK = REPO_ROOT / "scripts" / "fixtures" / "packs" / "validate-ok"


def test_known_surfaces_cover_acme() -> None:
    pack = system_pack.load_pack("acme-example")
    missing = set(pack["surfaces"]) - KNOWN_SURFACES
    assert not missing, f"acme surfaces missing from KNOWN_SURFACES: {missing}"


def test_validate_acme_example_ok() -> None:
    result = validate_pack(pack="acme-example", root=REPO_ROOT)
    assert result.ok, format_result(result)
    assert result.name == "acme-example"
    assert len(result.surfaces_ok) == 18


def test_validate_fixture_pack_ok() -> None:
    result = validate_pack(pack_dir=FIXTURE_PACK, root=REPO_ROOT)
    assert result.ok, format_result(result)
    assert result.name == "validate-ok"
    assert set(result.surfaces_ok) == {"personas", "models", "skill_bodies_dir"}


def test_cli_pack_validate_fixture_exit_0() -> None:
    rc = main(["pack", "validate", "--pack-dir", str(FIXTURE_PACK)])
    assert rc == 0


def test_cli_pack_validate_acme_json() -> None:
    # Capture via validate_pack path exercised by CLI wiring smoke above;
    # JSON path uses the same Result.to_dict.
    result = validate_pack(pack="acme-example", root=REPO_ROOT)
    payload = json.loads(format_result(result, as_json=True))
    assert payload["ok"] is True
    assert payload["errors"] == 0
    assert "personas" in payload["surfaces_ok"]


def test_missing_surface_fails(tmp_path: Path) -> None:
    pack_dir = tmp_path / "broken-pack"
    pack_dir.mkdir()
    (pack_dir / "system.json").write_text(
        json.dumps(
            {
                "schema": "chip-system-pack/v0",
                "name": "broken-pack",
                "surfaces": {"personas": "missing.json"},
            }
        ),
        encoding="utf-8",
    )
    result = validate_pack(pack_dir=pack_dir, root=REPO_ROOT, check_no_leak=False)
    assert not result.ok
    assert any(i.code == "missing_surface" for i in result.issues)


def test_bad_schema_fails(tmp_path: Path) -> None:
    pack_dir = tmp_path / "bad-schema"
    pack_dir.mkdir()
    (pack_dir / "system.json").write_text(
        json.dumps({"schema": "nope", "name": "bad-schema", "surfaces": {"personas": "p.json"}}),
        encoding="utf-8",
    )
    (pack_dir / "p.json").write_text("{}", encoding="utf-8")
    result = validate_pack(pack_dir=pack_dir, check_no_leak=False)
    assert not result.ok
    assert any(i.code == "bad_schema" for i in result.issues)


def test_name_must_match_directory(tmp_path: Path) -> None:
    pack_dir = tmp_path / "dir-name"
    pack_dir.mkdir()
    (pack_dir / "system.json").write_text(
        json.dumps(
            {
                "schema": "chip-system-pack/v0",
                "name": "other-name",
                "surfaces": {"personas": "p.json"},
            }
        ),
        encoding="utf-8",
    )
    (pack_dir / "p.json").write_text("{}", encoding="utf-8")
    result = validate_pack(pack_dir=pack_dir, check_no_leak=False)
    assert not result.ok
    assert any(i.code == "name_mismatch" for i in result.issues)


def test_surface_path_escape_fails(tmp_path: Path) -> None:
    pack_dir = tmp_path / "escape-pack"
    pack_dir.mkdir()
    (pack_dir / "system.json").write_text(
        json.dumps(
            {
                "schema": "chip-system-pack/v0",
                "name": "escape-pack",
                "surfaces": {"personas": "../../etc/passwd"},
            }
        ),
        encoding="utf-8",
    )
    result = validate_pack(pack_dir=pack_dir, check_no_leak=False)
    assert not result.ok
    codes = {i.code for i in result.issues}
    assert "surface_escape" in codes or "missing_surface" in codes


def test_allowlist_must_be_string_list(tmp_path: Path) -> None:
    pack_dir = tmp_path / "allow-pack"
    pack_dir.mkdir()
    (pack_dir / "p.json").write_text("{}", encoding="utf-8")
    (pack_dir / "system.json").write_text(
        json.dumps(
            {
                "schema": "chip-system-pack/v0",
                "name": "allow-pack",
                "surfaces": {"personas": "p.json"},
                "allowlists": {"service": "not-a-list"},
            }
        ),
        encoding="utf-8",
    )
    result = validate_pack(pack_dir=pack_dir, check_no_leak=False)
    assert not result.ok
    assert any(i.code == "bad_list" for i in result.issues)


def test_allowlist_narrow_not_widen_runtime() -> None:
    """Document narrowing semantics: validate does not invent engine defaults;
    runtime allowlist() intersects. Pack declaring only fixture-api cannot gain
    engine-only services."""
    engine_defaults = frozenset({"payments-worker", "inventory-api", "fixture-api"})
    pack = {
        "schema": "chip-system-pack/v0",
        "name": "validate-ok",
        "allowlists": {"service": ["fixture-api", "invented-widen"]},
        "surfaces": {},
    }
    narrowed = system_pack.allowlist("service", engine_defaults, pack)
    assert narrowed == frozenset({"fixture-api"})
    assert "invented-widen" not in narrowed


def test_unknown_surface_warns(tmp_path: Path) -> None:
    pack_dir = tmp_path / "warn-pack"
    pack_dir.mkdir()
    (pack_dir / "extra.json").write_text("{}", encoding="utf-8")
    (pack_dir / "system.json").write_text(
        json.dumps(
            {
                "schema": "chip-system-pack/v0",
                "name": "warn-pack",
                "surfaces": {"future_surface": "extra.json"},
            }
        ),
        encoding="utf-8",
    )
    result = validate_pack(pack_dir=pack_dir, check_no_leak=False)
    assert result.ok
    assert any(i.code == "unknown_surface" and i.level == "warning" for i in result.issues)


def test_fixture_and_acme_no_leak() -> None:
    for target in (FIXTURE_PACK, REPO_ROOT / "systems" / "acme-example"):
        result = validate_pack(pack_dir=target, root=REPO_ROOT, check_no_leak=True)
        assert result.ok, format_result(result)
        blob = (target / "system.json").read_text(encoding="utf-8").lower()
        for token in PUBLIC_NO_LEAK_TOKENS:
            assert token not in blob, f"{target}: leaked {token}"


def test_chip_pack_dir_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_PACK_DIR", str(FIXTURE_PACK.resolve()))
    monkeypatch.delenv("CHIP_SYSTEM_PACK", raising=False)
    result = validate_pack(environ=os.environ, root=REPO_ROOT)
    assert result.ok, format_result(result)
    assert result.name == "validate-ok"


def test_cli_fails_on_broken_pack(tmp_path: Path) -> None:
    pack_dir = tmp_path / "cli-broken"
    pack_dir.mkdir()
    (pack_dir / "system.json").write_text(
        json.dumps({"schema": "chip-system-pack/v0", "name": "cli-broken", "surfaces": {}}),
        encoding="utf-8",
    )
    rc = main(["pack", "validate", "--pack-dir", str(pack_dir), "--json"])
    assert rc == 2
