"""Spec B mock tests T1–T4."""

from __future__ import annotations

import pytest

from chip import evidence, task


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "CHIP_TOOLS_MANIFEST",
        str(__import__("pathlib").Path(__file__).resolve().parent / "fixtures" / "chip_tools_test.json"),
    )
    return tmp_path


def test_t1_prose_not_evidence(data_dir) -> None:
    env = {
        "id": "task-x",
        "created_at": "2026-01-01T00:00:00+00:00",
        "acceptance": [{"id": "a1", "text": "ci green", "evidence_type": "url_regex"}],
        "evidence": [],
        "status": "open",
    }
    ok, reason = evidence.try_done(env)
    assert ok is False
    assert reason == "a1"
    assert env["status"] != "done"


def test_t2_url_regex_evidence_done(data_dir) -> None:
    url = "https://github.com/a1ly404/chip-chat/actions/runs/12345"
    env = task.create_task(
        "echo",
        args=[url],
        acceptance=[{"id": "ci", "text": "run url", "evidence_type": "url_regex"}],
    )
    done = task.run_task(env["id"], execute=True, timeout_seconds=5)
    assert done["status"] == "done"
    ok, msg = evidence.try_done(done)
    assert ok and msg == "Done"


def test_t3_never_auto_done_refuse(data_dir, monkeypatch) -> None:
    monkeypatch.setenv(
        "CHIP_TOOLS_MANIFEST",
        str(__import__("pathlib").Path(__file__).resolve().parent / "fixtures" / "chip_tools_test.json"),
    )
    env = task.create_task(
        "echo",
        args=["https://github.com/a1ly404/chip-chat/actions/runs/99"],
        acceptance=[{"id": "ci", "text": "x", "evidence_type": "url_regex"}],
        issue_key="homelab-media-cutover",
    )
    ran = task.run_task(env["id"], execute=True, timeout_seconds=5)
    ok, reason = evidence.try_done(ran)
    assert ok is False
    assert reason == "REFUSE:never_auto_done"


def test_t4_acceptance_immutable(data_dir) -> None:
    env = task.create_task(
        "echo",
        acceptance=[{"id": "a1", "text": "x", "evidence_type": "literal_in_tool_output"}],
    )
    with pytest.raises(ValueError, match="acceptance_immutable"):
        evidence.validate_acceptance_immutable(
            env,
            [{"id": "a1", "text": "changed", "evidence_type": "literal_in_tool_output"}],
        )
