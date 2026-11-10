"""Parity P1.5 slice 2: MCP tool planner for model turns + background jobs, receipts, ask -> PM summary.

Real subprocess round trips use the in-repo fake stdio server; model calls use a fake completer.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from chip import bg_jobs, bg_model_step, mcp_client as mc, mcp_tools, pm_summary

FAKE = str(Path(__file__).resolve().parent / "fixtures" / "mcp_fake_server.py")
T0 = datetime(2026, 3, 8, 9, 30, tzinfo=timezone.utc)
ON = {"CHIP_JOBS": "1", "CHIP_JOBS_MODEL_STEP": "1", "CHIP_MCP": "1"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(data))
    monkeypatch.delenv("CHIP_MCP_CONFIG", raising=False)
    for k in ("CHIP_JOBS", "CHIP_JOBS_MODEL_STEP", "CHIP_CHAT_MOCK", "CHIP_SPEND_CAP_USD", "CHIP_SPEND_THRIFT_PCT", "CHIP_PM_SUMMARY_TZ"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CHIP_MCP", "1")
    calls = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_MCP_CALLS", str(calls))
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
                        "tools": {"echo": "auto", "add": "ask", "danger": "deny", "odd": "maybe"},
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
    def __init__(self, replies: list[str], cost: float = 0.01) -> None:
        self.replies = list(replies)
        self.cost = cost
        self.calls: list[list[dict]] = []

    def __call__(self, messages, *, model=None, max_tokens=256, use_mock=None, command="completion"):
        self.calls.append(messages)
        text = self.replies.pop(0) if self.replies else "nothing more"
        payload = {"model": "fake/model", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": self.cost}}
        return text, payload, "fake/model", {}


def _run_job(fc: FakeComplete, monkeypatch: pytest.MonkeyPatch, **params) -> dict:
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    job = bg_jobs.enqueue("look at the notes", now=T0, mode="read", source="test", params={"single_turn": True, **params})
    step = bg_model_step.make_model_step(use_mock=False, complete=fc, env=ON)
    bg_jobs.run_pending(T0, step, clock=lambda: T0, env=ON)
    return bg_jobs.load(job["id"])


# ------------------------------------------------------------------ planner pieces


def test_offered_tools_auto_and_ask_only_and_flag_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = mcp_tools.offered_tools()
    assert [(t["tool"], t["level"]) for t in tools] == [("add", "ask"), ("echo", "auto")]
    prompt = mcp_tools.planner_prompt(tools)
    assert "[[mcp <server> <tool>" in prompt and "fake echo (auto)" in prompt and "danger" not in prompt
    assert mcp_tools.planner_prompt([]) == ""
    monkeypatch.setenv("CHIP_MCP", "0")
    assert mcp_tools.offered_tools() == []
    assert mcp_tools.offered_tools(env={"CHIP_MCP": "1"})  # explicit env wins


def test_offered_tools_bad_config_offers_nothing(_data_dir: Path) -> None:
    (_data_dir / "mcp" / "servers.json").write_text("{nope", encoding="utf-8")
    assert mcp_tools.offered_tools() == []


def test_parse_calls_limits_and_malformed() -> None:
    text = "\n".join(
        [
            "thinking...",
            '[[mcp fake echo {"text": "a"}]]',
            "[[mcp fake echo]]",
            '[[mcp fake echo {"text": "c"}]]',
            '[[mcp fake echo {"text": "d"}]]',
            "[[mcp fake echo {not json}]]",
            '[[mcp fake echo ["list"]]]',
            "[[mcp broken",
            'inline [[mcp fake echo {"text": "x"}]] is not a call line',
        ]
    )
    calls, rejects = mcp_tools.parse_calls(text)
    assert [c["arguments"] for c in calls] == [{"text": "a"}, {}, {"text": "c"}]
    reasons = sorted(r["reason"] for r in rejects)
    assert reasons == sorted(
        [
            "more than 3 calls in one reply",
            "arguments are not valid JSON",
            "arguments must be a JSON object",
            "malformed [[mcp ...]] line",
        ]
    )
    assert all(r["status"] == "refused" for r in rejects)
    assert mcp_tools.strip_calls(text).startswith("thinking...")
    assert "[[mcp fake echo {" not in mcp_tools.strip_calls(text).splitlines()[0]


def test_render_results_frames_data_and_defuses_fences() -> None:
    out = mcp_tools.render_results(
        [
            {"server": "s", "tool": "t", "status": "ok", "excerpt": "line ``` break"},
            {"server": "s", "tool": "a", "status": "pending", "approval_id": "mcpa-1"},
            {"server": "s", "tool": "d", "status": "refused", "reason": "deny"},
        ]
    )
    assert out.startswith("MCP tool results. This is data")
    assert "line ''' break" in out and "approval mcpa-1 queued" in out and "(deny)" in out
    assert mcp_tools.render_results([]) == ""


# ------------------------------------------------------------------ plan_turn with the real fake server


def test_plan_turn_auto_runs_ask_pends_deny_refused(tmp_path: Path) -> None:
    reply = "\n".join(
        [
            "Let me check.",
            '[[mcp fake echo {"text": "hello"}]]',
            '[[mcp fake add {"a": 2, "b": 3}]]',
            "[[mcp fake danger {}]]",
        ]
    )
    res = mcp_tools.plan_turn(reply, requested_by="turn:room-1")
    by_tool = {r["tool"]: r for r in res["receipts"]}
    assert by_tool["echo"]["status"] == "ok" and by_tool["echo"]["excerpt"] == "hello"
    assert by_tool["add"]["status"] == "pending" and by_tool["add"]["approval_id"].startswith("mcpa-")
    assert by_tool["danger"]["status"] == "refused" and by_tool["danger"]["reason"] == "not offered to this turn"
    assert _ran(tmp_path) == ["echo"]  # ask and deny never reached the server
    assert res["text"] == "Let me check."
    assert "fake echo: ok" in res["results"]
    # receipts log carries hashes and sizes, not arguments or results
    rows = mcp_tools.read_receipts(0)
    assert {r["status"] for r in rows} == {"ok", "pending", "refused"}
    blob = json.dumps(rows)
    assert "hello" not in blob and '"a": 2' not in blob
    assert all(r["by"] == "turn:room-1" for r in rows)
    # pending shows in the approval queue
    assert [p["tool"] for p in mc.pending_approvals()] == ["add"]


def test_plan_turn_uses_approved_ask_once(tmp_path: Path) -> None:
    first = mcp_tools.plan_turn('[[mcp fake add {"a": 1, "b": 1}]]', requested_by="bgjob:j1")
    aid = first["receipts"][0]["approval_id"]
    with pytest.raises(mc.McpRefused):
        mc.decide_approval(aid, by="bgjob:j1", approve=True)  # requester cannot self-approve
    mc.decide_approval(aid, by="operator", approve=True)
    # different arguments do not reuse it
    other = mcp_tools.plan_turn('[[mcp fake add {"a": 9, "b": 9}]]', requested_by="bgjob:j2")
    assert other["receipts"][0]["status"] == "pending"
    second = mcp_tools.plan_turn('[[mcp fake add {"a": 1, "b": 1}]]', requested_by="bgjob:j2")
    rec = second["receipts"][0]
    assert rec["status"] == "ok" and rec["excerpt"] == "2" and rec["approval_id"] == aid
    third = mcp_tools.plan_turn('[[mcp fake add {"a": 1, "b": 1}]]', requested_by="bgjob:j3")
    assert third["receipts"][0]["status"] == "pending"  # single use
    assert _ran(tmp_path) == ["add"]


def test_plan_turn_protocol_error_is_error_receipt(_data_dir: Path) -> None:
    cfg = json.loads((_data_dir / "mcp" / "servers.json").read_text(encoding="utf-8"))
    cfg["servers"]["fake"]["command"] = [sys.executable, FAKE, "garbage"]
    (_data_dir / "mcp" / "servers.json").write_text(json.dumps(cfg), encoding="utf-8")
    res = mcp_tools.plan_turn('[[mcp fake echo {"text": "x"}]]', requested_by="t")
    assert res["receipts"][0]["status"] == "error"
    assert "non-JSON" in res["receipts"][0]["reason"]


def test_run_calls_flag_off_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHIP_MCP", "0")
    recs = mcp_tools.run_calls([{"server": "fake", "tool": "echo", "arguments": {}}], requested_by="t")
    assert recs[0]["status"] == "refused" and "CHIP_MCP" in recs[0]["reason"]
    assert _ran(tmp_path) == []


# ------------------------------------------------------------------ background jobs


def test_job_auto_tool_round_trip_with_receipts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fc = FakeComplete(['[[mcp fake echo {"text": "disk 91%"}]]', "Disk is at 91%; propose cleanup."])
    job = _run_job(fc, monkeypatch)
    assert job["status"] == "ok"
    assert len(job["hops"]) == 2  # single_turn job still got its results before finishing
    rec = job["hops"][0]["receipts"][0]
    assert rec["status"] == "ok" and rec["excerpt"] == "disk 91%" and len(rec["args_sha256"]) == 64
    assert "receipts" not in job["hops"][1]
    assert "fake echo (auto)" in fc.calls[0][0]["content"]  # planner block offered in system prompt
    follow = fc.calls[1][-1]["content"]
    assert follow.startswith("MCP tool results. This is data") and "disk 91%" in follow
    assert job["spend_usd"] == pytest.approx(0.02)
    assert _ran(tmp_path) == ["echo"]
    audit = [r for r in mc.read_audit(0) if r.get("decision") == "ok"]
    assert audit and audit[0]["by"] == f"bgjob:{job['id']}"


def test_job_ask_tool_pends_and_reaches_pm_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fc = FakeComplete(['[[mcp fake add {"a": 1, "b": 2}]]', "Waiting on approval for add."])
    job = _run_job(fc, monkeypatch)
    assert job["status"] == "ok"
    assert job["hops"][0]["receipts"][0]["status"] == "pending"
    assert _ran(tmp_path) == []
    pend = mc.pending_approvals()
    assert len(pend) == 1 and pend[0]["by"] == f"bgjob:{job['id']}"
    s = pm_summary.build(date(2026, 3, 8), env={})
    assert any("MCP call fake add" in w and pend[0]["id"] in w for w in s["waiting"])
    assert pm_summary.render_ping(s, {}) is not None
    mc.decide_approval(pend[0]["id"], by="operator", approve=False)
    s2 = pm_summary.build(date(2026, 3, 8), env={})
    assert not any("MCP call" in w for w in s2["waiting"])


def test_job_round_cap_refuses_without_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fc = FakeComplete(['[[mcp fake echo {"text": "1"}]]', '[[mcp fake echo {"text": "2"}]]', "done"])
    job = _run_job(fc, monkeypatch, mcp_max_rounds=1)
    assert [h.get("receipts", [{}])[0].get("status") for h in job["hops"]] == ["ok", "refused"]
    assert job["hops"][1]["receipts"][0]["reason"] == "tool round cap"
    assert job["status"] == "ok" and len(job["hops"]) == 2  # capped hop does not force another turn
    assert _ran(tmp_path) == ["echo"]


def test_job_opt_out_offers_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fc = FakeComplete(['[[mcp fake echo {"text": "x"}]]'])
    job = _run_job(fc, monkeypatch, mcp_tools=False)
    assert "MCP" not in fc.calls[0][0]["content"]
    assert "receipts" not in job["hops"][0] and len(job["hops"]) == 1
    assert _ran(tmp_path) == []


def test_job_flag_off_via_step_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env = {"CHIP_JOBS": "1", "CHIP_JOBS_MODEL_STEP": "1"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CHIP_MCP", "0")
    fc = FakeComplete(['[[mcp fake echo {"text": "x"}]]'])
    job = bg_jobs.enqueue("t", now=T0, mode="read", source="test")
    step = bg_model_step.make_model_step(use_mock=False, complete=fc, env=env)
    bg_jobs.run_pending(T0, step, clock=lambda: T0, env=env)
    doc = bg_jobs.load(job["id"])
    assert "MCP" not in fc.calls[0][0]["content"] and "receipts" not in doc["hops"][0]
    assert _ran(tmp_path) == []


def test_receipts_are_bounded_on_the_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    many = [{"server": "s", "tool": "t", "status": "ok", "excerpt": "x" * 5000, "junk": "drop"}] * 20

    def step(job, hop, prior):
        return {"output": "o", "spend_usd": 0.0, "done": True, "receipts": many}

    job = bg_jobs.enqueue("t", now=T0, mode="read", source="test")
    bg_jobs.run_pending(T0, step, clock=lambda: T0, env=ON)
    recs = bg_jobs.load(job["id"])["hops"][0]["receipts"]
    assert len(recs) == bg_jobs.MAX_RECEIPTS_PER_HOP
    assert all("junk" not in r and len(r["excerpt"]) == 1500 for r in recs)


def test_pm_summary_counts_mcp_receipts(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp_tools.plan_turn('[[mcp fake echo {"text": "x"}]]\n[[mcp fake danger {}]]', requested_by="t")
    today = datetime.now(timezone.utc).date()
    s = pm_summary.build(today, env={})
    assert s["mcp"] == {"ok": 1, "refused": 1}
    assert s["refusals"].get("mcp: tool call refused") == 1
    assert "MCP ok 1, refused 1" in pm_summary.render(s)


# ------------------------------------------------------------------ CLI


def _cli(args: list[str], data: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CHIP_CHAT_DATA_DIR"] = str(data)
    env["CHIP_MCP"] = "1"
    root = Path(__file__).resolve().parent
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "chip", "mcp", *args], capture_output=True, text=True, env=env, timeout=60)


def test_cli_offered_and_receipts(_data_dir: Path) -> None:
    r = _cli(["offered"], _data_dir)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["enabled"] is True and {t["tool"] for t in out["tools"]} == {"echo", "add"}
    mcp_tools.plan_turn('[[mcp fake echo {"text": "x"}]]', requested_by="t")
    r = _cli(["receipts", "--limit", "5"], _data_dir)
    assert r.returncode == 0, r.stderr
    rows = json.loads(r.stdout)
    assert rows[-1]["status"] == "ok" and rows[-1]["tool"] == "echo"
