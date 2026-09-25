"""Allowlisted tool execution with gating, timeout, and JSONL audit."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from chip import config
from chip import store
from chip.tools_manifest import ToolSpec, default_timeout_seconds, resolve_tool

AUTO_ENV = "CHIP_TOOL_AUTO"
DEFAULT_CWD_ENV = "CHIP_TOOL_CWD"


class ToolGatedError(Exception):
    """Execution refused because --execute (or CHIP_TOOL_AUTO) was not set."""


class ToolNotAllowedError(Exception):
    """Tool id is not on the allowlist or extra args are forbidden."""


@dataclass
class ToolRunResult:
    tool_id: str
    argv: list[str]
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    executed: bool
    risk: str
    cwd: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "argv": self.argv,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "executed": self.executed,
            "risk": self.risk,
            "cwd": self.cwd,
        }


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def execution_allowed(*, execute: bool) -> bool:
    if execute:
        return True
    config.load_dotenv()
    return os.environ.get(AUTO_ENV, "").strip().lower() in ("1", "true", "yes")


def audit_path() -> Path:
    return config.data_dir() / "audit" / "tool_invocations.jsonl"


def append_audit(record: dict[str, Any]) -> None:
    store.append_jsonl(audit_path(), record)


def _resolve_cwd(cwd: str | Path | None) -> Path:
    if cwd is not None:
        return Path(cwd).expanduser().resolve()
    config.load_dotenv()
    override = os.environ.get(DEFAULT_CWD_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.cwd()


def build_argv(spec: ToolSpec, extra_args: Sequence[str]) -> list[str]:
    extras = [str(a) for a in extra_args if str(a)]
    if extras and not spec.allow_extra_args:
        raise ToolNotAllowedError(
            f"tool {spec.id!r} does not accept extra arguments (allowlist argv only)"
        )
    return list(spec.argv) + extras


def run(
    tool_name: str,
    *extra_args: str,
    execute: bool = False,
    timeout_seconds: float | None = None,
    cwd: str | Path | None = None,
    task_id: str | None = None,
) -> ToolRunResult:
    """
    Run an allowlisted tool by id. No shell — argv comes from the manifest only.

    Requires ``execute=True`` or ``CHIP_TOOL_AUTO=1`` to actually spawn a subprocess.
    Without execution permission, returns a dry-run result (exit_code -1, executed=False).
    """
    spec = resolve_tool(tool_name)
    if spec is None:
        raise ToolNotAllowedError(f"tool {tool_name!r} is not on the allowlist")

    argv = build_argv(spec, extra_args)
    workdir = _resolve_cwd(cwd)
    timeout = timeout_seconds if timeout_seconds is not None else default_timeout_seconds()
    allowed = execution_allowed(execute=execute)

    base_audit: dict[str, Any] = {
        "ts": _ts(),
        "tool_id": spec.id,
        "argv": argv,
        "risk": spec.risk,
        "cwd": str(workdir),
        "execute_requested": bool(execute),
        "auto_env": os.environ.get(AUTO_ENV, ""),
    }
    if task_id:
        base_audit["task_id"] = task_id

    if not allowed:
        result = ToolRunResult(
            tool_id=spec.id,
            argv=argv,
            stdout="",
            stderr="execution gated: pass execute=True, CLI --execute, or CHIP_TOOL_AUTO=1",
            exit_code=-1,
            timed_out=False,
            executed=False,
            risk=spec.risk,
            cwd=str(workdir),
        )
        append_audit({**base_audit, **result.to_dict(), "gated": True})
        return result

    try:
        proc = subprocess.run(
            argv,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        timed_out = False
        exit_code = int(proc.returncode)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -9
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        stderr = (stderr + "\n").strip() + f"timed out after {timeout}s (process killed)"
    except OSError as exc:
        timed_out = False
        exit_code = 127
        stdout = ""
        stderr = str(exc)

    result = ToolRunResult(
        tool_id=spec.id,
        argv=argv,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        timed_out=timed_out,
        executed=True,
        risk=spec.risk,
        cwd=str(workdir),
    )
    append_audit({**base_audit, **result.to_dict(), "gated": False})
    return result


def run_or_raise(
    tool_name: str,
    *extra_args: str,
    execute: bool = False,
    **kwargs: Any,
) -> ToolRunResult:
    if not execution_allowed(execute=execute):
        raise ToolGatedError(
            "tool execution requires execute=True, CLI --execute, or CHIP_TOOL_AUTO=1"
        )
    return run(tool_name, *extra_args, execute=True, **kwargs)
