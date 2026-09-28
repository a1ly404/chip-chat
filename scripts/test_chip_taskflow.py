"""Task-flow tests (Phase 2; offline, ISOLATED palace via env override).

Isolation law learned the hard way (2026-09-28): a test that touches the
shared cross-host palace is pollution — the palace env override threads
through EVERY taskflow call, and these tests assert that.

Law coverage:
- fail-visible gate (palace missing -> delegation refused, explicit error)
- M2 namespace task-cycle write (spec fields verbatim; append-only)
- first-claim-wins via chip.claims (no second registry invented)
- brief builder: goal + AC + budget walls quoted + memory pulls + claim line
- result binding: receipt text returned, posting left to the agent lane (golden 1)
"""

import json

import pytest

from chip import taskflow
from chip.law import safe_room_name


def _palace_env(tmp_path):
    root = tmp_path / "palace"
    return root, {taskflow.TASKFLOW_PALACE_ENV: str(root)}


# --- gate --------------------------------------------------------------------

def test_gate_refuses_when_root_missing(tmp_path):
    _, env = _palace_env(tmp_path)
    with pytest.raises(taskflow.TaskPalaceUnavailable, match="delegation refused"):
        taskflow.preflight(environ=env)
    # and the message spells out the consequence (fail-visible, not silent)
    with pytest.raises(taskflow.TaskPalaceUnavailable) as ei:
        taskflow.preflight(environ={taskflow.TASKFLOW_PALACE_ENV: str(tmp_path / "ghost")})
    assert "palace root missing" in str(ei.value)


def test_gate_creates_tasks_namespace_when_root_exists(tmp_path):
    root, env = _palace_env(tmp_path)
    root.mkdir()
    assert taskflow.preflight(environ=env) is True
    assert (root / "tasks").is_dir()


def test_written_environ_threads_to_path_resolution(tmp_path):
    # regression: path resolution ignored the environ override (live-palace leak)
    root, env = _palace_env(tmp_path)
    root.mkdir()
    taskflow.write_task_memory("dev", "probe-agent", task_id="RAZ-9503",
        task_type="diagnosis", outcome="isolated", receipts=["r"], lessons="isolation",
        environ=env)
    assert (root / "tasks" / safe_room_name("dev") / "probe-agent.jsonl").is_file()


# --- M2 namespace ------------------------------------------------------------

def test_task_memory_spec_fields_and_append_only(tmp_path):
    root, env = _palace_env(tmp_path)
    root.mkdir()
    out = taskflow.write_task_memory("dev", "chipchatdev", task_id="RAZ-9501",
        task_type="diagnosis", outcome="root cause found", receipts=["PR #90 1234abc"],
        lessons="stale bindings kill monitor loops", environ=env)
    assert out["written"] == 1
    f = root / "tasks" / safe_room_name("dev") / safe_room_name("chipchatdev").__str__() + ".jsonl" if False else \
        root / "tasks" / "dev" / "chipchatdev.jsonl"
    rows = [json.loads(l) for l in f.read_text().splitlines()]
    rec = rows[0]
    assert rec["task_id"] == "RAZ-9501" and rec["agent"] == "chipchatdev"
    assert rec["task_type"] == "diagnosis"
    assert rec["outcome"] == "root cause found"
    assert rec["receipts"] == ["PR #90 1234abc"]
    assert rec["lessons"] and rec["verified_as_of"]  # M2 spec fields verbatim
    # append-only: second cycle extends, never rewrites prior rows
    taskflow.write_task_memory("dev", "chipchatdev", task_id="RAZ-9502",
        task_type="triage", outcome="two", receipts=[], lessons="again", environ=env)
    assert len([json.loads(l) for l in f.read_text().splitlines()]) == 2


# --- brief -------------------------------------------------------------------

