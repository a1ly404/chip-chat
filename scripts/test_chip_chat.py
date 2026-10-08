"""Offline tests for Chip Chat CLI (no OpenRouter key required)."""


import json
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
    proc = _run_chip(
        "ping",
        env={
            "OPENROUTER_CC_API_KEY": "not-a-real-key",
            "CHIP_CHAT_MOCK": "",
        },
    )
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


def test_spend_ledger_persists_across_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import spend

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    ledger = spend.default_ledger()
    ledger["monthly_usd"] = 0.001467
    ledger["estimated_monthly_usd"] = 0.001467
    ledger["monthly_source"] = "token_estimate"
    spend.save_ledger(ledger)

    reloaded = spend.load_ledger()
    assert reloaded["monthly_usd"] == pytest.approx(0.001467)
    assert spend.ledger_monthly_usd(reloaded) == pytest.approx(0.001467)
    assert (tmp_path / "spend.json").is_file()


def test_refresh_from_auth_does_not_zero_token_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chip import spend

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    ledger = spend.default_ledger()
    ledger["estimated_monthly_usd"] = 0.001467
    ledger["monthly_usd"] = 0.001467
    ledger["monthly_source"] = "token_estimate"
    spend.save_ledger(ledger)

    monkeypatch.setattr(
        spend.openrouter,
        "auth_key_info",
        lambda _key: {"usage_monthly": 0},
    )
    updated, api_monthly = spend.refresh_from_auth("sk-or-v1-unit-test-key")
    assert api_monthly is None
    assert updated["monthly_usd"] == pytest.approx(0.001467)
    assert spend.ledger_monthly_usd(updated) == pytest.approx(0.001467)


def test_effective_monthly_includes_session_delta_when_api_lags(tmp_path: Path) -> None:
    from chip import spend

    ledger = spend.default_ledger()
    ledger["monthly_usd"] = 0.00100806
    ledger["monthly_source"] = "api"
    ledger["monthly_sync_session_usd"] = 0.0
    ledger["session_usd"] = 0.0
    assert spend.effective_monthly_usd(ledger, 0.00100806) == pytest.approx(0.00100806)
    ledger["session_usd"] = 0.00045921
    assert spend.effective_monthly_usd(ledger, 0.00100806) == pytest.approx(0.00146727)


def test_record_after_live_completion_accrues_monthly_when_api_flat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chip import spend

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    ledger = spend.default_ledger()
    ledger["monthly_usd"] = 0.00100806
    ledger["monthly_source"] = "api"
    ledger["monthly_sync_session_usd"] = 0.0
    ledger["session_usd"] = 0.0
    spend.save_ledger(ledger)

    api_calls = {"n": 0}

    def fake_auth(_key: str) -> dict:
        api_calls["n"] += 1
        return {"usage_monthly": 0.00100806}

    monkeypatch.setattr(spend.openrouter, "auth_key_info", fake_auth)
    payload = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_cost": 0.00045921,
        }
    }
    updated = spend.record_after_live_completion("room", "deepseek/deepseek-v4.1-flash", payload, key="sk-test")
    assert api_calls["n"] == 1
    assert updated["session_usd"] == pytest.approx(0.00045921)
    assert spend.ledger_monthly_usd(updated) == pytest.approx(0.00146727)


def test_require_api_key_rejects_owui_env_names_only() -> None:
    """Chip module must not read OWUI key from env."""
    from chip.openrouter import ENV_KEY, require_api_key

    assert ENV_KEY == "OPENROUTER_CC_API_KEY"
    os.environ.pop("OPENROUTER_CC_API_KEY", None)
    os.environ["OPENROUTER_OWUI_API_KEY"] = "sk-or-v1-fake-owui-should-not-work"
    with pytest.raises(Exception) as exc:
        require_api_key()
    assert "OPENROUTER_CC_API_KEY" in str(exc.value)


def test_room_law_splits_binding_and_advisory() -> None:
    from chip.law import compose_system, load_room_law

    loaded = load_room_law("demo")
    assert loaded.path is not None
    assert "Stay inside Chip Chat CLI scope" in loaded.law
    assert "Prefer mock runs" in loaded.memory
    system = compose_system("You are Planner.", loaded)
    assert "ROOM LAW (binding)" in system
    assert "MEMORY NOTES (advisory only)" in system
    assert system.index("ROOM LAW") < system.index("MEMORY NOTES")
    assert system.endswith("You are Planner.")


def test_room_law_missing_file_is_empty() -> None:
    from chip.law import compose_system, law_label, load_room_law

    loaded = load_room_law("no-such-room")
    assert loaded.path is None
    assert law_label(loaded) == "law=(none)"
    assert compose_system("persona", loaded) == "persona"


def test_room_once_mock_prints_law_path() -> None:
    from test_pack_utils import channel_law_relative_label

    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_chip(
            "room",
            "example",
            "--once",
            "Say hello in one sentence.",
            "--mock",
            env={"CHIP_CHAT_DATA_DIR": tmp},
        )
    assert proc.returncode == 0
    assert channel_law_relative_label("example") in proc.stdout


def test_room_messages_include_law() -> None:
    from chip.cli import build_room_messages

    messages = build_room_messages("example", "default", "hi")
    assert messages[0]["role"] == "system"
    assert "Do not invent spend numbers" in messages[0]["content"]
    assert "MEMORY NOTES (advisory only)" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "hi"}


def test_load_room_history_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import store

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    path = store.thread_path("demo")
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one", "agent": "lucy"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "two", "agent": "mick"},
        {"role": "user", "content": "third", "agent": "planner"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    full = store.load_room_history("demo", limit=10)
    assert full == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "[lucy] one"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "[mick] two"},
        {"role": "user", "content": "[planner] third"},
    ]
    window = store.load_room_history("demo", limit=2)
    assert window == [
        {"role": "assistant", "content": "[mick] two"},
        {"role": "user", "content": "[planner] third"},
    ]
    assert store.load_room_history("demo", limit=0) == []


