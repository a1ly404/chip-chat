"""Tool executor authorize-wiring tests (round-7: single gate into tool.run)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from chip import tool as tool_mod

REPO = Path(__file__).resolve().parents[1]
FIXTURE_MANIFEST = REPO / "scripts" / "fixtures" / "chip_tools_test.json"


@pytest.fixture()
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHIP_TOOLS_MANIFEST", str(FIXTURE_MANIFEST))
    monkeypatch.delenv("CHIP_TOOL_AUTO", raising=False)
    monkeypatch.delenv("CHIP_TOOL_STRICT", raising=False)
    monkeypatch.delenv("CHIP_STAND_DOWN", raising=False)
    return tmp_path


def _write_claim(data_dir: Path, agent: str, scope: str) -> None:
    threads = data_dir / "threads"
    threads.mkdir(parents=True, exist_ok=True)
    with (threads / "dev.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"agent": agent, "content": f"claim: {agent} {scope}"}) + "\n")


def _audit_lines(data_dir: Path) -> list[dict]:
    path = data_dir / "audit" / "tool_invocations.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# legacy bridge (speaker-less callers stay working; strict flips them)
# ---------------------------------------------------------------------------


def test_execute_without_speaker_still_runs(data_dir: Path) -> None:
    result = tool_mod.run("echo", "chip", "ok", execute=True, timeout_seconds=5)
    assert result.executed is True
    assert result.exit_code == 0


def test_strict_refuses_anonymous_execute(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_TOOL_STRICT", "1")
    result = tool_mod.run("echo", "chip", "ok", execute=True, timeout_seconds=5)
    assert result.executed is False
    assert "no authenticated speaker" in result.stderr
    assert "authz_refused" in _audit_lines(data_dir)[-1]


# ---------------------------------------------------------------------------
# persona path (speaker provided → always gated)
# ---------------------------------------------------------------------------


def test_speaker_without_claim_refused(data_dir: Path) -> None:
    result = tool_mod.run(
        "echo", "hi", execute=True, speaker="chipchatdev", room="dev", scope="scope-a"
    )
    assert result.executed is False
    assert "claim_owner=none" in result.stderr
    assert _audit_lines(data_dir)[-1]["authz_refused"]


def test_claimed_speaker_executes(data_dir: Path) -> None:
    _write_claim(data_dir, "chipchatdev", "scope-b")
    result = tool_mod.run(
        "echo", "ok", execute=True, speaker="chipchatdev", room="dev", scope="scope-b"
    )
    assert result.executed is True
    assert result.exit_code == 0


def test_other_persona_cannot_use_someone_elses_claim(data_dir: Path) -> None:
    _write_claim(data_dir, "chipchatdev", "scope-c")
    result = tool_mod.run(
        "echo", "ok", execute=True, speaker="opsdev", room="dev", scope="scope-c"
    )
    assert result.executed is False
    assert "claim_owner=chipchatdev" in result.stderr


def test_blocked_target_in_scope_refused(data_dir: Path) -> None:
    _write_claim(data_dir, "chipchatdev", "maintenance-plex")
    result = tool_mod.run(
        "echo", "ok", execute=True, speaker="chipchatdev", room="dev", scope="maintenance-plex"
    )
    assert result.executed is False
    assert "blocked target" in result.stderr


def test_stand_down_freezes_persona_execute(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_claim(data_dir, "chipchatdev", "scope-d")
    monkeypatch.setenv("CHIP_STAND_DOWN", "1")
    result = tool_mod.run(
        "echo", "ok", execute=True, speaker="chipchatdev", room="dev", scope="scope-d"
    )
    assert result.executed is False
    assert "stand-down" in result.stderr


def test_human_inject_hot_path_kept_hot_even_under_stand_down(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIP_STAND_DOWN", "1")
    result = tool_mod.run("echo", "hot", execute=True, is_human_or_inject=True)
    assert result.executed is True
    assert result.exit_code == 0


def test_run_or_raise_surfaces_refusal(data_dir: Path) -> None:
    with pytest.raises(tool_mod.ToolGatedError) as excinfo:
        tool_mod.run_or_raise(
            "echo", "nope", execute=True, speaker="chipchatdev", room="dev", scope="scope-e"
        )
    assert "claim_owner=none" in str(excinfo.value)


def test_run_or_raise_executes_under_live_claim(data_dir: Path) -> None:
    _write_claim(data_dir, "chipchatdev", "scope-f")
    result = tool_mod.run_or_raise(
        "echo", "ok", execute=True, speaker="chipchatdev", room="dev", scope="scope-f"
    )
    assert result.executed is True
