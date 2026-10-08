"""Exported-engine test isolation: tmp data dirs and no live docker mutations."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_DOCKER_MUTATION_TOKENS = ("restart", "stop", "start", "rm", "compose")


class DockerMutationBlockedError(AssertionError):
    """Raised when a test would invoke a real docker mutation on the host."""


def _argv_list(cmd: object) -> list[str]:
    if isinstance(cmd, str):
        return [cmd]
    if isinstance(cmd, bytes):
        return [cmd.decode(errors="replace")]
    return [str(x) for x in cmd]


def _forbidden_docker_invocation(cmd: object) -> bool:
    argv = _argv_list(cmd)
    if not argv:
        return False
    exe = Path(argv[0]).name.lower()
    if exe in ("docker", "docker.exe"):
        return True
    blob = " ".join(argv).lower()
    if "docker" in blob and any(tok in blob for tok in _DOCKER_MUTATION_TOKENS):
        return True
    return False


def _guard_subprocess(real: object, cmd: object, *args: object, **kwargs: object):
    if _forbidden_docker_invocation(cmd):
        raise DockerMutationBlockedError(
            f"tests must not run live docker mutations (blocked argv={_argv_list(cmd)!r}); "
            "inject runner= or patch subprocess in the module under test"
        )
    return real(cmd, *args, **kwargs)


@pytest.fixture(autouse=True)
def chip_test_isolation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point chip-chat state at tmp_path and refuse live docker CLI mutations."""
    data = tmp_path / "chip-chat-data"
    data.mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(data))
    monkeypatch.setenv("HOME", str(home))

    real_run = subprocess.run
    real_popen = subprocess.Popen
    real_check_call = subprocess.check_call
    real_check_output = subprocess.check_output

    def guarded_run(cmd, *args, **kwargs):
        return _guard_subprocess(real_run, cmd, *args, **kwargs)

    class GuardedPopen(subprocess.Popen):
        def __init__(self, cmd, *args, **kwargs):
            if _forbidden_docker_invocation(cmd):
                raise DockerMutationBlockedError(
                    f"tests must not Popen live docker mutations (blocked argv={_argv_list(cmd)!r})"
                )
            super().__init__(cmd, *args, **kwargs)

    def guarded_check_call(cmd, *args, **kwargs):
        return _guard_subprocess(real_check_call, cmd, *args, **kwargs)

    def guarded_check_output(cmd, *args, **kwargs):
        return _guard_subprocess(real_check_output, cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
    monkeypatch.setattr(subprocess, "check_call", guarded_check_call)
    monkeypatch.setattr(subprocess, "check_output", guarded_check_output)

    return data
