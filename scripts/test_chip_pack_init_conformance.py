"""Engine pack slice: foreign-pack root option, surface classes, init, conformance (Refs RAZ-16).

Installed-engine use from an unrelated working directory is simulated with
``--repo-root`` / ``validate_pack(root=...)`` pointed at a fake client repo —
the way a pip-installed engine validates a private client pack.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chip import pack_validate as pv
from chip.cli import main
from chip.pack_validate import (
    EXTENSION_SURFACES,
    KNOWN_SURFACES,
    conformance_check,
    init_pack,
    validate_pack,
)
from chip.paths import REPO_ROOT


@pytest.fixture()
def client_repo(tmp_path: Path) -> Path:
    """A fictional client repo the engine is NOT installed inside."""
    root = tmp_path / "fictional-client"
    (root / "systems" / "other").mkdir(parents=True)
    (root / "systems" / "other" / "data.json").write_text("{}", encoding="utf-8")
    (root / "docs").mkdir()
    pack = root / "systems" / "my-pack"
    pack.mkdir(parents=True)
    (pack / "system.json").write_text(
        json.dumps(
            {
                "schema": "chip-system-pack/v0",
                "name": "my-pack",
                "surfaces": {
                    "personas": "config/personas.json",  # pack-relative
                    "case_taxonomy": "systems/other/data.json",  # repo-root-anchored
                    "bot_alert_rules": "config/bot-alert-rules.json",  # client extension
                    "event_cases": "docs/event-cases.json",  # engine-promoted (repo-anchored)
                },
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    (pack / "config").mkdir()
    (pack / "config" / "personas.json").write_text("{}", encoding="utf-8")
    (pack / "config" / "bot-alert-rules.json").write_text("{}", encoding="utf-8")
    (root / "docs" / "event-cases.json").write_text("[]", encoding="utf-8")
    return root


def test_known_and_extension_sets() -> None:
    assert "event_cases" in KNOWN_SURFACES  # engine owns chip_relay.event_cases
    assert EXTENSION_SURFACES.isdisjoint(KNOWN_SURFACES)
    assert "bot_alert_rules" in EXTENSION_SURFACES


def test_foreign_pack_needs_repo_root(client_repo: Path) -> None:
    """Repo-anchored surfaces of an out-of-tree pack resolve against --repo-root."""
    pack_dir = client_repo / "systems" / "my-pack"
    result = validate_pack(pack_dir=pack_dir, root=client_repo)
    assert result.ok, pv.format_result(result)
    assert set(result.surfaces_ok) == {"personas", "case_taxonomy", "bot_alert_rules", "event_cases"}
    # The surface classes behave as documented: extension warns, promoted generic does not.
    codes = {i.code for i in result.issues}
    assert "extension_surface" in codes or any(
        i.code == "extension_surface" for i in result.issues
    )
    assert not any(i.code == "unknown_surface" for i in result.issues)
    assert not any(i.code == "extension_surface" and "event_cases" in i.message for i in result.issues)


def test_foreign_pack_without_root_fails_closed(client_repo: Path) -> None:
    """Default resolution points at the ENGINE's own repo → repo-anchored surfaces miss."""
    pack_dir = client_repo / "systems" / "my-pack"
    result = validate_pack(pack_dir=pack_dir)  # root=None → engine REPO_ROOT (not client_repo)
    assert not result.ok
    assert any(i.code == "missing_surface" for i in result.issues)


def test_cli_repo_root_option(client_repo: Path) -> None:
    rc = main(["pack", "validate", "--pack-dir", str(client_repo / "systems" / "my-pack"),
               "--repo-root", str(client_repo)])
    assert rc == 0


def test_cli_repo_root_option_fails_without(client_repo: Path, capsys) -> None:
    rc = main(["pack", "validate", "--pack-dir", str(client_repo / "systems" / "my-pack")])
    assert rc == 2
    assert "missing_surface" in capsys.readouterr().out


def test_malformed_pack_reports_not_crashes(tmp_path: Path, capsys) -> None:
    (tmp_path / "system.json").write_text("{not json", encoding="utf-8")
    rc = main(["pack", "validate", "--pack-dir", str(tmp_path)])
    assert rc == 2
    assert capsys.readouterr().out  # a receipt-shaped FAIL line, not a traceback


def test_installed_engine_smoke_via_module_only(tmp_path: Path) -> None:
    """An installed engine (no checkout) validates packs from an unrelated cwd."""
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    before = Path.cwd()
    import os

    try:
        os.chdir(cwd)
        result = validate_pack(pack_dir=REPO_ROOT / "systems" / "acme-example", root=REPO_ROOT)
        assert result.ok and result.name == "acme-example"
    finally:
        os.chdir(before)


# ---------------------------------------------------------------- init + conformance


def test_init_creates_fictional_scaffold(tmp_path: Path) -> None:
    target = tmp_path / "systems" / "fresh-pack"
    created = init_pack(target)
    assert created and all(p.is_file() for p in created)
    manifest = json.loads((target / "system.json").read_text(encoding="utf-8"))
    # Fictional content only; the directory name drives identity.
    assert manifest["name"] == "fresh-pack"
    assert manifest["schema"] == "chip-system-pack/v0"
    assert "homelab" not in json.dumps(manifest)  # fictional scaffold stays generic
    # The scaffold must itself validate clean from the engine root.
    result = validate_pack(pack_dir=target, root=REPO_ROOT, check_no_leak=True)
    assert result.ok, pv.format_result(result)


def test_init_refuses_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "existing-pack"
    target.mkdir()
    (target / "system.json").write_text('{"schema": "chip-system-pack/v0", "name": "existing-pack", "surfaces": {"personas": "p.json"}}', encoding="utf-8")
    with pytest.raises(FileExistsError):
        init_pack(target)
    # --force is the explicit escape hatch.
    created = init_pack(target, force=True)
    assert target / "system.json" in created
    assert json.loads((target / "system.json").read_text(encoding="utf-8"))["name"] == "existing-pack"


def test_cli_init_refusal_exit_2(tmp_path: Path, capsys) -> None:
    target = tmp_path / "pack"
    init_pack(target)
    rc = main(["pack", "init", "--pack-dir", str(target)])
    assert rc == 2
    assert "overwrite" in capsys.readouterr().err


def test_conformance_promotes_extension_warning(client_repo: Path) -> None:
    pack_dir = client_repo / "systems" / "my-pack"
    sane = validate_pack(pack_dir=pack_dir, root=client_repo)
    assert sane.ok
    strict = conformance_check(pack_dir=pack_dir, root=client_repo)
    assert not strict.ok  # extension surface stays an error in conformance
    assert any(i.code == "unconforming_surface" for i in strict.issues)


def test_conformance_passes_clean_pack() -> None:
    result = conformance_check(pack="acme-example", root=REPO_ROOT)
    assert result.ok, pv.format_result(result)
