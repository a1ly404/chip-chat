"""Tests for chip-relay Docker bring-up helpers (no real docker)."""

from __future__ import annotations

import subprocess
import time

import pytest

from chip_relay_deploy import (
    container_is_stuck,
    health_exec_python,
    wait_for_container_health,
)


def test_container_is_stuck() -> None:
    assert container_is_stuck("created")
    assert container_is_stuck("Created")
    assert container_is_stuck("exited")
    assert container_is_stuck("dead")
    assert not container_is_stuck("running")
    assert not container_is_stuck("restarting")


def test_health_exec_python_uses_port() -> None:
    snippet = health_exec_python(48099)
    assert "48099" in snippet
    assert "urllib.request.urlopen" in snippet


def test_wait_for_health_succeeds_when_exec_returns_zero() -> None:
    calls = 0

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        assert "exec" in cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    wait_for_container_health(
        ["docker", "compose"],
        timeout_sec=10,
        poll_sec=0.01,
        run=fake_run,
    )
    assert calls == 1


def test_wait_for_health_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(cmd, 1, "", "starting")

    monkeypatch.setattr(time, "sleep", lambda _s: None)

    with pytest.raises(TimeoutError, match="did not return 200"):
        wait_for_container_health(
            ["docker", "compose"],
            timeout_sec=0.05,
            poll_sec=0.01,
            run=fake_run,
        )
