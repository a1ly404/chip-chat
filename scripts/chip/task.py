"""Task envelope: tool_invocations + execution_result (Phase 4)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip import config
from chip import evidence
from chip import tool as tool_exec
from chip.need_tool import NeedToolError
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
    acceptance: list[dict[str, Any]] | None = None,
    issue_key: str | None = None,
) -> dict[str, Any]:
    return {
        "id": new_task_id(),
        "created_at": _ts(),
        "updated_at": _ts(),
        "status": "open",
        "tool": tool_id,
        "args": list(args or []),
        "note": note or "",
        "acceptance": evidence.normalize_acceptance(acceptance),
        "evidence": [],
        "tool_invocations": [],
        "execution_result": None,
        "issue_key": issue_key or "",
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


def create_task(
    tool_id: str,
    args: list[str] | None = None,
    note: str | None = None,
    *,
    acceptance: list[dict[str, Any]] | None = None,
    issue_key: str | None = None,
) -> dict[str, Any]:
    if resolve_tool(tool_id) is None:
        raise NeedToolError(tool_id)
    env = empty_envelope(
        tool_id=tool_id,
        args=args,
        note=note,
        acceptance=acceptance,
        issue_key=issue_key,
    )
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

    envelope["status"] = "open"
    save_task(envelope)

    note = str(envelope.get("note") or "")
    try:
        result = tool_exec.run(
            tool_id,
            *extra,
            execute=execute,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
            task_id=task_id,
            user_intent=note,
        )
    except NeedToolError as exc:
        envelope["status"] = "blocked"
        envelope["execution_result"] = {
            "ok": False,
            "exit_code": -1,
            "error": exc.token(),
            "gated": False,
        }
        save_task(envelope)
        return envelope
    except tool_exec.ToolGatedError as exc:
        envelope["status"] = "blocked"
        envelope["execution_result"] = {
            "ok": False,
            "exit_code": -1,
            "error": str(exc),
            "gated": True,
        }
        save_task(envelope)
        return envelope
    except tool_exec.ToolNotAllowedError as exc:
        envelope["status"] = "blocked"
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
        envelope["status"] = "open"
        envelope["execution_result"] = {
            "ok": False,
            "exit_code": result.exit_code,
            "gated": True,
            "summary": result.stderr or "execution gated",
        }
    elif result.timed_out:
        envelope["status"] = "blocked"
        envelope["execution_result"] = {
            "ok": False,
            "exit_code": result.exit_code,
            "timed_out": True,
            "summary": "timed out",
        }
    elif result.exit_code == 0:
        _capture_tool_evidence(envelope, result)
        envelope["execution_result"] = {
            "ok": True,
            "exit_code": 0,
            "summary": "success",
        }
        evidence.apply_try_done(envelope)
    else:
        envelope["status"] = "blocked"
        envelope["execution_result"] = {
            "ok": False,
            "exit_code": result.exit_code,
            "summary": f"exit {result.exit_code}",
        }

    save_task(envelope)
    return envelope


def _capture_tool_evidence(envelope: dict[str, Any], result: tool_exec.ToolRunResult) -> None:
    """Attach evidence rows only from tool stdout/stderr (never model text)."""
    acceptance = envelope.get("acceptance")
    if not isinstance(acceptance, list):
        return
    blob = (result.stdout or "") + "\n" + (result.stderr or "")
    for item in acceptance:
        aid = str(item.get("id") or "")
        etype = str(item.get("evidence_type") or "")
        if etype == "url_regex" and blob.strip():
            evidence.capture_evidence_from_tool_output(
                envelope,
                tool_id=result.tool_id,
                stdout=result.stdout,
                stderr=result.stderr,
                acceptance_id=aid,
                kind=etype,
                payload=blob.strip(),
            )
        elif etype == "literal_in_tool_output":
            expected = str(item.get("text") or "")
            if expected and expected in blob:
                evidence.capture_evidence_from_tool_output(
                    envelope,
                    tool_id=result.tool_id,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    acceptance_id=aid,
                    kind=etype,
                    payload=blob,
                )
        elif etype == "json_path":
            meta = item.get("json_path") if isinstance(item.get("json_path"), dict) else {}
            path = str(meta.get("path") or "")
            expected = str(meta.get("expected") or "")
            if path and expected:
                try:
                    data = json.loads(result.stdout or "{}")
                    cur: Any = data
                    for part in path.split("."):
                        cur = cur[part]
                    if str(cur) == expected:
                        evidence.capture_evidence_from_tool_output(
                            envelope,
                            tool_id=result.tool_id,
                            stdout=result.stdout,
                            stderr=result.stderr,
                            acceptance_id=aid,
                            kind=etype,
                            payload=result.stdout or "",
                        )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    pass
        elif etype == "discord_ts" and blob.strip():
            evidence.capture_evidence_from_tool_output(
                envelope,
                tool_id=result.tool_id,
                stdout=result.stdout,
                stderr=result.stderr,
                acceptance_id=aid,
                kind=etype,
                payload=blob.strip().splitlines()[0],
            )


def close_task(task_id: str) -> dict[str, Any]:
    """Existing envelope close path — done only via try_done()."""
    envelope = load_task(task_id)
    evidence.apply_try_done(envelope)
    save_task(envelope)
    return envelope
