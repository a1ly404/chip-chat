"""PM self-alert loop: the PM's own summary / picker rows never become monitoring alerts (fakes only).

Regression: a PM daily summary posted to the monitoring channel came back through the relay's
dispatch log as a keyless alert, got ``warning`` inferred from words like "failed" in its job
lines, and the picker opened a read job to investigate the PM's own summary.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chip import bg_jobs, pm_summary, pm_tick

T0 = datetime(2026, 3, 8, 9, 30, tzinfo=timezone.utc)
ON = {"CHIP_PM_TICK": "1", "CHIP_PICKER": "1", "CHIP_JOBS": "1", "CHIP_ROUTINES": "1"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    for k in (
        "CHIP_PM_TICK", "CHIP_PICKER", "CHIP_JOBS", "CHIP_ROUTINES", "CHIP_PICKER_DAILY_MAX", "CHIP_JOBS_DAILY_MAX",
        "CHIP_JOBS_READ_CONCURRENCY", "CHIP_SPEND_CAP_USD", "CHIP_SPEND_THRIFT_PCT", "CHIP_PM_ALERTS_PATH",
        "CHIP_PM_ALERTS_EXTRA_PATHS", "CHIP_PM_LINEAR", "CHIP_LINEAR_API_KEY", "CHIP_JOBS_MODEL_STEP",
    ):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def relay_text_row(text: str) -> dict:
    """A free-text channel message as the relay logs it (no key: ``{"key": "", "detail", "raw"}``)."""
    return {
        "room": "alerts", "skill_id": "", "created_skill": False,
        "alert": {"key": "", "detail": text[:200], "raw": text[:500]},
        "learning": "", "gate": "ok", "posted": True,
    }


def _summary_text() -> str:
    summary = {
        "date": "2026-03-07", "tz": "America/Vancouver", "ticks": 96,
        "jobs": [
            {"id": "job-1", "status": "ok", "source": "picker:alert:svc-down", "why": "svc down, container exited"},
            {"id": "job-2", "status": "failed", "reason": "timeout", "source": "routine:nightly", "why": "nightly failed"},
        ],
        "spend": {"usd": 0.01, "hops": 3}, "changes": [], "refusals": {}, "waiting": [],
    }
    return pm_summary.render(summary)


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_summary_text_would_have_inferred_warning() -> None:
    """Guard the regression's premise: the summary text itself matches the warning rules."""
    assert pm_tick.infer_severity(_summary_text()) == "warning"


def test_rendered_summary_row_is_not_an_alert() -> None:
    row = relay_text_row(_summary_text())
    assert pm_tick.self_generated(row)
    assert pm_tick.normalize_alert(row) is None


def test_rendered_ping_row_is_not_an_alert() -> None:
    ping = pm_summary.render_ping({"date": "2026-03-07", "waiting": ["approve queued change job job-9 (picker:alert:x)"]})
    assert ping and pm_tick.normalize_alert(relay_text_row(ping)) is None


@pytest.mark.parametrize(
    "row",
    [
        {"source": "pm-summary", "title": "PM summary", "detail": "2 jobs failed"},
        {"id": "x", "title": "svc down", "source": "picker:alert:svc-down"},
        {"alert": {"key": "svc-down", "detail": "exited", "source": "picker:linear:ENG-1"}},
        {"alert": {"key": "", "detail": "jobs failed", "raw": "jobs failed source=pm-summary"}},
        {"alert": {"key": "", "detail": "x", "raw": "job-1 crashed · source: picker:alert:x"}},
        {"title": "tick", "detail": "tick failed", "origin": "pm-tick"},
        {"title": "x down", "username": "chip-pm-summary"},
    ],
)
def test_pm_sources_are_dropped(row: dict) -> None:
    assert pm_tick.self_generated(row)
    assert pm_tick.normalize_alert(row) is None


@pytest.mark.parametrize(
    "row,sev",
    [
        ({"alert": {"key": "svc-down", "detail": "container exited", "raw": "key=svc-down | detail=container exited"}}, "warning"),
        ({"alert": {"key": "", "detail": "web container unhealthy", "raw": "web container unhealthy source=uptime"}}, "warning"),
        ({"id": "job-err", "title": "job-1 failed", "detail": "picker job job-1 failed on step 2"}, "warning"),
        ({"kind": "alert", "id": "edge", "title": "full outage on edge"}, "critical"),
        ({"alert": {"key": "", "detail": "PM daily summary was not posted: webhook down", "raw": "x"}}, "warning"),
    ],
)
def test_real_alerts_still_pass(row: dict, sev: str) -> None:
    assert pm_tick.self_generated(row) == ""
    item = pm_tick.normalize_alert(row)
    assert item is not None and item["severity"] == sev


def test_tick_opens_no_job_for_own_summary_but_does_for_real_alert(tmp_path: Path) -> None:
    src = tmp_path / "chip_state" / "monitoring_dispatch.jsonl"
    _write(
        src,
        [
            relay_text_row(_summary_text()),
            {"room": "alerts", "alert": {"key": "", "detail": "x", "raw": "x"}, "source": "pm-summary", "gate": "ok"},
            {"alert": {"key": "svc-down", "detail": "container exited", "raw": "key=svc-down | detail=container exited"}, "gate": "ok"},
        ],
    )
    stream = pm_tick.AlertStream(pm_tick.default_alerts_path({}))
    res = pm_tick.tick(T0, alerts=stream, env=ON)
    assert res["status"] == "ok"
    assert res["alerts"]["self"] == 2 and res["alerts"]["ignored"] == 2
    keys = {j["params"]["pick_key"] for j in bg_jobs.list_all()}
    assert keys == {"alert:svc-down"}


def test_multi_stream_sums_self_counter(tmp_path: Path) -> None:
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _write(a, [relay_text_row(_summary_text())])
    _write(b, [{"id": "x", "title": "x down", "source": "picker:alert:x"}, {"id": "y", "title": "y down"}])
    multi = pm_tick.MultiAlertStream([pm_tick.AlertStream(a), pm_tick.AlertStream(b, cursor_name="c2.json")])
    items, info = multi.read()
    assert [i["id"] for i in items] == ["y"] and info["self"] == 2
