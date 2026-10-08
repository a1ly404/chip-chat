"""Allowlisted tool execution with gating, timeout, and JSONL audit."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from chip import config
from chip import constraints
from chip import store
from chip import lanes
from chip.need_tool import NeedToolError
from chip.tools_manifest import ToolSpec, default_timeout_seconds, resolve_tool

AUTO_ENV = "CHIP_TOOL_AUTO"
STRICT_ENV = "CHIP_TOOL_STRICT"
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


def authorize_execute(
    spec: ToolSpec,
    *,
    speaker: str | None,
    room: str | None,
    scope: str | None,
    is_human_or_inject: bool,
    manifest: frozenset[str] | None = None,
) -> tuple[bool, str]:
    """Single gate into the executor — law id:authorize (Grok round 5/7).

    Every subprocess spawn passes here. Persona runs require a live claim
    (stand_down ∧ claim_owner==speaker ∧ tool∈manifest); the human/inject hot
    path is exempt (operator keyboard). Caller MUST go through here — a run that
    skips it is the v1 jailbreak Grok flagged.
    """
    from chip import authorize as authorize_mod
    from chip.tools_manifest import load_tools

    from chip import system_pack

    cli_label = system_pack.pack_identity_string("human_cli_speaker", default="operator-cli")
    effective_speaker = speaker or (cli_label if is_human_or_inject else "")
    return authorize_mod.authorize(
        effective_speaker,
        (scope or "").strip(),
        "execute",
        environ=os.environ,
        room=room,
        tool=spec.id,
        is_human_or_inject=is_human_or_inject,
        manifest=set(manifest) if manifest is not None else set(load_tools().keys()),
    )


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


_PM_GIT = {
    "git-add": ToolSpec("git-add", ("git", "add", "--"), "low", True, "git add inside the pm root"),
    "git-commit": ToolSpec("git-commit", ("git", "commit", "-m"), "low", True, "git commit inside the pm root"),
}


def _confine_pm_git(speaker: str, spec: ToolSpec, extra_args: Sequence[str], workdir: Path) -> None:
    root = lanes.persona_root(speaker)
    if root is None:
        raise ToolNotAllowedError(f"{speaker} git tool requires a lane root")
    root = root.resolve()
    if not workdir.resolve().is_relative_to(root):
        raise ToolNotAllowedError("git cwd is outside the persona root")
    if spec.id == "git-add":
        for arg in extra_args:
            if arg.startswith("-"):
                raise ToolNotAllowedError("git-add accepts paths only")
            target = (workdir / arg).resolve()
            if not target.is_relative_to(root):
                raise ToolNotAllowedError("git-add path is outside the persona root")


def run(
    tool_name: str,
    *extra_args: str,
    execute: bool = False,
    timeout_seconds: float | None = None,
    cwd: str | Path | None = None,
    task_id: str | None = None,
    user_intent: str | None = None,
    speaker: str | None = None,
    room: str | None = None,
    scope: str | None = None,
    is_human_or_inject: bool = False,
) -> ToolRunResult:
    """
    Run an allowlisted tool by id. No shell — argv comes from the manifest only.

    Requires ``execute=True`` or ``CHIP_TOOL_AUTO=1`` to actually spawn a subprocess.
    Without execution permission, returns a dry-run result (exit_code -1, executed=False).

    Round-7 (authorize wiring): every execute passes ``authorize_execute`` first.
    Persona callers must pass ``speaker`` + ``room`` + ``scope`` with a live claim;
    the human/inject hot path passes ``is_human_or_inject=True``. Refusals return a
    gated result (exit_code -1, executed=False) with the authorize reason in stderr
    and the audit ledger.
    """
    allow = lanes.persona_tool_allow(speaker)
    if allow is not None and tool_name not in allow:
        raise ToolNotAllowedError(f"tool {tool_name!r} is outside the {speaker} allowlist")
    spec = resolve_tool(tool_name)
    if spec is None and tool_name in _PM_GIT and allow is not None and tool_name in allow:
        spec = _PM_GIT[tool_name]
    if spec is None:
        raise NeedToolError(tool_name)
    started = time.perf_counter()

    def _learn(exit_code: int, artifact: str) -> None:
        lanes.record_outcome(
            tool_id=spec.id,
            persona=speaker or "anonymous",
            exit_code=exit_code,
            duration_s=time.perf_counter() - started,
            artifact=artifact,
            reflection=f"{speaker or 'anonymous'} {spec.id} exited {exit_code}",
        )

    intent = (user_intent or "").strip()
    if intent:
        blocked = constraints.first_hard_block(intent)
        if blocked:
            _learn(-1, blocked)
            raise ToolGatedError(blocked)

    argv = build_argv(spec, extra_args)
    from chip import boards

    boards.assert_tool_cannot_touch_boards(list(argv))
    workdir = _resolve_cwd(cwd)
    if spec.id in _PM_GIT:
        _confine_pm_git(speaker or "", spec, extra_args, workdir)
    denied = lanes.refuse_blob(*(argv + [str(workdir)]), ticket_id=task_id)
    if denied:
        lanes.file_retro(
            attempted=" ".join(argv),
            why=denied,
            envelope=f"tool={spec.id} ticket={task_id or ''}",
        )
        _learn(-1, denied)
        raise ToolGatedError(denied)
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
        _learn(result.exit_code, result.stderr)
        return result

    base_audit["speaker"] = speaker
    base_audit["room"] = room
    base_audit["scope"] = scope
    strict = os.environ.get(STRICT_ENV, "").strip().lower() in ("1", "true", "yes")
    if speaker is None and not strict and not is_human_or_inject:
        # Legacy bridge (round 7): speaker-less callers (existing CLI/tests) run
        # ungated today. Agents that pass a speaker are ALWAYS gated. Flip
        # CHIP_TOOL_STRICT=1 (Phase C+) to fail-closed for anonymous executes too.
        pass
    else:
        ok, reason = authorize_execute(
            spec,
            speaker=speaker,
            room=room,
            scope=scope,
            is_human_or_inject=is_human_or_inject,
            manifest=allow,
        )
        if not ok:
            result = ToolRunResult(
                tool_id=spec.id,
                argv=argv,
                stdout="",
                stderr=f"authorization refused: {reason}",
                exit_code=-1,
                timed_out=False,
                executed=False,
                risk=spec.risk,
                cwd=str(workdir),
            )
            append_audit({**base_audit, **result.to_dict(), "gated": True, "authz_refused": reason})
            _learn(result.exit_code, result.stderr)
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
    _learn(result.exit_code, result.stdout or result.stderr)
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
    result = run(tool_name, *extra_args, execute=True, **kwargs)
    if not result.executed:
        # authorize/executor refusals surface as exceptions here (or_raise contract)
        raise ToolGatedError(result.stderr or "tool execution refused")
    return result
