"""pm persona tools: pytest and git add/commit inside the lane root."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from chip import store
from chip import tool
from chip.need_tool import NeedToolError


def _claim(persona: str, scope: str) -> None:
    path = store.thread_path("dev")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"agent": persona, "content": f"claim: {persona} {scope}"}) + "\n")


@pytest.fixture()
def pm_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "pm"
    root.mkdir()
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHIP_LANE_PM_ROOT", str(root))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "pm")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "pm@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "pm")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "pm@example.invalid")
    _claim("pm", "raz-pm-tools")
    return root


def test_pm_pytest_and_git_inside_root(pm_root: Path) -> None:
    ran = tool.run(
        "pytest",
        "--collect-only",
        "-q",
        execute=True,
        speaker="pm",
        room="dev",
        scope="raz-pm-tools",
        cwd=pm_root,
    )
    assert ran.executed is True
    assert ran.argv[:3] == ["python3", "-m", "pytest"]
    repo = pm_root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "note.txt").write_text("x", encoding="utf-8")
    added = tool.run(
        "git-add",
        "note.txt",
        execute=True,
        speaker="pm",
        room="dev",
        scope="raz-pm-tools",
        cwd=repo,
    )
    assert added.executed is True and added.exit_code == 0
    committed = tool.run(
        "git-commit",
        "note",
        execute=True,
        speaker="pm",
        room="dev",
        scope="raz-pm-tools",
        cwd=repo,
    )
    assert committed.executed is True and committed.exit_code == 0


def test_pm_refuses_push_shell_and_media(pm_root: Path, tmp_path: Path) -> None:
    with pytest.raises(tool.ToolNotAllowedError):
        tool.run("git-push", execute=True, speaker="pm", room="dev", scope="raz-pm-tools", cwd=pm_root)
    with pytest.raises(tool.ToolNotAllowedError):
        tool.run("shell", execute=True, speaker="pm", room="dev", scope="raz-pm-tools", cwd=pm_root)
    outside = tmp_path / "out"
    outside.mkdir()
    (outside / "x.txt").write_text("x", encoding="utf-8")
    with pytest.raises(tool.ToolNotAllowedError):
        tool.run(
            "git-add",
            str(outside / "x.txt"),
            execute=True,
            speaker="pm",
            room="dev",
            scope="raz-pm-tools",
            cwd=pm_root,
        )
    with pytest.raises(tool.ToolGatedError, match="media"):
        tool.run(
            "pytest",
            execute=True,
            speaker="pm",
            room="dev",
            scope="raz-pm-tools",
            cwd="/mnt/nas/media",
        )


def test_unknown_tool_still_need_tool_for_other_personas() -> None:
    with pytest.raises(NeedToolError):
        tool.run("git-add", execute=False)


def test_pm_cannot_run_tier1_infra(pm_root: Path) -> None:
    import json
    from chip.config import REPO_ROOT

    raw = json.loads((REPO_ROOT / "config" / "chip_tools.json").read_text(encoding="utf-8"))
    tier1 = [
        row["id"]
        for row in raw["tools"]
        if row.get("audience") == "global-only"
    ]
    assert set(tier1) == {
        "gluetun-restart",
        "qb-restart",
        "airvpn-port-regab",
        "fw-rule-reload",
        "qb-port-update",
    }
    for tool_id in tier1:
        with pytest.raises(tool.ToolNotAllowedError):
            tool.run(
                tool_id,
                execute=True,
                speaker="pm",
                room="dev",
                scope="raz-pm-tools",
                cwd=pm_root,
            )
