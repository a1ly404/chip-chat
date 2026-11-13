"""chip.disk_health: free-space levels, stable findings, status file, alert rows (fakes only)."""

from __future__ import annotations

import json
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chip import disk_health as dh

DU = namedtuple("DU", "total used free")
GIB = dh.GIB
NOW = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)


def fake_usage(table: dict[str, DU | Exception]):
    def usage(path: str):
        val = table[path]
        if isinstance(val, Exception):
            raise val
        return val
    return usage


def du(total_gib: float, free_gib: float) -> DU:
    total, free = int(total_gib * GIB), int(free_gib * GIB)
    return DU(total, total - free, free)


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    return {"CHIP_CHAT_DATA_DIR": str(tmp_path / "data")}


def test_defaults_and_paths(env: dict[str, str]) -> None:
    assert dh.thresholds({}) == {dh.WARN_PCT_ENV: 15.0, dh.WARN_GIB_ENV: 10.0,
                                 dh.PROBLEM_PCT_ENV: 5.0, dh.PROBLEM_GIB_ENV: 2.0}
    assert dh.paths(env) == [env["CHIP_CHAT_DATA_DIR"]]
    assert dh.paths({dh.PATHS_ENV: " /a, /b ,/a"}) == ["/a", "/b"]
    assert dh.paths({}) == [dh.DEFAULT_DATA_DIR]
    bad = dh.thresholds({dh.WARN_PCT_ENV: "nope", dh.WARN_GIB_ENV: "-3", dh.PROBLEM_PCT_ENV: "nan"})
    assert bad[dh.WARN_PCT_ENV] == 15.0 and bad[dh.WARN_GIB_ENV] == 10.0 and bad[dh.PROBLEM_PCT_ENV] == 5.0
    assert not dh.enabled({}) and dh.enabled({dh.ENABLED_ENV: "1"})


@pytest.mark.parametrize("total,free,want", [
    (1000, 500, dh.OK),
    (1000, 140, dh.WARN),     # 14% < 15%
    (1000, 40, dh.PROBLEM),   # 4% < 5%
    (50, 20, dh.OK),          # 40%, 20 GiB
    (50, 9, dh.WARN),         # 18% but 9 GiB < 10 GiB
    (50, 1.5, dh.PROBLEM),    # 1.5 GiB < 2 GiB
    (0, 0, dh.PROBLEM),       # nonsense total -> 0%
])
def test_levels(total: float, free: float, want: str) -> None:
    d = du(total, free)
    assert dh.level(d.free, d.total, dh.thresholds({})) == want


def test_zero_threshold_disables_a_leg() -> None:
    d = du(50, 9)
    assert dh.level(d.free, d.total, dh.thresholds({dh.WARN_GIB_ENV: "0"})) == dh.OK


def test_measure_stable_strings_and_stat_failure() -> None:
    usage = fake_usage({"/ok": du(1000, 500), "/low": du(1000, 30), "/gone": FileNotFoundError("x")})
    doc = dh.measure(["/ok", "/low", "/gone"], {}, usage=usage, now=NOW)
    assert doc["check"] == "disk" and doc["status"] == dh.PROBLEM and doc["checked_at"] == "2026-03-08T12:00:00Z"
    assert doc["problems"] == ["disk /low: free space below problem threshold (<5% or <2 GiB)"]
    assert doc["warns"] == ["disk /gone: stat failed (FileNotFoundError)"]
    assert any(n.startswith("disk /low: 30 GiB free (3%)") for n in doc["notes"])
    # findings carry no byte counts, so the watch fingerprint is stable while space drifts
    doc2 = dh.measure(["/low"], {}, usage=fake_usage({"/low": du(1000, 29)}), now=NOW)
    assert doc2["problems"] == doc["problems"]


