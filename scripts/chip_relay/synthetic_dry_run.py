"""Block live host mutations during synthetic accuracy / smoke paths."""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_DOCKER_MUTATION_TOKENS = ("restart", "stop", "start", "rm", "compose")


class SyntheticDryRunViolation(RuntimeError):
    """A real subprocess/docker invocation was attempted during a synthetic run."""


def _argv_list(cmd: object) -> list[str]:
    if isinstance(cmd, str):
        return [cmd]
    if isinstance(cmd, bytes):
        return [cmd.decode(errors="replace")]
    return [str(x) for x in cmd]


def _is_docker_invocation(cmd: object) -> bool:
    argv = _argv_list(cmd)
    if not argv:
        return False
    exe = Path(argv[0]).name.lower()
    if exe in ("docker", "docker.exe"):
        return True
    blob = " ".join(argv).lower()
    return "docker" in blob and any(tok in blob for tok in _DOCKER_MUTATION_TOKENS)


@contextmanager
def guard_subprocess(*, allow_non_docker: bool = False) -> Iterator[dict[str, list[str]]]:
    """Refuse docker CLI during synthetic runs; optionally refuse any subprocess."""
    attempts: list[list[str]] = []
    real_run = subprocess.run
    real_popen = subprocess.Popen
    real_check_call = subprocess.check_call
    real_check_output = subprocess.check_output

    def _blocked(cmd: object) -> None:
        argv = _argv_list(cmd)
        attempts.append(argv)
        if _is_docker_invocation(cmd):
            raise SyntheticDryRunViolation(f"synthetic dry-run blocked docker argv={argv!r}")
        if not allow_non_docker:
            raise SyntheticDryRunViolation(f"synthetic dry-run blocked subprocess argv={argv!r}")

    def guarded_run(cmd, *args, **kwargs):
        _blocked(cmd)
        return real_run(cmd, *args, **kwargs)

    class GuardedPopen(subprocess.Popen):
        def __init__(self, cmd, *args, **kwargs):
            _blocked(cmd)
            super().__init__(cmd, *args, **kwargs)

    def guarded_check_call(cmd, *args, **kwargs):
        _blocked(cmd)
        return real_check_call(cmd, *args, **kwargs)

    def guarded_check_output(cmd, *args, **kwargs):
        _blocked(cmd)
        return real_check_output(cmd, *args, **kwargs)

    subprocess.run = guarded_run  # type: ignore[method-assign]
    subprocess.Popen = GuardedPopen  # type: ignore[misc,assignment]
    subprocess.check_call = guarded_check_call  # type: ignore[method-assign]
    subprocess.check_output = guarded_check_output  # type: ignore[method-assign]
    try:
        yield {"attempts": attempts}
    finally:
        subprocess.run = real_run  # type: ignore[method-assign]
        subprocess.Popen = real_popen  # type: ignore[method-assign]
        subprocess.check_call = real_check_call  # type: ignore[method-assign]
        subprocess.check_output = real_check_output  # type: ignore[method-assign]
