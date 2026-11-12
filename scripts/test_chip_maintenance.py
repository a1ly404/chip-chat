"""Planned maintenance window markers (chip.maintenance): offline, no docker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from chip import maintenance as mt

SCRIPTS = Path(__file__).resolve().parent
NOW = 1_800_000_000.0


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    for k in ("CHIP_BGJOB_ID", "CHIP_JOB_ACTOR", mt.PATH_ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    return {"CHIP_CHAT_DATA_DIR": str(tmp_path)}


def test_path_follows_data_dir_and_override(env: dict[str, str], tmp_path: Path) -> None:
    assert mt.marker_path(env) == tmp_path / "chip_state" / "maintenance.jsonl"
    assert mt.marker_path({mt.PATH_ENV: str(tmp_path / "m.jsonl")}) == tmp_path / "m.jsonl"


def test_start_end_closes_window(env: dict[str, str]) -> None:
    mt.start("deploy-20260308T093000", reason="deploy", now=NOW, environ=env)
    assert mt.active(now=NOW + 10, environ=env)["receipt"] == "deploy-20260308T093000"
    mt.end("deploy-20260308T093000", now=NOW + 600, environ=env)
    (w,) = mt.windows(now=NOW + 700, environ=env)
    assert (w["start"], w["end"], w["state"], w["status"]) == (NOW, NOW + 600, "closed", "ok")
    assert mt.active(now=NOW + 700, environ=env) is None
    assert mt.covering(NOW + 300, now=NOW + 700, environ=env)["receipt"] == "deploy-20260308T093000"
    assert mt.covering(NOW + 600 + mt.DEFAULT_GRACE_S - 1, now=NOW + 700, environ=env) is not None


def test_restart_outside_every_window_is_not_covered(env: dict[str, str]) -> None:
    mt.start("r1", now=NOW, environ=env)
    mt.end("r1", now=NOW + 600, environ=env)
    assert mt.covering(NOW + 600 + mt.DEFAULT_GRACE_S + 1, now=NOW + 7200, environ=env) is None
    assert mt.covering(NOW - mt.DEFAULT_GRACE_S - 1, now=NOW + 7200, environ=env) is None
    assert mt.covering(0, environ=env) is None and mt.covering("bad", environ=env) is None  # type: ignore[arg-type]


def test_window_without_end_expires_at_ttl(env: dict[str, str]) -> None:
    mt.start("r2", ttl_s=1800, now=NOW, environ=env)
    assert mt.windows(now=NOW + 60, environ=env)[0]["state"] == "open"
    (w,) = mt.windows(now=NOW + 1801, environ=env)
    assert w["state"] == "expired" and w["end"] == NOW + 1800
    assert mt.active(now=NOW + 1801, environ=env) is None
    assert mt.covering(NOW + 1800 + mt.DEFAULT_GRACE_S + 5, now=NOW + 9000, environ=env) is None


def test_ttl_is_clamped_so_a_marker_cannot_mute_forever(env: dict[str, str]) -> None:
    mt.start("r3", ttl_s=10 * 24 * 3600, now=NOW, environ=env)
    (w,) = mt.windows(now=NOW, environ=env)
    assert w["end"] == NOW + mt.MAX_TTL_S
    mt.end("r3", now=NOW + 30 * 24 * 3600, environ=env)  # a late end row cannot stretch it either
    assert mt.windows(now=NOW + 31 * 24 * 3600, environ=env)[0]["end"] == NOW + mt.MAX_TTL_S


def test_restart_marker_keeps_first_start(env: dict[str, str]) -> None:
    """A rollback restores the volume and re-marks the same receipt: the earlier start wins."""
    mt.start("rb", now=NOW, environ=env)
    mt.start("rb", now=NOW + 120, environ=env)
    assert mt.windows(now=NOW + 200, environ=env)[0]["start"] == NOW


def test_end_without_start_opens_nothing_and_bad_rows_are_skipped(env: dict[str, str]) -> None:
    mt.end("ghost", now=NOW, environ=env)
    path = mt.marker_path(env)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not json\n[1]\n" + json.dumps({"event": "start", "receipt": "x", "ts": "nan?"}) + "\n")
    assert mt.windows(now=NOW, environ=env) == []


@pytest.mark.parametrize("rid", ["", " ", "a b", "-lead", "x" * 200, "semi;colon"])
def test_bad_receipt_ids_are_refused(env: dict[str, str], rid: str) -> None:
    with pytest.raises(mt.MaintenanceError):
        mt.start(rid, environ=env)


@pytest.mark.parametrize("marker", ["CHIP_BGJOB_ID", "CHIP_JOB_ACTOR"])
def test_marker_write_refused_inside_a_job(env: dict[str, str], marker: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(mt.MaintenanceError, match="in-job refusal"):
        mt.start("r", environ={**env, marker: "job-1"})
    monkeypatch.setenv(marker, "job-1")  # a hand-built env without the marker does not get around it
    with pytest.raises(mt.MaintenanceError, match="in-job refusal"):
        mt.end("r", environ=env)
    assert not mt.marker_path(env).exists()


def test_cli_start_end_status(env: dict[str, str]) -> None:
    run_env = {k: v for k, v in os.environ.items() if k not in ("CHIP_BGJOB_ID", "CHIP_JOB_ACTOR")}
    run_env.update(env)
    run_env["PYTHONPATH"] = str(SCRIPTS) + os.pathsep + run_env.get("PYTHONPATH", "")

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-m", "chip.maintenance", *args], env=run_env,
                              capture_output=True, text=True, timeout=60)

    r = cli("start", "--receipt", "deploy-1", "--reason", "deploy", "--ttl", "900")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["event"] == "start"
    assert cli("end", "--receipt", "deploy-1", "--status", "failed").returncode == 0
    st = json.loads(cli("status").stdout)
    assert [(w["receipt"], w["state"], w["status"]) for w in st["windows"]] == [("deploy-1", "closed", "failed")]
    bad = cli("start", "--receipt", "a b")
    assert bad.returncode == 2 and "bad receipt id" in bad.stderr
    run_env["CHIP_BGJOB_ID"] = "job-1"
    assert cli("start", "--receipt", "deploy-2").returncode == 2
