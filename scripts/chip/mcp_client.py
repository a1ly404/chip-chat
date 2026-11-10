"""MCP client, stdio transport (parity P1.5 slice 1).

Chip can talk to Model Context Protocol servers that run as local subprocesses and speak
newline-delimited JSON-RPC 2.0 on stdin/stdout. Nothing here runs unless ``CHIP_MCP=1``.

Configuration lives in ``CHIP_CHAT_DATA_DIR/mcp/servers.json`` (override with
``CHIP_MCP_CONFIG``)::

    {
      "servers": {
        "notes": {
          "command": ["python3", "-m", "notes_mcp"],
          "env": {"NOTES_TOKEN": "$NOTES_TOKEN"},
          "timeout_s": 15,
          "tools": {"search": "auto", "create_note": "ask", "delete_note": "deny"}
        }
      }
    }

Policy, fail-closed at every step:

* **Allowlist per server.** Only tools named in ``tools`` can be called. A tool the server does not
  advertise in ``tools/list`` is refused too, even when it is in the allowlist.
* **Approval level per tool.** ``auto`` runs; ``ask`` records a pending approval and does not run
  until someone other than the requester approves it (single use, bound to the exact arguments);
  ``deny`` never runs. Unknown levels are treated as ``deny``.
* **Environment.** The subprocess gets a minimal environment (``PATH``, ``HOME``, ``LANG``) plus the
  server's ``env`` block. A value of ``$NAME`` is copied from the parent environment at spawn time,
  so secrets never live in the config file. Missing references fail the spawn.
* **Malformed output** (non-JSON line, wrong JSON-RPC shape, oversized line, early exit, timeout)
  raises :class:`McpProtocolError`; the call is audited as ``error``.

Every decision (``ok`` / ``error`` / ``refused`` / ``pending``) appends a row to
``mcp/audit.jsonl``. Rows carry a SHA-256 of the arguments, never the arguments themselves.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chip.config import data_dir

FLAG = "CHIP_MCP"
PROTOCOL_VERSION = "2025-06-18"
LEVELS = ("auto", "ask", "deny")
DEFAULT_TIMEOUT_S = 15.0
MAX_TIMEOUT_S = 120.0
MAX_LINE_BYTES = 1_000_000
MAX_RESULT_CHARS = 20_000
MAX_LIST_PAGES = 20
_BASE_ENV_KEYS = ("PATH", "HOME", "LANG", "SYSTEMROOT", "TEMP", "TMP")


class McpError(RuntimeError):
    """Base error for the MCP client."""


class McpDisabled(McpError):
    """Raised when CHIP_MCP is not on."""


class McpConfigError(McpError):
    """Raised for an invalid servers.json or unknown server."""


class McpProtocolError(McpError):
    """Raised when the server output is malformed, the server exits, or a call times out."""


class McpRefused(McpError):
    """Raised when policy refuses a call (not allowlisted, deny, bad approval)."""


def enabled(env: dict[str, str] | None = None) -> bool:
    src = os.environ if env is None else env
    return str(src.get(FLAG, "0")).strip().lower() in {"1", "true", "yes", "on"}


def require_enabled() -> None:
    if not enabled():
        raise McpDisabled(f"MCP is off; set {FLAG}=1 to enable")


def mcp_dir() -> Path:
    return data_dir() / "mcp"


def config_path() -> Path:
    override = os.environ.get("CHIP_MCP_CONFIG", "").strip()
    return Path(override).expanduser() if override else mcp_dir() / "servers.json"


def audit_path() -> Path:
    return mcp_dir() / "audit.jsonl"


def approvals_path() -> Path:
    return mcp_dir() / "approvals.jsonl"


# ---------------------------------------------------------------- config


@dataclass(frozen=True)
class ServerConfig:
    name: str
    command: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S
    tools: dict[str, str] = field(default_factory=dict)

    def level(self, tool: str) -> str | None:
        """Approval level for ``tool``; None when the tool is not allowlisted."""
        if tool not in self.tools:
            return None
        lvl = self.tools[tool]
        return lvl if lvl in LEVELS else "deny"

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command[:1]),
            "timeout_s": self.timeout_s,
            "tools": dict(self.tools),
            "env_keys": sorted(self.env),
        }


def _parse_server(name: str, raw: Any) -> ServerConfig:
    if not isinstance(raw, dict):
        raise McpConfigError(f"server {name!r}: must be an object")
    cmd = raw.get("command")
    if isinstance(cmd, str) or not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) and c for c in cmd):
        raise McpConfigError(f"server {name!r}: command must be a non-empty list of strings (no shell)")
    env = raw.get("env") or {}
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise McpConfigError(f"server {name!r}: env must map strings to strings")
    tools = raw.get("tools") or {}
    if not isinstance(tools, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in tools.items()):
        raise McpConfigError(f"server {name!r}: tools must map tool names to auto/ask/deny")
    try:
        timeout = float(raw.get("timeout_s", DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError) as exc:
        raise McpConfigError(f"server {name!r}: timeout_s must be a number") from exc
    timeout = min(max(timeout, 0.1), MAX_TIMEOUT_S)
    cwd = raw.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise McpConfigError(f"server {name!r}: cwd must be a string")
    return ServerConfig(
        name=name,
        command=tuple(cmd),
        env=dict(env),
        cwd=cwd,
        timeout_s=timeout,
        tools={k: v.strip().lower() for k, v in tools.items()},
    )


def load_config(path: Path | None = None) -> dict[str, ServerConfig]:
    p = path or config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise McpConfigError(f"cannot read {p}: {exc}") from exc
    servers = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise McpConfigError(f"{p}: top-level 'servers' object required")
    return {name: _parse_server(name, raw) for name, raw in servers.items()}


def get_server(name: str, path: Path | None = None) -> ServerConfig:
    servers = load_config(path)
    if name not in servers:
        raise McpConfigError(f"unknown MCP server: {name}")
    return servers[name]


def build_env(cfg: ServerConfig, parent: dict[str, str] | None = None) -> dict[str, str]:
    src = os.environ if parent is None else parent
    env = {k: src[k] for k in _BASE_ENV_KEYS if k in src}
    for key, val in cfg.env.items():
        if val.startswith("$") and len(val) > 1:
            ref = val[1:]
            if ref not in src:
                raise McpConfigError(f"server {cfg.name!r}: env {key} references unset ${ref}")
            env[key] = src[ref]
        else:
            env[key] = val
    return env


# ---------------------------------------------------------------- audit


def _now_iso(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def args_digest(arguments: dict[str, Any]) -> str:
    blob = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def audit(row: dict[str, Any]) -> None:
    _append(audit_path(), {"ts": _now_iso(), **row})


def read_audit(limit: int = 50) -> list[dict[str, Any]]:
    p = audit_path()
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-limit:] if limit > 0 else rows


# ---------------------------------------------------------------- stdio session


class StdioSession:
    """One MCP server subprocess. Use as a context manager."""

    def __init__(self, cfg: ServerConfig, *, parent_env: dict[str, str] | None = None) -> None:
        self.cfg = cfg
        self._env = build_env(cfg, parent_env)
        self._proc: subprocess.Popen[bytes] | None = None
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._stderr_tail: list[str] = []
        self._next_id = 0
        self.server_info: dict[str, Any] = {}

    # lifecycle
    def __enter__(self) -> "StdioSession":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start(self) -> None:
        try:
            self._proc = subprocess.Popen(
                list(self.cfg.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._env,
                cwd=self.cfg.cwd,
            )
        except OSError as exc:
            raise McpProtocolError(f"cannot start server {self.cfg.name!r}: {exc}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        res = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "chip-chat", "version": "1"},
            },
        )
        if not isinstance(res, dict) or "protocolVersion" not in res:
            raise McpProtocolError("initialize result missing protocolVersion")
        self.server_info = res.get("serverInfo") if isinstance(res.get("serverInfo"), dict) else {}
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

    # io
    def _read_stdout(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        out = proc.stdout
        while True:
            line = out.readline(MAX_LINE_BYTES + 1)
            if not line:
                self._lines.put(None)
                return
            self._lines.put(line)

    def _read_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        for raw in proc.stderr:
            self._stderr_tail.append(raw.decode("utf-8", "replace").rstrip()[:500])
            del self._stderr_tail[:-20]

    def _send(self, msg: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpProtocolError("session not started")
        try:
            proc.stdin.write((json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpProtocolError(f"server {self.cfg.name!r} closed stdin: {exc}") from exc

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        rid = self._next_id
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        deadline = time.monotonic() + self.cfg.timeout_s
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise McpProtocolError(f"timeout after {self.cfg.timeout_s}s waiting for {method}")
            try:
                line = self._lines.get(timeout=left)
            except queue.Empty:
                raise McpProtocolError(f"timeout after {self.cfg.timeout_s}s waiting for {method}") from None
            if line is None:
                raise McpProtocolError(f"server {self.cfg.name!r} exited during {method}")
            if len(line) > MAX_LINE_BYTES:
                raise McpProtocolError("server line exceeds size limit")
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                raise McpProtocolError("server wrote a non-JSON line on stdout") from None
            if not isinstance(obj, dict) or obj.get("jsonrpc") != "2.0":
                raise McpProtocolError("server wrote a message that is not JSON-RPC 2.0")
            if "method" in obj:
                if "id" in obj:  # server->client request: we offer no client capabilities
                    self._send(
                        {"jsonrpc": "2.0", "id": obj["id"], "error": {"code": -32601, "message": "not supported"}}
                    )
                continue  # notification
            if obj.get("id") != rid:
                continue  # stale/unknown response id
            if "error" in obj:
                err = obj["error"] if isinstance(obj["error"], dict) else {}
                raise McpProtocolError(f"{method} error {err.get('code')}: {str(err.get('message'))[:300]}")
            if "result" not in obj:
                raise McpProtocolError(f"{method} response has neither result nor error")
            return obj["result"]

    # MCP methods
    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_LIST_PAGES):
            res = self.request("tools/list", {"cursor": cursor} if cursor else {})
            if not isinstance(res, dict) or not isinstance(res.get("tools"), list):
                raise McpProtocolError("tools/list result missing tools array")
            for t in res["tools"]:
                if not isinstance(t, dict) or not isinstance(t.get("name"), str):
                    raise McpProtocolError("tools/list entry without a string name")
                tools.append(t)
            cursor = res.get("nextCursor") if isinstance(res.get("nextCursor"), str) else None
            if not cursor:
                return tools
        raise McpProtocolError("tools/list pagination exceeded page limit")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        res = self.request("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(res, dict) or not isinstance(res.get("content"), list):
            raise McpProtocolError("tools/call result missing content array")
        return res


# ---------------------------------------------------------------- approvals


def _read_approvals() -> list[dict[str, Any]]:
    p = approvals_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def approval_state() -> dict[str, dict[str, Any]]:
    """Fold the approvals log into id -> latest state."""
    state: dict[str, dict[str, Any]] = {}
    for row in _read_approvals():
        aid = row.get("id")
        if not isinstance(aid, str):
            continue
        if row.get("event") == "requested":
            state[aid] = {**row, "status": "pending"}
        elif aid in state and row.get("event") in {"approved", "rejected", "used"}:
            state[aid]["status"] = row["event"]
            state[aid][f"{row['event']}_by"] = row.get("by")
    return state


def pending_approvals() -> list[dict[str, Any]]:
    return [r for r in approval_state().values() if r["status"] == "pending"]


def request_approval(server: str, tool: str, arguments: dict[str, Any], requested_by: str) -> dict[str, Any]:
    row = {
        "event": "requested",
        "id": "mcpa-" + uuid.uuid4().hex[:12],
        "server": server,
        "tool": tool,
        "args_sha256": args_digest(arguments),
        "by": requested_by,
        "ts": _now_iso(),
    }
    _append(approvals_path(), row)
    return {**row, "status": "pending"}


def decide_approval(approval_id: str, *, by: str, approve: bool) -> dict[str, Any]:
    st = approval_state().get(approval_id)
    if st is None:
        raise McpRefused(f"no approval: {approval_id}")
    if st["status"] != "pending":
        raise McpRefused(f"approval {approval_id} is {st['status']}, not pending")
    if not by.strip() or by == st.get("by"):
        raise McpRefused("the requester cannot approve or reject their own call")
    event = "approved" if approve else "rejected"
    _append(approvals_path(), {"event": event, "id": approval_id, "by": by, "ts": _now_iso()})
    audit({"decision": event, "approval_id": approval_id, "server": st["server"], "tool": st["tool"], "by": by})
    return {**st, "status": event, f"{event}_by": by}


# ---------------------------------------------------------------- policy-gated entry points


def _clip_result(res: dict[str, Any]) -> dict[str, Any]:
    blob = json.dumps(res, ensure_ascii=False)
    if len(blob) <= MAX_RESULT_CHARS:
        return res
    texts = [c.get("text", "") for c in res.get("content", []) if isinstance(c, dict) and c.get("type") == "text"]
    return {
        "content": [{"type": "text", "text": "\n".join(texts)[:MAX_RESULT_CHARS]}],
        "isError": bool(res.get("isError")),
        "truncated": True,
    }


def list_tools(server: str, *, config: Path | None = None, parent_env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Advertised tools annotated with this host's policy level (None = not allowlisted)."""
    require_enabled()
    cfg = get_server(server, config)
    with StdioSession(cfg, parent_env=parent_env) as s:
        tools = s.list_tools()
    return [
        {"name": t["name"], "description": str(t.get("description", ""))[:300], "level": cfg.level(t["name"])}
        for t in tools
    ]


