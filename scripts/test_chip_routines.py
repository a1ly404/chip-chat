"""Parity P1.1: scheduled routines with run history (mock-only, injected clock)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chip import routines

T0 = datetime(2026, 3, 8, 9, 30, tzinfo=timezone.utc)  # US DST switch day; UTC math must not care
ON = {"CHIP_ROUTINES": "1"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CHIP_ROUTINES", raising=False)
    return tmp_path


def test_crud_roundtrip(_data_dir: Path) -> None:
    doc = routines.add("daily-summary", schedule="cron:0 15 * * *", kind="summary", prompt="hi", now=T0)
    assert doc["next_run"] == "2026-03-08T15:00:00Z"
    assert (_data_dir / "routines" / "daily-summary.json").is_file()
    assert [r["id"] for r in routines.list_all()] == ["daily-summary"]
    with pytest.raises(FileExistsError):
        routines.add("daily-summary", schedule="every:1h", kind="x", now=T0)
    assert routines.set_enabled("daily-summary", False, now=T0)["enabled"] is False
    later = T0 + timedelta(days=3)
    again = routines.set_enabled("daily-summary", True, now=later)
    assert again["enabled"] is True and again["next_run"] == "2026-03-11T15:00:00Z"
    assert routines.delete("daily-summary") is True
    assert routines.delete("daily-summary") is False
    assert routines.list_all() == []


@pytest.mark.parametrize(
    "bad",
    ["", "every:30s", "every:5x", "cron:* * * *", "cron:60 * * * *", "cron:*/0 * * * *", "cron:5-1 * * * *", "hourly"],
)
def test_bad_schedules_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        routines.parse_schedule(bad)


def test_bad_ids_and_kind_rejected() -> None:
    with pytest.raises(ValueError):
        routines.add("../escape", schedule="every:1h", kind="x", now=T0)
    with pytest.raises(ValueError):
        routines.add("ok", schedule="every:1h", kind="  ", now=T0)


def test_interval_and_cron_next_after() -> None:
    assert routines.next_after("every:15m", T0) == T0 + timedelta(minutes=15)
    assert routines.next_after("every:2d", T0) == T0 + timedelta(days=2)
    assert routines.next_after("cron:*/20 * * * *", T0) == T0.replace(minute=40)
    assert routines.next_after("cron:50 * * * *", T0.replace(minute=50)) == T0.replace(hour=10, minute=50)
    # Monday 08:00 UTC; T0 is Sunday 2026-03-08.
    assert routines.next_after("cron:0 8 * * 1", T0) == datetime(2026, 3, 9, 8, 0, tzinfo=timezone.utc)
    assert routines.next_after("cron:0 8 * * 7", T0) == datetime(2026, 3, 15, 8, 0, tzinfo=timezone.utc)
    # dom + dow both restricted -> either matches (classic cron)
    assert routines.next_after("cron:0 0 1 * 2", T0) == datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)
    assert routines.next_after("cron:0 12 1-3,15 4 *", T0) == datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)


def test_naive_clock_rejected() -> None:
    with pytest.raises(ValueError):
        routines.next_after("every:1h", datetime(2026, 1, 1))


def test_non_utc_aware_clock_normalised() -> None:
    pst = timezone(timedelta(hours=-8))
    local = datetime(2026, 3, 8, 1, 30, tzinfo=pst)  # == T0
    assert routines.next_after("every:1h", local) == T0 + timedelta(hours=1)


def test_due_is_deterministic() -> None:
    routines.add("a", schedule="every:1h", kind="k", now=T0)
    routines.add("b", schedule="every:2h", kind="k", now=T0)
    routines.add("c", schedule="every:1h", kind="k", now=T0)
    routines.set_enabled("c", False, now=T0)
    assert routines.due(T0) == []
    assert [r["id"] for r in routines.due(T0 + timedelta(hours=1))] == ["a"]
    assert [r["id"] for r in routines.due(T0 + timedelta(hours=2))] == ["a", "b"]


def test_tick_refuses_when_flag_off() -> None:
    routines.add("a", schedule="every:1h", kind="k", now=T0)
    calls: list[dict] = []
    with pytest.raises(routines.RoutinesDisabled):
        routines.tick(T0 + timedelta(hours=1), calls.append, env={})
    with pytest.raises(routines.RoutinesDisabled):
        routines.tick(T0 + timedelta(hours=1), calls.append)  # process env: unset
    assert calls == [] and routines.history("a") == []


def test_tick_dispatches_and_records_history() -> None:
    routines.add("a", schedule="every:1h", kind="digest", prompt="p", now=T0)
    seen: list[dict] = []

    def runner(r: dict) -> dict:
        seen.append(r)
        return {"status": "ok", "summary": "done", "spend_usd": 0.0123}

    now = T0 + timedelta(hours=1, minutes=5)
    rows = routines.tick(now, runner, env=ON)
    assert [r["id"] for r in seen] == ["a"] and seen[0]["prompt"] == "p"
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "ok" and row["spend_usd"] == pytest.approx(0.0123)
    assert row["scheduled_for"] == "2026-03-08T10:30:00Z" and row["missed_skipped"] == 0
    assert routines.history("a") == rows
    doc = routines.load("a")
    assert doc["last_status"] == "ok" and doc["next_run"] == "2026-03-08T11:35:00Z"
    # Second tick at the same instant: nothing due, history unchanged.
    assert routines.tick(now, runner, env=ON) == []
    assert len(routines.history("a")) == 1


def test_missed_runs_catch_up_once() -> None:
    routines.add("a", schedule="cron:0 * * * *", kind="k", now=T0)
    calls: list[dict] = []
    now = T0 + timedelta(hours=5, minutes=10)  # 5 hourly slots missed
    rows = routines.tick(now, lambda r: calls.append(r) or {"status": "ok"}, env=ON)
    assert len(calls) == 1 and len(rows) == 1
    assert rows[0]["missed_skipped"] == 4
    assert routines.load("a")["next_run"] == "2026-03-08T15:00:00Z"


def test_runner_error_becomes_failed_row() -> None:
    routines.add("a", schedule="every:1h", kind="k", now=T0)
    routines.add("b", schedule="every:1h", kind="k", now=T0)

    def runner(r: dict) -> dict:
        if r["id"] == "a":
            raise RuntimeError("boom")
        return {"status": "weird"}

    rows = routines.tick(T0 + timedelta(hours=1), runner, env=ON)
    assert [(r["routine_id"], r["status"]) for r in rows] == [("a", "failed"), ("b", "failed")]
    assert "RuntimeError: boom" in rows[0]["summary"]
    assert routines.load("a")["last_status"] == "failed"


def test_history_is_append_only_and_survives_delete() -> None:
    routines.add("a", schedule="every:1h", kind="k", now=T0)
    for h in (1, 2, 3):
        routines.tick(T0 + timedelta(hours=h), routines.mock_runner, env=ON)
    assert len(routines.history("a")) == 3
    assert len(routines.history("a", limit=2)) == 2
    routines.delete("a")
    assert len(routines.history("a")) == 3


def test_injected_clock_stamps_start_end() -> None:
    routines.add("a", schedule="every:1h", kind="k", now=T0)
    ticks = iter([T0 + timedelta(hours=1, seconds=1), T0 + timedelta(hours=1, seconds=9)])
    rows = routines.tick(T0 + timedelta(hours=1), routines.mock_runner, clock=lambda: next(ticks), env=ON)
    assert rows[0]["started_at"] == "2026-03-08T10:30:01Z"
    assert rows[0]["ended_at"] == "2026-03-08T10:30:09Z"


def _cli(args: list[str], env_extra: dict[str, str], data: Path) -> subprocess.CompletedProcess:
    import os

    env = {k: v for k, v in os.environ.items() if k != "CHIP_ROUTINES"}
    env.update({"CHIP_CHAT_DATA_DIR": str(data), **env_extra})
    root = Path(__file__).resolve().parent
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "chip", "routine", *args], capture_output=True, text=True, env=env, timeout=60
    )


def test_cli_transcript(_data_dir: Path) -> None:
    r = _cli(["add", "tiny", "--schedule", "every:1m", "--kind", "digest", "--mock"], {}, _data_dir)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["id"] == "tiny"
    r = _cli(["list", "--mock"], {}, _data_dir)
    assert [x["id"] for x in json.loads(r.stdout)] == ["tiny"]
    r = _cli(["run-due", "--mock"], {}, _data_dir)
    assert r.returncode == 3 and "REFUSED" in r.stderr
    # Make it due now, then run with the flag on.
    path = _data_dir / "routines" / "tiny.json"
    doc = json.loads(path.read_text())
    doc["next_run"] = "2000-01-01T00:00:00Z"
    path.write_text(json.dumps(doc))
    r = _cli(["run-due", "--mock"], {"CHIP_ROUTINES": "1"}, _data_dir)
    assert r.returncode == 0, r.stderr
    rows = json.loads(r.stdout)
    assert rows[0]["status"] == "ok" and rows[0]["summary"].startswith("mock:")
    r = _cli(["history", "tiny", "--mock"], {}, _data_dir)
    assert len(json.loads(r.stdout)) == 1
    r = _cli(["add", "bad", "--schedule", "hourly", "--kind", "x"], {}, _data_dir)
    assert r.returncode == 2 and "unsupported schedule" in r.stderr
    r = _cli(["delete", "tiny"], {}, _data_dir)
    assert r.returncode == 0
