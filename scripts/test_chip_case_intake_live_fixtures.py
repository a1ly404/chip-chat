"""Regression loader for live-captured intake fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from chip.config import REPO_ROOT

FIXTURE_DIR = REPO_ROOT / "config" / "fixtures" / "case_intake_live"


def _fixtures() -> list[dict]:
    index = FIXTURE_DIR / "index.json"
    if not index.is_file():
        return []
    rows = json.loads(index.read_text(encoding="utf-8"))
    out: list[dict] = []
    for row in rows:
        path = REPO_ROOT / row["path"]
        if path.is_file():
            out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def test_live_fixtures_index_valid() -> None:
    for fx in _fixtures():
        assert fx.get("raw_input")
        assert fx.get("outcome")
