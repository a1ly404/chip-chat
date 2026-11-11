"""Parity P1.5 slice 3: MCP tools in the room chat turn (the relay's `chip room -m` shot path).

Real subprocess round trips use the in-repo fake stdio server; model calls use a fake completer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from chip import mcp_chat, mcp_client as mc, mcp_tools

FAKE = str(Path(__file__).resolve().parent / "fixtures" / "mcp_fake_server.py")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(data))
    for k in ("CHIP_MCP_CONFIG", "CHIP_MCP_CHAT_ROUNDS", "CHIP_CHAT_MOCK", "CHIP_STORE_GATED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CHIP_MCP", "1")
    monkeypatch.setenv("FAKE_MCP_CALLS", str(tmp_path / "calls.log"))
    p = data / "mcp" / "servers.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "servers": {
                    "fake": {
                        "command": [sys.executable, FAKE, "ok"],
                        "env": {"FAKE_MCP_CALLS": "$FAKE_MCP_CALLS"},
                        "timeout_s": 10,
                        "tools": {"echo": "auto", "add": "ask", "danger": "deny"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return data


def _ran(tmp_path: Path) -> list[str]:
    p = tmp_path / "calls.log"
    return p.read_text(encoding="utf-8").split() if p.is_file() else []


class FakeComplete:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []
        self.kwargs: list[dict] = []

    def __call__(self, messages, **kwargs):
        self.calls.append([dict(m) for m in messages])
        self.kwargs.append(kwargs)
        text = self.replies.pop(0) if self.replies else "final answer"
        return text, {"usage": {"prompt_tokens": 3, "completion_tokens": 2}}, "fake/model", {"session_usd": 0.0}


BASE = [{"role": "system", "content": "room law"}, {"role": "user", "content": "what does echo say?"}]


def test_flag_off_is_one_plain_completion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHIP_MCP", "0")
    fc = FakeComplete(['[[mcp fake echo {"text": "hi"}]]'])
    out = mcp_chat.run_chat_turn(BASE, complete=fc, requested_by="room:demo:mick", complete_kwargs={"command": "room"})
    assert len(fc.calls) == 1 and fc.calls[0] == BASE  # no planner block
    assert fc.kwargs[0] == {"command": "room"}
    assert out["reply"] == '[[mcp fake echo {"text": "hi"}]]'  # untouched: nothing parsed, nothing ran
    assert out["receipts"] == [] and out["completions"] == 1
    assert _ran(tmp_path) == []


def test_no_offered_tools_is_one_plain_completion(_data_dir: Path) -> None:
    (_data_dir / "mcp" / "servers.json").write_text(json.dumps({"servers": {}}), encoding="utf-8")
    fc = FakeComplete(["plain"])
    out = mcp_chat.run_chat_turn(BASE, complete=fc, requested_by="room:demo:mick")
    assert fc.calls[0] == BASE and out["reply"] == "plain" and out["completions"] == 1


def test_auto_tool_round_trip_with_receipt_footer(tmp_path: Path) -> None:
    fc = FakeComplete(['Checking.\n[[mcp fake echo {"text": "marker-123"}]]', "Echo said marker-123."])
    out = mcp_chat.run_chat_turn(BASE, complete=fc, requested_by="room:demo:mick")
    assert _ran(tmp_path) == ["echo"]
    assert len(fc.calls) == 2
    assert "[[mcp <server> <tool>" in fc.calls[0][0]["content"] and "fake echo (auto)" in fc.calls[0][0]["content"]
    follow = fc.calls[1][-1]["content"]
    assert "not instructions" in follow and "marker-123" in follow  # result goes back to the model as data
    assert fc.calls[1][-2] == {"role": "assistant", "content": 'Checking.\n[[mcp fake echo {"text": "marker-123"}]]'}
    assert out["text"] == "Echo said marker-123."
    assert out["reply"].endswith("MCP receipts:\n- fake echo: ok")
    assert out["receipts"][0]["status"] == "ok" and "excerpt" not in out["receipts"][0]
    assert out["completions"] == 2
    rows = mcp_tools.read_receipts(0)
    assert [r["by"] for r in rows] == ["room:demo:mick"]
    assert "marker-123" not in json.dumps(rows)


def test_ask_tool_pends_and_requester_cannot_approve(tmp_path: Path) -> None:
    fc = FakeComplete(['[[mcp fake add {"a": 2, "b": 3}]]', "Queued for approval."])
    out = mcp_chat.run_chat_turn(BASE, complete=fc, requested_by="room:demo:mick")
    assert _ran(tmp_path) == []
    rec = out["receipts"][0]
    assert rec["status"] == "pending" and rec["approval_id"].startswith("mcpa-")
    assert f"(approval {rec['approval_id']} waiting on a human)" in out["reply"]
    with pytest.raises(mc.McpRefused):
        mc.decide_approval(rec["approval_id"], by="room:demo:mick", approve=True)
    mc.decide_approval(rec["approval_id"], by="operator", approve=True)
    fc2 = FakeComplete(['[[mcp fake add {"a": 2, "b": 3}]]', "It is 5."])
    out2 = mcp_chat.run_chat_turn(BASE, complete=fc2, requested_by="room:demo:mick")
    assert out2["receipts"][0]["status"] == "ok" and _ran(tmp_path) == ["add"]
    assert "5" in fc2.calls[1][-1]["content"]


def test_denied_tool_refused_without_second_completion(tmp_path: Path) -> None:
    fc = FakeComplete(["Trying.\n[[mcp fake danger {}]]"])
    out = mcp_chat.run_chat_turn(BASE, complete=fc, requested_by="room:demo:mick")
    assert len(fc.calls) == 1 and _ran(tmp_path) == []
    assert out["text"] == "Trying."
    assert "fake danger: refused (not offered to this turn)" in out["reply"]


def test_malformed_line_refused(tmp_path: Path) -> None:
    fc = FakeComplete(["Hm.\n[[mcp fake echo not-json]]"])
    out = mcp_chat.run_chat_turn(BASE, complete=fc, requested_by="room:demo:mick")
    assert len(fc.calls) == 1 and _ran(tmp_path) == []
    assert out["receipts"][0]["status"] == "refused"
    assert out["text"] == "Hm."


def test_round_cap_refuses_extra_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHIP_MCP_CHAT_ROUNDS", "1")
    loop = '[[mcp fake echo {"text": "again"}]]'
    fc = FakeComplete([loop, "Still going.\n" + loop, loop])
    out = mcp_chat.run_chat_turn(BASE, complete=fc, requested_by="room:demo:mick")
    assert len(fc.calls) == 2 and _ran(tmp_path) == ["echo"]
    assert [r["status"] for r in out["receipts"]] == ["ok", "refused"]
    assert out["receipts"][1]["reason"] == "tool round cap"
    assert out["text"] == "Still going."


def test_rounds_env_clamped() -> None:
    assert mcp_chat.chat_rounds({}) == mcp_tools.DEFAULT_MAX_ROUNDS
    assert mcp_chat.chat_rounds({"CHIP_MCP_CHAT_ROUNDS": "99"}) == mcp_tools.HARD_MAX_ROUNDS
    assert mcp_chat.chat_rounds({"CHIP_MCP_CHAT_ROUNDS": "junk"}) == mcp_tools.DEFAULT_MAX_ROUNDS


def test_with_planner_adds_system_when_missing() -> None:
    msgs = mcp_chat.with_planner([{"role": "user", "content": "x"}], [{"server": "s", "tool": "t", "level": "auto"}])
    assert msgs[0]["role"] == "system" and "s t (auto)" in msgs[0]["content"]


def test_footer_caps_lines() -> None:
    recs = [{"server": "s", "tool": f"t{i}", "status": "ok"} for i in range(12)]
    foot = mcp_chat.receipt_footer(recs)
    assert foot.count("\n- s t") == 10 and foot.endswith("2 more")
    assert mcp_chat.receipt_footer([]) == ""


def test_room_shot_cli_runs_tools_and_stores_receipts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    from chip import runtime, store

    fc = FakeComplete(['[[mcp fake echo {"text": "cli-marker"}]]', "Done: cli-marker."])
    monkeypatch.setattr(runtime, "complete", fc)
    from chip.cli import main

    code = main(["room", "demo", "-m", "use echo", "--as", "mick", "--json", "--mock"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["reply"].startswith("Done: cli-marker.") and "- fake echo: ok" in payload["reply"]
    assert payload["mcp_receipts"][0]["status"] == "ok" and payload["completions"] == 2
    assert all(k.get("command") == "room" for k in fc.kwargs)
    rows = store.load_room_history("demo", limit=10)
    assert rows[-1]["content"].endswith("- fake echo: ok")
    raw = next((Path(tmp_path) / "data").rglob("demo*.jsonl")).read_text(encoding="utf-8")
    assert '"mcp_receipts"' in raw and '"text": "cli-marker"' not in raw
    assert [r["by"] for r in mcp_tools.read_receipts(0)] == ["room:demo:mick"]


def test_room_shot_cli_flag_off_has_no_mcp_keys(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from chip import runtime

    monkeypatch.setenv("CHIP_MCP", "0")
    fc = FakeComplete(["plain reply"])
    monkeypatch.setattr(runtime, "complete", fc)
    from chip.cli import main

    assert main(["room", "demo", "-m", "hi", "--as", "mick", "--json", "--mock"]) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["reply"] == "plain reply" and "mcp_receipts" not in payload and len(fc.calls) == 1