def call(
    server: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    requested_by: str = "cli",
    approval_id: str | None = None,
    config: Path | None = None,
    parent_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Policy-gated tool call. Returns {"status": "ok"|"pending", ...}; raises McpRefused / McpProtocolError."""
    require_enabled()
    args = arguments or {}
    if not isinstance(args, dict):
        raise McpRefused("arguments must be a JSON object")
    digest = args_digest(args)
    base = {"server": server, "tool": tool, "args_sha256": digest, "by": requested_by}
    try:
        cfg = get_server(server, config)
    except McpConfigError as exc:
        audit({**base, "decision": "refused", "reason": str(exc)})
        raise McpRefused(str(exc)) from exc
    level = cfg.level(tool)
    base["level"] = level
    if level is None:
        audit({**base, "decision": "refused", "reason": "not allowlisted"})
        raise McpRefused(f"tool {tool!r} is not allowlisted for server {server!r}")
    if level == "deny":
        audit({**base, "decision": "refused", "reason": "deny"})
        raise McpRefused(f"tool {tool!r} on {server!r} is set to deny")
    if level == "ask":
        if not approval_id:
            req = request_approval(server, tool, args, requested_by)
            audit({**base, "decision": "pending", "approval_id": req["id"]})
            return {"status": "pending", "approval_id": req["id"], "server": server, "tool": tool}
        st = approval_state().get(approval_id)
        reason = None
        if st is None:
            reason = "unknown approval"
        elif st["status"] != "approved":
            reason = f"approval is {st['status']}"
        elif (st.get("server"), st.get("tool"), st.get("args_sha256")) != (server, tool, digest):
            reason = "approval does not match this server/tool/arguments"
        if reason:
            audit({**base, "decision": "refused", "reason": reason, "approval_id": approval_id})
            raise McpRefused(reason)
        _append(approvals_path(), {"event": "used", "id": approval_id, "by": requested_by, "ts": _now_iso()})
        base["approval_id"] = approval_id
    t0 = time.monotonic()
    try:
        with StdioSession(cfg, parent_env=parent_env) as s:
            advertised = {t["name"] for t in s.list_tools()}
            if tool not in advertised:
                audit({**base, "decision": "refused", "reason": "not advertised by server"})
                raise McpRefused(f"server {server!r} does not advertise tool {tool!r}")
            res = s.call_tool(tool, args)
    except (McpProtocolError, McpConfigError) as exc:
        audit({**base, "decision": "error", "reason": str(exc)[:300], "ms": int((time.monotonic() - t0) * 1000)})
        raise McpProtocolError(str(exc)) from exc
    out = _clip_result(res)
    audit(
        {
            **base,
            "decision": "ok",
            "is_error": bool(out.get("isError")),
            "result_chars": len(json.dumps(out, ensure_ascii=False)),
            "ms": int((time.monotonic() - t0) * 1000),
        }
    )
    return {"status": "ok", "server": server, "tool": tool, "result": out}
