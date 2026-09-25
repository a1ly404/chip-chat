"""Task envelope: tool_invocations + execution_result (Phase 4)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip import config
from chip import tool as tool_exec
from chip.tools_manifest import resolve_tool

TASKS_DIR_NAME = "tasks"


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def tasks_dir() -> Path:
    path = config.data_dir() / TASKS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def task_path(task_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)
    return tasks_dir() / f"{safe}.json"


def new_task_id() -> str:
    return f"task-{uuid.uuid4().hex[:12]}"


def empty_envelope(
    *,
    tool_id: str,
    args: list[str] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "id": new_task_id(),
        "created_at": _ts(),
        "updated_at": _ts(),
        "status": "pending",
        "tool": tool_id,
        "args": list(args or []),
        "note": note or "",
        "tool_invocations": [],
        "execution_result": None,
    }


def save_task(envelope: dict[str, Any]) -> None:
    envelope["updated_at"] = _ts()
    path = task_path(str(envelope["id"]))
    path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_task(task_id: str) -> dict[str, Any]:
    path = task_path(task_id)
    if not path.is_file():
        raise FileNotFoundError(f"unknown task {task_id!r}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"corrupt task file {path}")
    return raw


def create_task(tool_id: str, args: list[str] | None = None, note: str | None = None) -> dict[str, Any]:
    if resolve_tool(tool_id) is None:
        raise ValueError(f"tool {tool_id!r} is not on the allowlist")
    env = empty_envelope(tool_id=tool_id, args=args, note=note)
    save_task(env)
    return env


def _invocation_record(result: tool_exec.ToolRunResult) -> dict[str, Any]:
    return {
        "ts": _ts(),
        "tool_id": result.tool_id,
        "argv": result.argv,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "executed": result.executed,
        "risk": result.risk,
        "cwd": result.cwd,
    }


def run_task(
    task_id: str,
    *,
    execute: bool = False,
    timeout_seconds: float | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    envelope = load_task(task_id)
    tool_id = str(envelope.get("tool") or "")
    args = envelope.get("args")
    if not isinstance(args, list):
        args = []
    extra = [str(a) for a in args]

    envelope["status"] = "running"
    save_task(envelope)

    try:
        result = tool_exec.run(
            tool_id,
            *extra,
            execute=execute,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
            task_id=task_id,
        )
    except tool_exec.ToolNotAllowedError as exc:
        envelope["status"] = "failed"
        envelope["execution_result"] = {
            "ok": False,
            "exit_code": -1,
            "error": str(exc),
            "gated": False,
        }
        save_task(envelope)
        return envelope

    invocations = envelope.get("tool_invocations")
    if not isinstance(invocations, list):
        invocations = []
    invocations.append(_invocation_record(result))
    envelope["tool_invocations"] = invocations

    if not result.executed:
        envelope["status"] = "pending"
        envelope["execution_result"] = {
            "ok": False,
            "exit_code": result.exit_code,
            "gated": True,
            "summary": result.stderr or "execution gated",
        }
    elif result.timed_out:
        envelope["status"] = "failed"
        envelope["execution_result"] = {
            "ok": False,
            "exit_code": result.exit_code,
            "timed_out": True,
            "summary": "timed out",
        }
    elif result.exit_code == 0:
        envelope["status"] = "completed"
        envelope["execution_result"] = {
            "ok": True,
            "exit_code": 0,
            "summary": "success",
        }
    else:
        envelope["status"] = "failed"
        envelope["execution_result"] = {
            "ok": False,
            "exit_code": result.exit_code,
            "summary": f"exit {result.exit_code}",
        }

    save_task(envelope)
    return envelope
