"""Parity P1.5 slice 1: MCP stdio client with allowlist, approval levels and audit."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from chip import mcp_client as mc

FAKE = str(Path(__file__).resolve().parent / "fixtures" / "mcp_fake_server.py")


def _server(mode: str = "ok", **extra) -> dict:
    cfg = {
        "command": [sys.executable, FAKE, mode],
        "timeout_s": 10,
        "tools": {"echo": "auto", "add": "ask", "danger": "deny", "env": "auto", "ghost": "auto", "odd": "maybe"},
    }
    cfg.update(extra)
    return cfg


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(data))
    monkeypatch.delenv("CHIP_MCP_CONFIG", raising=False)
    monkeypatch.setenv("CHIP_MCP", "1")
    return data


def _write_config(data: Path, servers: dict) -> Path:
    p = data / "mcp" / "servers.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"servers": servers}), encoding="utf-8")
    return p


@pytest.fixture
def config(_data_dir: Path) -> Path:
    return _write_config(_data_dir, {"fake": _server()})


def _audit() -> list[dict]:
    return mc.read_audit(limit=0)


def test_flag_default_off(monkeypatch: pytest.MonkeyPatch, config: Path) -> None:
    assert mc.enabled({}) is False
    assert mc.enabled({"CHIP_MCP": "1"}) is True
    monkeypatch.delenv("CHIP_MCP")
    with pytest.raises(mc.McpDisabled):
        mc.list_tools("fake")
    with pytest.raises(mc.McpDisabled):
        mc.call("fake", "echo", {"text": "hi"})
    assert _audit() == []


def test_config_validation(_data_dir: Path) -> None:
    assert mc.load_config() == {}
    _write_config(_data_dir, {"bad": {"command": "python3 server.py"}})
    with pytest.raises(mc.McpConfigError, match="list of strings"):
        mc.load_config()
    _write_config(_data_dir, {"bad": {"command": ["x"], "tools": {"t": 1}}})
    with pytest.raises(mc.McpConfigError):
        mc.load_config()
    p = _data_dir / "mcp" / "servers.json"
    p.write_text("{nope", encoding="utf-8")
    with pytest.raises(mc.McpConfigError):
        mc.load_config()
    _write_config(_data_dir, {"ok": {"command": ["x"], "timeout_s": 9999, "tools": {"A": " AUTO "}}})
    cfg = mc.load_config()["ok"]
    assert cfg.timeout_s == mc.MAX_TIMEOUT_S
    assert cfg.level("A") == "auto"
    assert cfg.level("missing") is None


def test_unknown_level_is_deny(config: Path) -> None:
    assert mc.get_server("fake").level("odd") == "deny"


def test_initialize_and_list_tools_annotated(config: Path) -> None:
    tools = {t["name"]: t["level"] for t in mc.list_tools("fake")}
    assert tools == {"echo": "auto", "add": "ask", "danger": "deny", "env": "auto"}


def test_paged_list_skips_notifications_and_server_requests(_data_dir: Path) -> None:
    _write_config(_data_dir, {"fake": _server("paged")})
    names = [t["name"] for t in mc.list_tools("fake")]
    assert names == ["echo", "add", "danger", "env"]


def test_auto_tool_runs_and_is_audited(config: Path) -> None:
    res = mc.call("fake", "echo", {"text": "hello"}, requested_by="pm")
    assert res["status"] == "ok"
    assert res["result"]["content"][0]["text"] == "hello"
    rows = _audit()
    assert rows[-1]["decision"] == "ok" and rows[-1]["tool"] == "echo" and rows[-1]["by"] == "pm"
    assert rows[-1]["args_sha256"] == mc.args_digest({"text": "hello"})


def test_audit_never_stores_arguments(config: Path) -> None:
    secret = "sk-" + "fake" + "9" * 20  # built at runtime
    mc.call("fake", "echo", {"text": secret})
    raw = mc.audit_path().read_text(encoding="utf-8")
    assert secret not in raw


def test_unallowlisted_tool_refused_without_spawn(_data_dir: Path, tmp_path: Path) -> None:
    calls = tmp_path / "calls.log"
    _write_config(_data_dir, {"fake": _server(env={"FAKE_MCP_CALLS": str(calls)})})
    with pytest.raises(mc.McpRefused, match="not allowlisted"):
        mc.call("fake", "unlisted", {})
    assert not calls.exists()
    assert _audit()[-1] == {**_audit()[-1], "decision": "refused", "reason": "not allowlisted"}


def test_deny_tool_never_runs(_data_dir: Path, tmp_path: Path) -> None:
    calls = tmp_path / "calls.log"
    _write_config(_data_dir, {"fake": _server(env={"FAKE_MCP_CALLS": str(calls)})})
    with pytest.raises(mc.McpRefused, match="deny"):
        mc.call("fake", "danger", {})
    assert not calls.exists()
    assert _audit()[-1]["decision"] == "refused"


def test_allowlisted_but_not_advertised_refused(config: Path) -> None:
    with pytest.raises(mc.McpRefused, match="does not advertise"):
        mc.call("fake", "ghost", {})
    assert _audit()[-1]["reason"] == "not advertised by server"


def test_unknown_server_refused_and_audited(config: Path) -> None:
    with pytest.raises(mc.McpRefused):
        mc.call("nope", "echo", {})
    assert _audit()[-1]["decision"] == "refused"


def test_ask_tool_pending_then_approved_single_use(_data_dir: Path, tmp_path: Path) -> None:
    calls = tmp_path / "calls.log"
    _write_config(_data_dir, {"fake": _server(env={"FAKE_MCP_CALLS": str(calls)})})
    res = mc.call("fake", "add", {"a": 2, "b": 3}, requested_by="dev")
    assert res["status"] == "pending"
    aid = res["approval_id"]
    assert not calls.exists()
    assert [p["id"] for p in mc.pending_approvals()] == [aid]
    # not yet approved
    with pytest.raises(mc.McpRefused, match="pending"):
        mc.call("fake", "add", {"a": 2, "b": 3}, requested_by="dev", approval_id=aid)
    # requester cannot approve own call
    with pytest.raises(mc.McpRefused, match="own call"):
        mc.decide_approval(aid, by="dev", approve=True)
    mc.decide_approval(aid, by="operator", approve=True)
    assert mc.pending_approvals() == []
    # approval bound to exact arguments
    with pytest.raises(mc.McpRefused, match="does not match"):
        mc.call("fake", "add", {"a": 2, "b": 4}, requested_by="dev", approval_id=aid)
    ok = mc.call("fake", "add", {"a": 2, "b": 3}, requested_by="dev", approval_id=aid)
    assert ok["result"]["content"][0]["text"] == "5"
    assert calls.read_text().split() == ["add"]
    # single use
    with pytest.raises(mc.McpRefused, match="used"):
        mc.call("fake", "add", {"a": 2, "b": 3}, requested_by="dev", approval_id=aid)
    decisions = [r["decision"] for r in _audit()]
    assert decisions[0] == "pending" and "approved" in decisions and decisions.count("ok") == 1


def test_rejected_approval_refuses(config: Path) -> None:
    aid = mc.call("fake", "add", {"a": 1, "b": 1}, requested_by="dev")["approval_id"]
    mc.decide_approval(aid, by="operator", approve=False)
    with pytest.raises(mc.McpRefused, match="rejected"):
        mc.call("fake", "add", {"a": 1, "b": 1}, requested_by="dev", approval_id=aid)
    with pytest.raises(mc.McpRefused, match="not pending"):
        mc.decide_approval(aid, by="operator", approve=True)
    with pytest.raises(mc.McpRefused, match="no approval"):
        mc.decide_approval("mcpa-missing", by="operator", approve=True)


@pytest.mark.parametrize("mode,match", [("garbage", "non-JSON"), ("badinit", "protocolVersion"), ("exit", "exited")])
def test_malformed_server_fails_closed(_data_dir: Path, mode: str, match: str) -> None:
    _write_config(_data_dir, {"fake": _server(mode)})
    with pytest.raises(mc.McpProtocolError, match=match):
        mc.call("fake", "echo", {"text": "x"})
    assert _audit()[-1]["decision"] == "error"


def test_timeout_fails_closed(_data_dir: Path) -> None:
    _write_config(_data_dir, {"fake": _server("hang", timeout_s=0.5)})
    with pytest.raises(mc.McpProtocolError, match="timeout"):
        mc.list_tools("fake")


def test_missing_binary_fails_closed(_data_dir: Path) -> None:
    _write_config(_data_dir, {"fake": _server(command=[str(_data_dir / "no-such-binary")])})
    with pytest.raises(mc.McpProtocolError, match="cannot start"):
        mc.call("fake", "echo", {})


def test_env_minimal_and_reference_resolution(_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = "v-" + "x" * 12
    monkeypatch.setenv("PARENT_SECRET_REF", value)
    monkeypatch.setenv("PARENT_ONLY", "1")
    _write_config(_data_dir, {"fake": _server(env={"FAKE_MCP_SECRET": "$PARENT_SECRET_REF"})})
    res = mc.call("fake", "env", {})
    assert res["result"]["content"][0]["text"] == value + "|clean"
    assert value not in mc.audit_path().read_text(encoding="utf-8")
    assert "FAKE_MCP_SECRET" in mc.get_server("fake").summary()["env_keys"]
    assert value not in json.dumps(mc.get_server("fake").summary())


def test_env_missing_reference_fails(_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    _write_config(_data_dir, {"fake": _server(env={"K": "$NOT_SET_ANYWHERE"})})
    with pytest.raises(mc.McpProtocolError, match="unset"):
        mc.call("fake", "echo", {})


def test_large_result_clipped(config: Path) -> None:
    res = mc.call("fake", "echo", {"text": "y" * (mc.MAX_RESULT_CHARS + 500)})
    assert res["result"]["truncated"] is True
    assert len(res["result"]["content"][0]["text"]) == mc.MAX_RESULT_CHARS


def _cli(args: list[str], data: Path, *, flag: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CHIP_CHAT_DATA_DIR"] = str(data)
    env.pop("CHIP_MCP", None)
    if flag:
        env["CHIP_MCP"] = "1"
    root = Path(__file__).resolve().parent
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "chip", "mcp", *args], capture_output=True, text=True, env=env, timeout=60)


def test_cli_transcript(config: Path, _data_dir: Path) -> None:
    r = _cli(["tools", "fake", "--mock"], _data_dir, flag=False)
    assert r.returncode == 3, r.stderr
    r = _cli(["servers", "--mock"], _data_dir)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["servers"][0]["name"] == "fake"
    r = _cli(["tools", "fake", "--mock"], _data_dir)
    assert r.returncode == 0, r.stderr
    assert {t["name"] for t in json.loads(r.stdout)} == {"echo", "add", "danger", "env"}
    r = _cli(["call", "fake", "echo", "--args", '{"text": "cli"}', "--mock"], _data_dir)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["result"]["content"][0]["text"] == "cli"
    r = _cli(["call", "fake", "danger", "--mock"], _data_dir)
    assert r.returncode == 2 and "REFUSED" in r.stderr
    r = _cli(["call", "fake", "add", "--args", '{"a": 1, "b": 2}', "--by", "dev", "--mock"], _data_dir)
    assert r.returncode == 4, r.stderr
    aid = json.loads(r.stdout)["approval_id"]
    r = _cli(["pending", "--mock"], _data_dir)
    assert [p["id"] for p in json.loads(r.stdout)] == [aid]
    r = _cli(["approve", aid, "--by", "dev", "--mock"], _data_dir)
    assert r.returncode == 2
    r = _cli(["approve", aid, "--by", "operator", "--mock"], _data_dir)
    assert r.returncode == 0, r.stderr
    r = _cli(["call", "fake", "add", "--args", '{"a": 1, "b": 2}', "--by", "dev", "--approval", aid, "--mock"], _data_dir)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["result"]["content"][0]["text"] == "3"
    r = _cli(["call", "fake", "echo", "--args", "{bad", "--mock"], _data_dir)
    assert r.returncode == 2
    r = _cli(["audit", "--limit", "3", "--mock"], _data_dir)
    assert r.returncode == 0 and len(json.loads(r.stdout)) == 3
