"""Offline tests for Chip Chat CLI (no OpenRouter key required)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "bin" / "chip"


def _run_chip(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged["PYTHONPATH"] = str(REPO / "scripts")
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-m", "chip", *args],
        cwd=REPO,
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )


def test_ping_fails_without_key() -> None:
    proc = _run_chip(
        "ping",
        env={
            "OPENROUTER_CC_API_KEY": "",
            "CHIP_CHAT_MOCK": "",
            "OPENROUTER_OWUI_API_KEY": "sk-or-v1-should-not-be-used",
        },
    )
    assert proc.returncode != 0
    assert "OPENROUTER_CC_API_KEY" in proc.stderr


def test_ping_fails_bad_key_shape() -> None:
    proc = _run_chip("ping", env={"OPENROUTER_CC_API_KEY": "not-a-real-key"})
    assert proc.returncode == 2
    assert "sk-or-" in proc.stderr


def test_ping_mock_ok() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_chip(
            "ping",
            env={
                "CHIP_CHAT_MOCK": "1",
                "CHIP_CHAT_DATA_DIR": tmp,
            },
        )
    assert proc.returncode == 0
    assert "OK" in proc.stdout
    assert "model=" in proc.stdout
    assert "usage_monthly=" in proc.stdout
    assert "z-ai/glm-5.3-flash" in proc.stdout


def test_room_demo_mock_handoff() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_chip(
            "room",
            "demo",
            "--demo",
            "--mock",
            env={"CHIP_CHAT_DATA_DIR": tmp},
        )
        assert proc.returncode == 0
        assert "planner>" in proc.stdout.lower() or "Planner" in proc.stdout
        assert "builder>" in proc.stdout.lower() or "Builder" in proc.stdout
        log = Path(tmp) / "threads" / "demo.jsonl"
        assert log.is_file()
        lines = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 2


def test_agent_spawn_and_say_mock() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        spawn = _run_chip(
            "agent",
            "spawn",
            "tester",
            "--persona",
            "planner",
            env={"CHIP_CHAT_DATA_DIR": tmp, "CHIP_CHAT_MOCK": "1"},
        )
        assert spawn.returncode == 0
        say = _run_chip(
            "agent",
            "say",
            "tester",
            "hello",
            env={"CHIP_CHAT_DATA_DIR": tmp, "CHIP_CHAT_MOCK": "1"},
        )
    assert say.returncode == 0
    assert "tester>" in say.stdout


def test_chat_once_mock() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_chip(
            "chat",
            "--once",
            "Reply with exactly: pong",
            "--mock",
            env={"CHIP_CHAT_DATA_DIR": tmp},
        )
        assert proc.returncode == 0
        assert "pong" in proc.stdout.lower() or "mock" in proc.stdout.lower()
        assert "monthly_usd=" in proc.stdout


def test_spend_command_mock_no_key() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_chip("spend", env={"CHIP_CHAT_DATA_DIR": tmp, "OPENROUTER_CC_API_KEY": ""})
    assert proc.returncode == 0
    assert "thresholds warn=$5.00 cutoff=$10.00" in proc.stdout


def test_blocked_model_rejected() -> None:
    proc = _run_chip(
        "chat",
        "--once",
        "hi",
        "--model",
        "deepseek/deepseek-chat",
        "--mock",
    )
    assert proc.returncode == 2
    assert "blocked" in proc.stderr.lower()


def test_spend_warn_emits_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import spend

    monkeypatch.setattr(spend, "WARN_MONTHLY_USD", 5.0)
    monkeypatch.setattr(spend, "CUTOFF_MONTHLY_USD", 10.0)
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    ledger = spend.default_ledger()
    ledger["monthly_usd"] = 5.5
    ledger["monthly_source"] = "api"
    spend.save_ledger(ledger)

    def fake_auth(_key: str) -> tuple[dict, float | None]:
        return ledger, 5.5

    monkeypatch.setattr(spend, "refresh_from_auth", fake_auth)
    monkeypatch.setattr(spend.openrouter, "chat_completion", lambda *a, **k: {"choices": [], "usage": {}})

    import contextlib
    import io

    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        spend.guard_before_live_completion("test", "sk-or-v1-unit-test-key")
    assert "WARN" in stderr.getvalue()
    assert (tmp_path / "standup_facts.jsonl").is_file()


def test_spend_cutoff_blocks_live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from chip import spend
    from chip.openrouter import ChipConfigError

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))

    def fake_refresh(_key: str) -> tuple[dict, float | None]:
        led = spend.default_ledger()
        led["monthly_usd"] = 10.0
        return led, 10.0

    monkeypatch.setattr(spend, "refresh_from_auth", fake_refresh)
    with pytest.raises(ChipConfigError) as exc:
        spend.guard_before_live_completion("ping", "sk-or-v1-unit-test-key")
    assert "cutoff" in str(exc.value).lower()


def test_spend_cutoff_via_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from chip import runtime, spend
    from chip.openrouter import ChipConfigError

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_CC_API_KEY", "sk-or-v1-unit-test-key")

    def fake_guard(command: str, key: str) -> float:
        raise spend.SpendCutoffError("cutoff for test")

    monkeypatch.setattr(spend, "guard_before_live_completion", fake_guard)
    with pytest.raises(ChipConfigError):
        runtime.complete([{"role": "user", "content": "hi"}], use_mock=False, command="chat")


def test_token_estimate_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import spend

    cost, source, p, c = spend.cost_from_completion_payload(
        {"usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}},
        "deepseek/deepseek-v4.1-flash",
    )
    assert source == "token_estimate"
    assert cost == pytest.approx(0.07, rel=1e-3)


def test_require_api_key_rejects_owui_env_names_only() -> None:
    """Chip module must not read OWUI key from env."""
    from chip.openrouter import ENV_KEY, require_api_key

    assert ENV_KEY == "OPENROUTER_CC_API_KEY"
    os.environ.pop("OPENROUTER_CC_API_KEY", None)
    os.environ["OPENROUTER_OWUI_API_KEY"] = "sk-or-v1-fake-owui-should-not-work"
    with pytest.raises(Exception) as exc:
        require_api_key()
    assert "OPENROUTER_CC_API_KEY" in str(exc.value)


def test_launcher_executable() -> None:
    assert LAUNCHER.is_file()
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [str(LAUNCHER), "ping", "--mock"],
            cwd=REPO,
            env={**os.environ, "CHIP_CHAT_DATA_DIR": tmp, "CHIP_CHAT_MOCK": "1"},
            capture_output=True,
            text=True,
            check=False,
        )
    assert proc.returncode == 0
