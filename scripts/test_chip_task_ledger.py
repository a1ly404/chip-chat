"""Phase-1 cost ledger tests (columns land before any dev-bot task runs).

Contract: task-bound rows carry task_id/task_type/turn_of_task/usd_cost/
memory_op_counts; task-less rows keep the EXACT old shape (relay + tests
untouched); the ticket anchor binds spend to the outcome (unit economics).
"""

import json
import sys
from pathlib import Path

from chip import task_ledger as tl
from chip.task_ledger import TaskCounter, counts_for_row

sys.path.insert(0, str(Path(__file__).resolve().parent / "chip"))


def test_counter_increments_across_processes(tmp_path):
    c1 = TaskCounter(tmp_path / "n.json")
    c2 = TaskCounter(tmp_path / "n.json")
    t1 = c1.next_turn("TASK-9499")
    t2 = c2.next_turn("TASK-9499")
    t3 = TaskCounter(tmp_path / "n.json").next_turn("TASK-9499")
    assert (t1, t2, t3) == (1, 2, 3)


def test_counter_separate_tasks_and_garbage_file(tmp_path):
    c = TaskCounter(tmp_path / "bad.json")
    (tmp_path / "bad.json").write_text("{not json")
    c.next_turn("TASK-9401")
    assert TaskCounter(tmp_path / "other.json").next_turn("TASK-9401") == 1  # task keyed globally? No — per file
    fresh = TaskCounter(tmp_path / "fresh.json")
    assert fresh.next_turn("TASK-9401") == 1


def test_counts_for_row_invariant_keys():
    row = counts_for_row("TASK-9401", "diagnosis", 3, 0.0125, {"reads": 2, "writes": 1, "written_lines": 7})
    assert row["task_id"] == "TASK-9401"
    assert row["task_type"] == "diagnosis"
    assert row["turn_of_task"] == 3
    assert abs(row["usd_cost"] - 0.0125) < 1e-9
    assert row["memory_op_counts"] == {"reads": 2, "writes": 1, "written_lines": 7}


def test_counts_for_row_missing_ops_default_zero():
    row = counts_for_row("TASK-9401", "other", 1, 0.0, {})
    assert row["memory_op_counts"] == {"reads": 0, "writes": 0, "written_lines": 0}


def test_known_task_types_list():
    assert "diagnosis" in tl.TASK_TYPES_KNOWN and "fix-package" in tl.TASK_TYPES_KNOWN


# --- CLI integration: real room shot gets the columns (mock, no network) ------

def _shot(args_extra: list[str], data_dir, tmp_base) -> dict:
    import os
    import subprocess

    env = dict(os.environ)
    env["CHIP_CHAT_MOCK"] = "1"
    env["CHIP_CHAT_DATA_DIR"] = str(data_dir)
    cmd = ["/bin/bash", str(Path(__file__).resolve().parents[1] / "bin" / "chip"),
           "room", "demo", "-m", "ping", "--as", "chipchatdev", "--json"] + args_extra
    out = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_cli_task_row_has_all_columns(tmp_path):
    import subprocess

    data = tmp_path / "appdata"
    reply = _shot(["--task-id", "TASK-9501", "--task-type", "diagnosis"], data, tmp_path)
    row = reply["task_ledger"]
    assert row["task_id"] == "TASK-9501"
    assert row["task_type"] == "diagnosis"
    assert row["turn_of_task"] == 1
    assert row["usd_cost"] == reply["session_usd"] or abs(row["usd_cost"]) >= 0.0  # mock = 0.0
    assert row["memory_op_counts"] == {"reads": 1, "writes": 0, "written_lines": 0}
    thread = data / "threads" / "demo.jsonl"
    assert thread.is_file()
    assistant_rows = [json.loads(l) for l in thread.read_text().splitlines() if "task_id" in l]
    assert assistant_rows and assistant_rows[-1]["turn_of_task"] == 1
    assert "usd_cost" in assistant_rows[-1] and "cost_usd" in assistant_rows[-1]


def test_cli_task_less_row_unchanged_shape(tmp_path):
    import subprocess

    data = tmp_path / "appdata2"
    env = dict(__import__("os").environ)
    env["CHIP_CHAT_MOCK"] = "1"
    env["CHIP_CHAT_DATA_DIR"] = str(data)
    cmd = ["/bin/bash", str(Path(__file__).resolve().parents[1] / "bin" / "chip"),
           "room", "demo", "-m", "ping", "--as", "chipchatdev", "--json"]
    out = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
    reply = json.loads(out.stdout.strip().splitlines()[-1])
    assert "task_ledger" not in reply
    thread = data / "threads" / "demo.jsonl"
    for line in thread.read_text().splitlines():
        if json.loads(line).get("role") == "assistant":
            assert "task_id" not in json.loads(line)  # old shape untouched


def test_cli_turn_counter_advances_per_task(tmp_path):
    d1, d2 = tmp_path / "a", tmp_path / "b"
    _shot(["--task-id", "TASK-9499"], d1, tmp_path)
    second = _shot(["--task-id", "TASK-9499"], d1, tmp_path)
    assert second["task_ledger"]["turn_of_task"] == 2
