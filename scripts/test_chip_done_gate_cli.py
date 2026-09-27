"""CLI done-gate tests (RAZ-182/217 machine-checkable gating, offline)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from chip import cli as cli_mod

GOOD = """## Acceptance

- [x] probe 200
verified-as-of: 2026-09-27 03:10 — container ok
"""
BAD = """## Acceptance

- [x] probe 200
(no receipt line bound)
"""


def _fake_stdin(text: str):
    class _Stdin:
        def read(self) -> str:
            return text

    return _Stdin()


def test_gate_allow_and_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _fake_stdin(GOOD))
    rc = cli_mod.main(["done-gate", "--declare-done", "--json"])
    assert rc == 0

    monkeypatch.setattr(sys, "stdin", _fake_stdin(BAD))
    rc = cli_mod.main(["done-gate", "--declare-done", "--json"])
    assert rc == 2


def test_file_mode_refusal_exit_code(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.md"
    bad_file.write_text(BAD, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "chip", "done-gate", "--file", str(bad_file)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "REFUSE" in proc.stdout and "verified-as-of" in proc.stdout


def test_file_mode_allow_json(tmp_path: Path) -> None:
    good_file = tmp_path / "good.md"
    good_file.write_text(GOOD, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "chip", "done-gate", "--file", str(good_file), "--json"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["allow_done"] is True
    assert payload["reason"].startswith("all boxes verified")


def test_missing_file_errors(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "chip", "done-gate", "--file", str(tmp_path / "nope.md")],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "file not found" in proc.stderr