def test_run_writes_status_file_in_watch_checks_shape(env: dict[str, str]) -> None:
    doc = dh.run(env, usage=fake_usage({env["CHIP_CHAT_DATA_DIR"]: du(1000, 120)}), now=NOW)
    path = Path(env["CHIP_CHAT_DATA_DIR"]) / "watch" / "checks" / "disk.json"
    on_disk = json.loads(path.read_text())
    assert on_disk["status"] == dh.WARN == doc["status"]
    for key in ("check", "checked_at", "status", "problems", "warns", "notes", "stale_after_s"):
        assert key in on_disk
    assert on_disk["warns"] == [f"disk {env['CHIP_CHAT_DATA_DIR']}: free space below warn threshold (<15% or <10 GiB)"]


def test_alert_rows_only_when_level_worsens(env: dict[str, str], tmp_path: Path) -> None:
    env = {**env, dh.ALERTS_ENV: str(tmp_path / "alerts" / "disk.jsonl"), dh.PATHS_ENV: "/v"}
    seq = [du(1000, 500), du(1000, 120), du(1000, 110), du(1000, 30), du(1000, 30), du(1000, 500), du(1000, 30)]
    for d in seq:
        dh.run(env, usage=fake_usage({"/v": d}), now=NOW)
    rows = [json.loads(x) for x in (tmp_path / "alerts" / "disk.jsonl").read_text().splitlines()]
    assert [r["severity"] for r in rows] == ["warning", "critical", "critical"]
    assert all(r["kind"] == "alert" and r["id"] == "disk-low:/v" and r["source"] == "disk-health" for r in rows)


def test_alert_rows_are_pm_alerts(env: dict[str, str]) -> None:
    from chip import pm_tick

    doc = dh.measure(["/v"], {}, usage=fake_usage({"/v": du(1000, 30)}), now=NOW)
    (row,) = dh.alert_rows(doc, {})
    norm = pm_tick.normalize_alert(row)
    assert norm is not None and norm["id"] == "disk-low:/v" and norm["severity"] == "critical"


def test_maybe_write_off_by_default_and_clears_own_file(env: dict[str, str]) -> None:
    usage = fake_usage({env["CHIP_CHAT_DATA_DIR"]: du(1000, 500)})
    assert dh.maybe_write(env, usage=usage, now=NOW) is None
    path = dh.status_path(env)
    assert not path.exists()
    on = {**env, dh.ENABLED_ENV: "1"}
    assert dh.maybe_write(on, usage=usage, now=NOW)["status"] == dh.OK and path.exists()
    dh.maybe_write(env, usage=usage, now=NOW)
    assert not path.exists()
    # someone else's file with that name is left alone
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"check": "other"}))
    dh.maybe_write(env, usage=usage, now=NOW)
    assert path.exists()


def test_never_raises(env: dict[str, str], tmp_path: Path) -> None:
    def boom(_p: str):
        raise RuntimeError("stat exploded")

    on = {**env, dh.ENABLED_ENV: "1"}
    doc = dh.maybe_write(on, usage=boom, now=NOW)
    assert doc["status"] == dh.WARN and "stat failed (RuntimeError)" in doc["warns"][0]
    blocker = tmp_path / "file"
    blocker.write_text("x")
    bad = {dh.ENABLED_ENV: "1", "CHIP_CHAT_DATA_DIR": str(blocker), dh.PATHS_ENV: "/v"}
    doc = dh.maybe_write(bad, usage=fake_usage({"/v": du(1000, 500)}), now=NOW)
    assert doc["status"] == dh.OK and doc.get("write_error")


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = {"CHIP_CHAT_DATA_DIR": str(tmp_path)}
    assert dh.main(["--path", str(tmp_path), "--json"], environ={**env, dh.WARN_PCT_ENV: "0",
                   dh.WARN_GIB_ENV: "0", dh.PROBLEM_PCT_ENV: "0", dh.PROBLEM_GIB_ENV: "0"}) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert dh.main(["--path", str(tmp_path / "missing")], environ=env) == 1
    assert not dh.status_path(env).exists()
    assert dh.main(["--path", str(tmp_path), "--write"], environ={**env, dh.PROBLEM_PCT_ENV: "101"}) == 2
    assert dh.status_path(env).exists()
