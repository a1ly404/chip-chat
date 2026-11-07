"""Engine parity plan slice H: typed host-exec protocol (Refs RAZ-16).

Offline: the engine adapter is dry-run only — no hostnames, no subprocesses,
no service-specific behavior; real adapters are plugin-side per
``docs/host-hooks.md``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from chip import host_exec
from chip.host_exec import (
    DENY_EXIT,
    GUARD_EXIT,
    RECEIPTS_DIR_ENV,
    ManifestAdapter,
    host_exec_enabled,
)
from chip.paths import REPO_ROOT
import yaml


@pytest.fixture()
def manifest() -> dict:
    path = REPO_ROOT / "plugins" / "example" / "host-hooks.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict) and doc.get("hooks")
    return doc


def _receipts(tmp_path: Path) -> str:
    # A generic receipts path; no host identifiers.
    return str(tmp_path / "hook-receipts")


def test_flag_default_off_and_truthy() -> None:
    assert host_exec_enabled({}) is False
    assert host_exec_enabled({"CHIP_HOST_EXEC": "0"}) is False
    assert host_exec_enabled({"CHIP_HOST_EXEC": "on"}) is True


def test_manifest_adapter_allowlist_is_explicit(manifest, tmp_path) -> None:
    adapter = ManifestAdapter(manifest, environ={RECEIPTS_DIR_ENV: _receipts(tmp_path)})
    allowed = adapter.allowed_hooks()
    assert "host-watchdog" in allowed and "host-repair-docker" in allowed
    assert "invented-hook" not in allowed


def test_deny_is_explicit_and_receipt_free(manifest, tmp_path) -> None:
    receipts = Path(_receipts(tmp_path))
    result = ManifestAdapter(manifest, environ={RECEIPTS_DIR_ENV: _receipts(tmp_path)}, receipts_dir=receipts).run("invented-hook")
    assert result.exit == DENY_EXIT
    assert "not declared in manifest" in result.detail
    assert result.receipt_path is None
    assert not receipts.exists() or not any(receipts.glob("invented-hook.jsonl"))


def test_guard_rule_refuses_may_restart_hooks(manifest, tmp_path, monkeypatch) -> None:
    receipts = Path(_receipts(tmp_path))
    env = {RECEIPTS_DIR_ENV: _receipts(tmp_path), "CHIP_HOST_LOCK_FILE": str(tmp_path / "lock")}
    monkeypatch.setitem(dict(env), "CHIP_HOST_LOCK_FILE", env["CHIP_HOST_LOCK_FILE"])
    (tmp_path / "lock").write_text("maintenance window", encoding="utf-8")
    adapter = ManifestAdapter(manifest, environ=env, receipts_dir=receipts)
    result = adapter.run("host-restart-stack")  # may_restart: true
    assert result.exit == GUARD_EXIT
    assert "lock file" in result.detail
    entry = json.loads(Path(result.receipt_path).read_text(encoding="utf-8").splitlines()[-1])
    assert entry["exit"] == GUARD_EXIT and entry["hook"] == "host-restart-stack"


def test_read_only_hooks_are_dry_run_with_receipts(manifest, tmp_path) -> None:
    receipts = Path(_receipts(tmp_path))
    adapter = ManifestAdapter(manifest, environ={RECEIPTS_DIR_ENV: _receipts(tmp_path)}, receipts_dir=receipts)
    result = adapter.run("host-watchdog")
    assert result.exit == 0 and "dry-run" in result.detail
    entry = json.loads(Path(result.receipt_path).read_text(encoding="utf-8").splitlines()[-1])
    assert entry["hook"] == "host-watchdog" and "detail" in entry


def test_receipt_line_shape_matches_contract(manifest, tmp_path) -> None:
    receipts = Path(_receipts(tmp_path))
    adapter = ManifestAdapter(manifest, environ={RECEIPTS_DIR_ENV: _receipts(tmp_path)}, receipts_dir=receipts)
    adapter.run("host-watchdog")
    line = (receipts / "host-watchdog.jsonl").read_text(encoding="utf-8").splitlines()[0]
    entry = json.loads(line)
    assert set(entry) == {"ts", "hook", "exit", "detail"}  # contract shape, no secrets keys


def test_host_exec_protocol_shape(manifest, tmp_path) -> None:
    from chip.host_exec import HostAdapter

    adapter = ManifestAdapter(manifest, environ={RECEIPTS_DIR_ENV: _receipts(tmp_path)}, receipts_dir=Path(_receipts(tmp_path)))
    assert isinstance(adapter, HostAdapter)  # runtime_checkable protocol: seam implemented


def test_missing_receipts_config_is_a_usage_error(manifest) -> None:
    with pytest.raises(ValueError, match="CHIP_HOST_RECEIPTS_DIR"):
        ManifestAdapter(manifest, environ={}, receipts_dir=None).run("host-watchdog")
