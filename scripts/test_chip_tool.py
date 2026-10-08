from __future__ import annotations
"""Offline tests for allowlisted tool execution and task envelopes."""


import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from chip import task as task_mod
from chip import tool as tool_mod

REPO = Path(__file__).resolve().parents[1]
FIXTURE_MANIFEST = REPO / "scripts" / "fixtures" / "chip_tools_test.json"


@pytest.fixture()
def data_dir(monkeypatch: pytest.MonkeyPatch) -> Path:
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp))
    monkeypatch.setenv("CHIP_TOOLS_MANIFEST", str(FIXTURE_MANIFEST))
    monkeypatch.delenv("CHIP_TOOL_AUTO", raising=False)
    return tmp


def test_tool_list_cli(data_dir: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "scripts")
    env["CHIP_CHAT_DATA_DIR"] = str(data_dir)
    env["CHIP_TOOLS_MANIFEST"] = str(FIXTURE_MANIFEST)
    proc = subprocess.run(
        [sys.executable, "-m", "chip", "tool", "list", "--json"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    rows = json.loads(proc.stdout)
    ids = {r["id"] for r in rows}
    assert "echo" in ids
    assert "git-status" in ids


def test_gated_run_without_execute(data_dir: Path) -> None:
    result = tool_mod.run("echo", "hello", execute=False)
    assert result.executed is False
    assert result.exit_code == -1
    audit = (data_dir / "audit" / "tool_invocations.jsonl").read_text(encoding="utf-8")
    assert "gated" in audit
    assert "echo" in audit


def test_execute_echo(data_dir: Path) -> None:
    result = tool_mod.run("echo", "chip", "ok", execute=True, timeout_seconds=5)
    assert result.executed is True
    assert result.exit_code == 0
    assert "chip ok" in result.stdout
    lines = (data_dir / "audit" / "tool_invocations.jsonl").read_text(encoding="utf-8").strip().splitlines()
    last = json.loads(lines[-1])
    assert last["exit_code"] == 0
    assert last["executed"] is True


def test_timeout_kills_and_audits(data_dir: Path) -> None:
    result = tool_mod.run("sleepy", execute=True, timeout_seconds=0.2)
    assert result.executed is True
    assert result.timed_out is True
    assert result.exit_code == -9
    assert "timed out" in result.stderr.lower()
    lines = (data_dir / "audit" / "tool_invocations.jsonl").read_text(encoding="utf-8").strip().splitlines()
    last = json.loads(lines[-1])
    assert last["timed_out"] is True


def test_disallow_extra_args(data_dir: Path) -> None:
    with pytest.raises(tool_mod.ToolNotAllowedError):
        tool_mod.run("git-status", "--porcelain", execute=True)


def test_task_envelope_run_gated_and_execute(data_dir: Path) -> None:
    env = task_mod.create_task("echo", args=["from-task"])
    task_id = env["id"]
    assert env["tool_invocations"] == []
    assert env["execution_result"] is None

    pending = task_mod.run_task(task_id, execute=False)
    assert pending["status"] == "open"
    assert pending["execution_result"]["gated"] is True
    assert len(pending["tool_invocations"]) == 1
    assert pending["tool_invocations"][0]["executed"] is False

    done = task_mod.run_task(task_id, execute=True, timeout_seconds=5)
    assert done["status"] == "open"
    env2 = task_mod.create_task(
        "echo",
        args=["chip", "ok"],
        acceptance=[
            {
                "id": "echoed",
                "text": "chip ok",
                "evidence_type": "literal_in_tool_output",
            }
        ],
    )
    finished = task_mod.run_task(env2["id"], execute=True, timeout_seconds=5)
    assert finished["status"] == "done"
    assert finished["execution_result"]["ok"] is True
    assert len(finished["tool_invocations"]) == 1
    assert finished["tool_invocations"][-1]["exit_code"] == 0


def test_task_show_cli(data_dir: Path) -> None:
    env = task_mod.create_task("echo", args=["x"])
    task_id = env["id"]
    shell_env = os.environ.copy()
    shell_env["PYTHONPATH"] = str(REPO / "scripts")
    shell_env["CHIP_CHAT_DATA_DIR"] = str(data_dir)
    shell_env["CHIP_TOOLS_MANIFEST"] = str(FIXTURE_MANIFEST)
    proc = subprocess.run(
        [sys.executable, "-m", "chip", "task", "show", task_id, "--json"],
        cwd=REPO,
        env=shell_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    shown = json.loads(proc.stdout)
    assert shown["id"] == task_id
    assert shown["tool"] == "echo"
    assert "tool_invocations" in shown


def test_git_status_allowlisted_readonly(data_dir: Path) -> None:
    git_check = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if git_check.returncode != 0:
        pytest.skip("git-status smoke requires a git checkout")
    result = tool_mod.run("git-status", execute=True, cwd=REPO, timeout_seconds=10)
    assert result.executed is True
    assert result.exit_code == 0
    assert "main" in result.stdout or "##" in result.stdout
