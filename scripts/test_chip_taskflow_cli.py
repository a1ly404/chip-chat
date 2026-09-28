"""chip taskflow CLI tests (Phase 2.5 item b; Lyla GO; fail-closed faces).

Subcommand style compatible with bin/chip (argparse). Faces:
brief (4 on gate fail), claim (5 on scope conflict), bind (receipt body +
memory row), gate (0/4). Isolated palace via CHIP_TASK_PALACE_DIR; isolated
data dir via CHIP_CHAT_DATA_DIR. No live LLM (mock).
"""

import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHIP = str(REPO / "bin" / "chip")


def _env(tmp_path, *, palace=True, data="chip"):
    env = dict(os.environ)
    env["CHIP_CHAT_MOCK"] = "1"
    env["CHIP_CHAT_DATA_DIR"] = str(tmp_path / data)
    env["CHIP_TASK_PALACE_DIR"] = str(tmp_path / "palace") if palace else ""
    return env


def _taskflow(args, tmp_path, *, palace=True):
    return subprocess.run(["/bin/bash", CHIP, "taskflow"] + args,
                          capture_output=True, text=True,
                          env=_env(tmp_path, palace=palace), timeout=60)


def test_gate_open_and_blocked(tmp_path):
    (tmp_path / "palace").mkdir()
    r = _taskflow(["gate"], tmp_path)
    assert r.returncode == 0 and json.loads(r.stdout)["gate"] == "open"

    r2 = _taskflow(["gate"], tmp_path, palace=False)
    # palace=False resolves the default config → the LIVE palace; assert visible, not blocked
    assert r2.returncode in (0, 4)  # either open (real config present) or 4 without config
    if r2.returncode == 4:
        assert json.loads(r2.stdout)["gate"] == "BLOCKED"


def test_gate_blocked_with_ghost_dir_is_explicit_rc4(tmp_path):
    r = _taskflow(["gate"], tmp_path, palace=False)
    env = dict(os.environ)
    env["CHIP_TASK_PALACE_DIR"] = str(tmp_path / "ghost")
    env["CHIP_CHAT_MOCK"] = "1"
    env["CHIP_CHAT_DATA_DIR"] = str(tmp_path / "chip")
    r2 = subprocess.run(["/bin/bash", CHIP, "taskflow", "gate"], capture_output=True,
                        text=True, env=env, timeout=60)
    assert r2.returncode == 4
    out = json.loads(r2.stdout)
    assert out["gate"] == "BLOCKED"
    assert "palace root missing" in out["error"]


def test_brief_emits_contract_face(tmp_path):
    (tmp_path / "palace").mkdir()
    r = _taskflow(["brief", "--task-id", "RAZ-9501", "--agent", "chipchatdev",
                   "--room", "dev", "--goal", "probe the thing", "--ac", "one|two"],
                  tmp_path)
    assert r.returncode == 0
    assert "task-brief: RAZ-9501" in r.stdout
    assert "ac: one" in r.stdout
    assert "$10.00 HARD" in r.stdout


def test_brief_gate_blocked_fails_closed(tmp_path):
    r = _taskflow(["brief", "--task-id", "RAZ-9501", "--agent", "a", "--room", "dev",
                   "--goal", "x"], tmp_path, palace=False)
    # isolated: empty palace path override means the ghost dir → refuse
    env = dict(os.environ)
    env["CHIP_CHAT_MOCK"] = "1"
    env["CHIP_TASK_PALACE_DIR"] = str(tmp_path / "ghost")
    env["CHIP_CHAT_DATA_DIR"] = str(tmp_path / "chip")
    r2 = subprocess.run(["/bin/bash", CHIP, "taskflow", "brief", "--task-id", "RAZ-9501",
                         "--agent", "a", "--room", "dev", "--goal", "x"],
                        capture_output=True, text=True, env=env, timeout=60)
    assert r2.returncode == 4
    assert "BLOCKED" in r2.stderr


def test_bind_prints_body_and_memory_path(tmp_path):
    (tmp_path / "palace").mkdir()
    r = _taskflow(["bind", "--task-id", "RAZ-9503", "--room", "dev",
                   "--agent", "chipchatdev", "--outcome", "isolated ok",
                   "--receipts", "PR #90 abc, RAZ-16 anchor",
                   "--lessons", "isolation wins", "--task-type", "diagnosis",
                   "--linear-anchor", "RAZ-16"], tmp_path)
    assert r.returncode == 0
    assert "task result: RAZ-9503" in r.stdout
    assert "receipts: PR #90 abc, RAZ-16 anchor" in r.stdout
    assert ("tasks" in r.stdout) and ("chipchatdev.jsonl" in r.stdout)
    # the memory row exists with spec fields
    f = tmp_path / "palace" / "tasks" / "dev" / "chipchatdev.jsonl"
    rec = json.loads(f.read_text().splitlines()[-1])
    assert rec["outcome"] == "isolated ok"
    assert rec["verified_as_of"]


def test_claim_then_conflict_rc5(tmp_path):
    (tmp_path / "palace").mkdir()
    r1 = _taskflow(["claim", "--room", "dev", "--agent", "chipchatdev",
                    "--scope", "raz-9501"], tmp_path)
    assert r1.returncode == 0
    r2 = _taskflow(["claim", "--room", "dev", "--agent", "chipchatdev",
                    "--scope", "raz-9501"], tmp_path)
    assert r2.returncode == 0  # same persona re-claiming own scope is fine
    r3 = _taskflow(["claim", "--room", "dev", "--agent", "homelabdev",
                    "--scope", "raz-9501"], tmp_path)
    assert r3.returncode == 5  # first-claim-wins → conflict rc
    assert "chipchatdev" in r3.stderr
