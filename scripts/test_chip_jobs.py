from __future__ import annotations
"""PM orchestrator job board."""


import json

import pytest

from chip import jobs, store


def test_create_and_load_job(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    doc = jobs.create_job(
        "dev",
        owner_persona="pm",
        goal="ship orchestrator slice",
        acceptance="pytest green",
        stop="one PR",
        job_id="smoke01",
    )
    assert doc["id"] == "smoke01"
    assert doc["status"] == "open"
    loaded = jobs.load_job("dev", "smoke01")
    assert loaded["goal"] == doc["goal"]
    path = tmp_path / "jobs" / "dev" / "smoke01.json"
    assert path.is_file()


def test_mirror_job_line_in_transcript(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    jobs.create_job(
        "dev",
        owner_persona="pm",
        goal="g",
        acceptance="a",
        stop="s",
        job_id="j1",
    )
    lines = store.thread_path("dev").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    rec = json.loads(lines[-1])
    assert rec.get("pm_job", {}).get("id") == "j1"


def test_cli_job_create_smoke(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    from chip.cli import main

    rc = main(
        [
            "job",
            "create",
            "dev",
            "--goal",
            "mock",
            "--acceptance",
            "ok",
            "--stop",
            "stop",
            "--json",
        ]
    )
    assert rc == 0