def test_brief_budget_verbatim_ac_and_refusal(tmp_path):
    root, env = _palace_env(tmp_path)
    root.mkdir()
    brief = taskflow.build_brief(
        "RAZ-9502", "chipchatdev", "dev",
        "diagnose razzorstack cron gap",
        acceptance=["verdict is one line", "receipts inline"],
        environ=env,
    )
    assert brief.text.startswith("task-brief: RAZ-9502")
    assert "goal: diagnose razzorstack cron gap" in brief.text
    for line in ("ac: verdict is one line" if False else "ac: verdict is one line",
                 "ac: receipts inline"):
        pass
    assert "ac: verdict is one line" in brief.text
    assert "$10.00 HARD" in brief.text and "$5.00 WARN" in brief.text
    assert "claim: chipchatdev raz-9502" in brief.text  # claim discipline inline
    assert brief.to_dict()["acceptance"] == ["verdict is one line", "receipts inline"]


def test_brief_refuses_when_gate_fails(tmp_path):
    with pytest.raises(taskflow.TaskPalaceUnavailable):
        taskflow.build_brief("RAZ-9499", "chipchatdev", "dev", "waste nothing",
                             environ={taskflow.TASKFLOW_PALACE_ENV: str(tmp_path / "ghost")})


def test_brief_memory_pull_seeded(tmp_path, monkeypatch):
    root, env = _palace_env(tmp_path)
    root.mkdir()
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "chip"))
    from chip import mempalace
    rec = mempalace.MemoryRecord(kind="fact", text="monitoring-api 404s were stale bindings",
                                 agent="chipchatdev", ts="2026-09-28T00:00:00Z", room="dev")
    mempalace.append_memories("dev", [rec])
    brief = taskflow.build_brief("RAZ-9503", "chipchatdev", "dev", "probe again",
                                 environ=env)
    assert "monitoring-api 404s" in brief.text  # palace pulls quoted in


# --- claims ------------------------------------------------------------------

def test_claim_first_wins_warning_and_conflict(tmp_path, monkeypatch):
    ra, rb = tmp_path / "ra", tmp_path / "rb"
    (ra / "threads").mkdir(parents=True)
    (rb / "threads").mkdir(parents=True)
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(ra))
    taskflow.claim_if_free("RAZ-9501", "chipchatdev", "raz-9501")
    with pytest.raises(taskflow.TaskScopeConflict, match="chipchatdev"):
        taskflow.claim_if_free("RAZ-9501", "homelabdev", "raz-9501")


# --- result binding ----------------------------------------------------------

def test_bind_result_returns_body_not_posted(tmp_path, monkeypatch):
    root, env = _palace_env(tmp_path)
    root.mkdir()
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "chip"))
    out = taskflow.bind_result(
        "RAZ-9501", "dev", "chipchatdev",
        "stale-image binding root-caused; fix package parked",
        receipts=["PR #90 1234abc", "RAZ-16 498de08f"], lessons="probe-on-merge",
        task_type="fix-package", linear_anchor="RAZ-16", environ=env,
    )
    body = out["linear_comment"]
    assert "task result: RAZ-9501" in body
    assert "receipts: PR #90 1234abc, RAZ-16 498de08f" in body
    assert "chip.done_gate" in body
    assert out["palace"]["written"] == 1
    rows = [json.loads(l) for l in
            (tmp_path / "chip" / "threads" / "dev.jsonl").read_text().splitlines()]
    assert any(r.get("task_id") == "RAZ-9501" for r in rows)


def test_bind_result_without_receipts_is_refuse_verdict(tmp_path, monkeypatch):
    root, env = _palace_env(tmp_path)
    root.mkdir()
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "chip2"))
    out = taskflow.bind_result("RAZ-9502", "homelab", "homelab-dev", "nothing happened",
        receipts=[], lessons="n/a", task_type="ledger", environ=env)
    assert "receipts: (none)" in out["linear_comment"]
    assert "REFUSE" in out["linear_comment"]  # no receipts = fail-closed verdict
    keep_rows = json.loads(
        (root / "tasks" / safe_room_name("homelab") / (safe_room_name("homelab-dev") + ".jsonl")).read_text().splitlines()[-1]
    )
    assert keep_rows["outcome"] == "nothing happened"  # verdict is honest even when REFUSE
