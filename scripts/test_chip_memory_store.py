"""Parity P1.2: durable memory with provenance, user/room scopes and recall."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chip import memory_store as ms

T0 = datetime(2026, 3, 8, 9, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CHIP_MEMORY", raising=False)
    return tmp_path


def _rem(scope: str, text: str, minutes: int = 0, **kw):
    return ms.remember(scope, text, by="tester", source="unit", now=T0 + timedelta(minutes=minutes), **kw)


def test_flag_default_off() -> None:
    assert ms.enabled({}) is False
    assert ms.enabled({"CHIP_MEMORY": "1"}) is True


def test_remember_recall_roundtrip_across_scopes(_data_dir: Path) -> None:
    u = _rem("user", "Prefers metric units for weather reports")
    r = _rem("room:ops", "Deploys happen after the nightly backup", minutes=1)
    assert u["provenance"] == {"by": "tester", "source": "unit", "at": "2026-03-08T09:30:00Z"}
    assert u["scope"] == "user" and r["scope"] == "room:ops"
    assert (_data_dir / "memory" / "user.json").is_file()
    assert (_data_dir / "memory" / "room__ops.json").is_file()
    assert [x["id"] for x in ms.recall("weather units")] == [u["id"]]
    assert [x["id"] for x in ms.recall("when do deploys happen")] == [r["id"]]
    # Scope filter: user + this room only.
    assert ms.recall("deploys", scopes=["user"]) == []
    both = ms.recall("deploys weather", scopes=["user", "room:ops"])
    assert {x["id"] for x in both} == {u["id"], r["id"]}
    assert ms.recall("") == [] and ms.recall("zebra") == []


def test_scope_validation() -> None:
    for bad in ["", "global", "room:", "room:___", "rooms:x"]:
        with pytest.raises(ValueError):
            ms.normalize_scope(bad)
    assert ms.normalize_scope("room:../etc") == "room:___etc"


def test_forget_by_id_removes_from_recall(_data_dir: Path) -> None:
    rec = _rem("user", "Favourite editor is vim")
    assert ms.forget(rec["id"], by="tester", now=T0) is True
    assert ms.recall("editor vim") == []
    assert ms.forget(rec["id"], by="tester", now=T0) is False
    with pytest.raises(ValueError):
        ms.forget("../nope", by="tester", now=T0)
    audit = [json.loads(x) for x in (_data_dir / "memory" / "audit.jsonl").read_text().splitlines()]
    assert [a["event"] for a in audit] == ["remember", "forget"]
    assert all("text" not in a for a in audit)  # audit never keeps forgotten text


def test_duplicate_returns_existing() -> None:
    a = _rem("user", "Timezone is  UTC-7")
    b = _rem("user", "timezone is utc-7", minutes=5)
    assert a["id"] == b["id"]
    assert len(ms.list_records("user")) == 1


def test_supersede_by_key_keeps_newest() -> None:
    old = _rem("user", "Home city is Lisbon", key="home.city")
    new = _rem("user", "Home city is Porto", minutes=1, key="home.city")
    active = ms.list_records("user")
    assert [r["id"] for r in active] == [new["id"]]
    every = ms.list_records("user", include_superseded=True)
    prev = next(r for r in every if r["id"] == old["id"])
    assert prev["status"] == "superseded" and prev["superseded_by"] == new["id"]
    assert [r["id"] for r in ms.recall("home city")] == [new["id"]]
    # Same key in a different scope is independent.
    other = _rem("room:a", "Home city is Oslo", key="home.city")
    assert ms.list_records("user")[0]["id"] == new["id"]
    assert ms.list_records("room:a")[0]["id"] == other["id"]


def test_size_cap_evicts_oldest() -> None:
    ids = [_rem("user", f"note number {i} about gardening", minutes=i, max_records=3)["id"] for i in range(5)]
    assert [r["id"] for r in ms.list_records("user")] == ids[2:]


@pytest.mark.parametrize(
    "secret",
    [
        "my key is " + "sk-" + "or-v1-" + "a1b2c3d4" * 6,
        "use " + "gh" + "p_" + "Ab12" * 9,
        "password" + "= hunter2hunter2",
        "Authorization: " + "Bear" + "er " + "x" * 24,
        "-----BEGIN " + "RSA PRIVATE" + " KEY-----",
        "https://user:" + "pa55word" + "@example.com/x",
        "token " + "Zx9" + "Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0Lm9Nb8V",
    ],
)
def test_secret_shaped_text_refused(secret: str, _data_dir: Path) -> None:
    with pytest.raises(ms.MemoryRefused):
        _rem("user", secret)
    assert ms.list_records() == []


def test_hex_digests_and_ordinary_text_allowed() -> None:
    sha = "864c92123717deb4c4cdbb9eba87016a9c7c93b5"
    assert not ms.looks_secret(f"merged as {sha}")
    assert not ms.looks_secret("the api key lives in the vault, ask before rotating")
    _rem("user", f"Release merged as {sha}")


def test_input_validation() -> None:
    with pytest.raises(ms.MemoryRefused):
        _rem("user", "   ")
    with pytest.raises(ms.MemoryRefused):
        _rem("user", "x " * ms.MAX_TEXT_CHARS)
    with pytest.raises(ValueError):
        _rem("user", "ok", kind="rumour")
    with pytest.raises(ValueError):
        _rem("user", "ok", key="bad key!")
    with pytest.raises(ValueError):
        ms.remember("user", "ok", by="", source="unit", now=T0)
    with pytest.raises(ValueError):
        ms.remember("user", "ok", by="a", source="unit", now=datetime(2026, 1, 1))


def test_recall_ranking_topk_and_budget() -> None:
    a = _rem("user", "coffee", minutes=0)
    b = _rem("user", "coffee beans grinder", minutes=1)
    c = _rem("user", "coffee", minutes=2, kind="preference", key="drink")
    ranked = ms.recall("coffee beans grinder")
    assert ranked[0]["id"] == b["id"]
    assert [r["id"] for r in ranked[1:]] == [c["id"], a["id"]]  # recency breaks ties
    assert len(ms.recall("coffee", top_k=1)) == 1
    small = ms.recall("coffee beans grinder", budget_chars=12)
    assert [r["id"] for r in small] == [c["id"], a["id"]]  # long one skipped, not truncated
    assert sum(len(r["text"]) for r in small) <= 12


def test_format_recall_block() -> None:
    assert ms.format_recall_block([]) == ""
    rec = _rem("room:ops", "Backups run at night")
    block = ms.format_recall_block([rec])
    assert block.startswith("## Remembered facts (advisory")
    assert "[room:ops] Backups run at night (by tester via unit, 2026-03-08T09:30:00Z)" in block


def _cli(args: list[str], data: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "CHIP_MEMORY"}
    env["CHIP_CHAT_DATA_DIR"] = str(data)
    root = Path(__file__).resolve().parent
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "chip", "memory", *args], capture_output=True, text=True, env=env, timeout=60
    )


def test_cli_transcript(_data_dir: Path) -> None:
    r = _cli(["add", "Standup is at nine", "--by", "alex", "--source", "chat", "--mock"], _data_dir)
    assert r.returncode == 0, r.stderr
    rec = json.loads(r.stdout)
    assert rec["provenance"]["by"] == "alex" and rec["scope"] == "user"
    r = _cli(["add", "Room uses squash merges", "--scope", "room:dev", "--key", "merge.style", "--mock"], _data_dir)
    assert r.returncode == 0, r.stderr
    r = _cli(["list", "--mock"], _data_dir)
    assert len(json.loads(r.stdout)) == 2
    r = _cli(["search", "standup", "--scope", "user", "--mock"], _data_dir)
    assert [x["id"] for x in json.loads(r.stdout)] == [rec["id"]]
    r = _cli(["add", "password" + "=hunter2hunter2", "--mock"], _data_dir)
    assert r.returncode == 3 and "REFUSED" in r.stderr
    r = _cli(["add", "x", "--scope", "nope", "--mock"], _data_dir)
    assert r.returncode == 2
    r = _cli(["forget", rec["id"], "--mock"], _data_dir)
    assert r.returncode == 0 and "forgotten" in r.stdout
    r = _cli(["forget", rec["id"], "--mock"], _data_dir)
    assert r.returncode == 1