def test_build_room_messages_includes_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import store
    from chip.cli import build_room_messages

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    store.log_message("example", "user", "earlier question")
    store.log_message("example", "assistant", "earlier answer", agent="lucy")

    messages = build_room_messages("example", "default", "follow-up", history_limit=10)
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "earlier question"}
    assert messages[2] == {"role": "assistant", "content": "[lucy] earlier answer"}
    assert messages[3] == {"role": "user", "content": "follow-up"}


def test_room_shot_loads_prior_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import runtime, store

    captured: list[list[dict[str, str]]] = []

    def fake_complete(messages, **kwargs):
        captured.append(list(messages))
        return ("ok", {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}, "z-ai/glm-5.3-flash", {})

    monkeypatch.setattr(runtime, "complete", fake_complete)
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    store.log_message("demo", "user", "line one")
    store.log_message("demo", "assistant", "reply one", agent="lucy")

    from chip.cli import main

    code = main(["room", "demo", "-m", "line two", "--as", "mick", "--json", "--mock"])
    assert code == 0
    assert captured
    msgs = captured[0]
    assert msgs[1]["content"] == "line one"
    assert msgs[2]["content"] == "[lucy] reply one"
    assert msgs[-1] == {"role": "user", "content": "line two"}


def test_room_shot_accepts_once_with_json(tmp_path: Path) -> None:
    proc = _run_chip(
        "room",
        "demo",
        "--once",
        "Start test",
        "--as",
        "lucy",
        "--json",
        "--mock",
        env={"CHIP_CHAT_DATA_DIR": str(tmp_path)},
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["reply"]
    assert "session_usd" in payload


def test_room_shot_accepts_once_with_as(tmp_path: Path) -> None:
    proc = _run_chip(
        "room",
        "demo",
        "--once",
        "ping",
        "--as",
        "pm",
        "--mock",
        env={"CHIP_CHAT_DATA_DIR": str(tmp_path)},
    )
    assert proc.returncode == 0
    assert "pm" in (tmp_path / "threads" / "demo.jsonl").read_text(encoding="utf-8")


def test_room_shot_json_mock(tmp_path: Path) -> None:
    proc = _run_chip(
        "room",
        "demo",
        "-m",
        "Start test",
        "--as",
        "lucy",
        "--json",
        "--mock",
        env={"CHIP_CHAT_DATA_DIR": str(tmp_path)},
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["tokens_prompt"] == 12
    assert payload["tokens_completion"] == 20
    assert "session_usd" in payload and "monthly_usd" in payload
    assert "reply" in payload
    lines = (tmp_path / "threads" / "demo.jsonl").read_text(encoding="utf-8").splitlines()
    assistant = json.loads(lines[-1])
    assert assistant["agent"] == "lucy"
    assert assistant["role"] == "assistant"
    assert assistant["model"]
    assert "tokens_prompt" in assistant
    assert "cost_usd" in assistant


def test_room_shot_requires_as(tmp_path: Path) -> None:
    proc = _run_chip(
        "room",
        "demo",
        "-m",
        "hi",
        "--json",
        "--mock",
        env={"CHIP_CHAT_DATA_DIR": str(tmp_path)},
    )
    assert proc.returncode == 1
    assert "--as" in proc.stderr


def test_room_shot_api_error_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chip import runtime
    from chip.openrouter import ChipConfigError

    def boom(*_a, **_k):
        raise ChipConfigError("auth failed")

    monkeypatch.setattr(runtime, "complete", boom)
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    from chip.cli import main

    code = main(["room", "demo", "-m", "hi", "--as", "lucy", "--json", "--mock"])
    assert code == 2


def test_room_shot_turn_limit_blocks(tmp_path: Path) -> None:
    proc = _run_chip(
        "room",
        "demo",
        "-m",
        "hi",
        "--as",
        "lucy",
        "--turn-limit",
        "0",
        "--json",
        "--mock",
        env={"CHIP_CHAT_DATA_DIR": str(tmp_path)},
    )
    assert proc.returncode == 1
    assert not (tmp_path / "threads" / "demo.jsonl").exists()


def test_room_shot_does_not_invent_agent(tmp_path: Path) -> None:
    proc = _run_chip(
        "room",
        "demo",
        "-m",
        "Start test",
        "--as",
        "mick",
        "--json",
        "--mock",
        env={"CHIP_CHAT_DATA_DIR": str(tmp_path)},
    )
    assert proc.returncode == 0
    raw = (tmp_path / "threads" / "demo.jsonl").read_text(encoding="utf-8")
    assert "human_operator" not in raw
    assert "planner" not in raw
    agents = [json.loads(line).get("agent") for line in raw.splitlines()]
    assert agents == [None, "mick"]


def test_ui_lists_turns_and_mock_send(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CHIP_CHAT_UI_LIVE", raising=False)
    spec = importlib.util.spec_from_file_location("chip_ui", REPO / "scripts" / "chip_ui.py")
    assert spec and spec.loader
    ui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ui)
    assert "demo" in ui.list_rooms()
    proc = ui.send_via_cli("example", "Say hello in one sentence.")
    assert proc.returncode == 0
    assert "--mock" in proc.args
    turns = ui.recent_turns("example", 10)
    assert turns
    assert any(t.get("role") == "user" for t in turns)


def test_chip_ui_launcher_executable() -> None:
    ui_launcher = REPO / "bin" / "chip-ui"
    assert ui_launcher.is_file()


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
