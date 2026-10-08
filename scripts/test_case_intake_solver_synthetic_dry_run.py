"""Synthetic cases must not invoke subprocess/docker mutations in solver actions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chip import pack_values
from chip_relay.case_intake_solver_actions import RESTART_ALLOWLIST_ENV, docker_inspect, docker_restart, fix_script


def _case_path(tmp_path: Path) -> Path:
    path = tmp_path / "case.json"
    path.write_text(
        json.dumps(
            {
                "id": "synthetic-smoke-1",
                "synthetic": True,
                "description": "container example-bot restart warranted",
                "repro_context": "container=example-bot",
                "evidence_path": "synthetic://smoke",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_synthetic_allowlisted_restart_produces_zero_runner_calls(monkeypatch, tmp_path) -> None:
    case_path = _case_path(tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return type("P", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr("chip_relay.case_intake_solver_actions.subprocess.run", fake_run)
    allow = sorted(pack_values.service_allowlist())
    assert allow, "active pack must declare service allowlist"
    container = allow[0]
    row = docker_restart(
        container,
        environ={RESTART_ALLOWLIST_ENV: container},
        case_id="synthetic-smoke-1",
        case_path=case_path,
    )
    assert calls == []
    assert row["phase"] == "pre-dry-run"
    assert row.get("reason") == "synthetic-case-no-mutations"


def test_synthetic_fix_script_and_inspect_dry_run(monkeypatch, tmp_path) -> None:
    case_path = _case_path(tmp_path)
    monkeypatch.setenv("CASE_INTAKE_SOLVER_FIX_SCRIPTS", "noop.py")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "chip_relay.case_intake_solver_actions.subprocess.run",
        lambda cmd, **k: calls.append(list(cmd)),
    )
    r1 = fix_script("noop.py", environ={"CASE_INTAKE_SOLVER_FIX_SCRIPTS": "noop.py"}, case_id="x", case_path=case_path)
    r2 = docker_inspect("example-bot", case_id="x", case_path=case_path)
    assert calls == []
    assert r1["phase"] == r2["phase"] == "pre-dry-run"
