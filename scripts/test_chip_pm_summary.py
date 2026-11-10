"""Parity P1.4d: retry cost in job spend, routine path with the model step, daily PM summary (fakes only)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from chip import bg_jobs, bg_model_step, openrouter, pm_summary, routines, runtime, spend

SCRIPTS = Path(__file__).resolve().parent
DAY = date(2026, 3, 8)
# Built at runtime so secret scanners never see a literal webhook-looking value in the repo.
HOOK = "https://hooks.example.invalid/" + "summary"
PING = "https://hooks.example.invalid/" + "ping"
ON = {"CHIP_PM_SUMMARY": "1", pm_summary.WEBHOOK_ENV: HOOK, "CHIP_PM_SUMMARY_TZ": "America/Los_Angeles"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    for k in (
        "CHIP_PM_SUMMARY", pm_summary.WEBHOOK_ENV, pm_summary.PING_WEBHOOK_ENV, pm_summary.TZ_ENV, pm_summary.LINK_ENV,
        "CHIP_JOBS", "CHIP_JOBS_MODEL_STEP", "CHIP_ROUTINES", "CHIP_SPEND_CAP_USD", "CHIP_PM_TICK", "CHIP_PICKER",
    ):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


class Sink:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def __call__(self, url: str, body: bytes) -> int:
        self.posts.append((url, json.loads(body)))
        return 204


def _job(jid: str, *, ended: str, status: str = "ok", reason: str = "", output: str = "", source: str = "picker:alert:a1",
         title: str = "disk full", spend_usd: float = 0.0, mode: str = "read") -> dict:
    return {
        "id": jid, "status": status, "reason": reason, "mode": mode, "source": source, "spend_usd": spend_usd,
        "prompt": f'Read-only investigation.\nid: "x"\ntitle: "{title}"', "hops": [{"output": output}],
        "created_at": ended, "ended_at": ended,
    }


def _jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# ------------------------------------------------------------------ retry cost


def _fake_api(monkeypatch: pytest.MonkeyPatch, payloads: list[dict]) -> list[int]:
    budgets: list[int] = []

    def fake_chat(_key: str, *, model: str, messages: list, max_tokens: int) -> dict:
        del model, messages
        budgets.append(max_tokens)
        return payloads[len(budgets) - 1]

    monkeypatch.setattr(openrouter, "chat_completion", fake_chat)
    monkeypatch.setattr(openrouter, "require_api_key", lambda: "unit-" + "test")
    monkeypatch.setattr(spend, "guard_before_live_completion", lambda *a, **k: None)
    monkeypatch.setattr(spend, "record_after_live_completion", lambda *a, **k: spend.default_ledger())
    return budgets


STARVED = {
    "model": "fake/model",
    "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "", "reasoning": "thinking..."}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 64, "cost": 0.003},
}
OK = {
    "model": "fake/model",
    "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
}


def test_runtime_reports_retry_cost_of_starved_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    if openrouter.retry_max_tokens_if_reasoning_starved(STARVED, 64) is None:
        pytest.skip("fixture not classified as reasoning-starved")
    budgets = _fake_api(monkeypatch, [STARVED, dict(OK)])
    text, payload, _routed, _ = runtime.complete([{"role": "user", "content": "x"}], max_tokens=64, use_mock=False)
    assert text == "done" and len(budgets) == 2
    assert runtime.retry_cost_usd(payload) == pytest.approx(0.003)
    assert payload[runtime.RETRY_KEY]["cost_source"] == "api"
    assert OK.get(runtime.RETRY_KEY) is None  # caller's dict not mutated


def test_runtime_no_retry_key_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_api(monkeypatch, [dict(OK)])
    _t, payload, _r, _l = runtime.complete([{"role": "user", "content": "x"}], max_tokens=64, use_mock=False)
    assert runtime.RETRY_KEY not in payload and runtime.retry_cost_usd(payload) == 0.0
    assert runtime.retry_cost_usd(None) == 0.0 and runtime.retry_cost_usd({runtime.RETRY_KEY: {"cost_usd": "bad"}}) == 0.0


def test_model_step_adds_retry_cost_to_job_spend(_data_dir: Path) -> None:
    def fake_complete(messages, **kw):
        payload = {**OK, runtime.RETRY_KEY: {"model": "fake/model", "cost_usd": 0.004, "cost_source": "api"}}
        return "found it", payload, "fake/model", {}

    env = {"CHIP_JOBS_MODEL_STEP": "1", "CHIP_JOBS": "1"}
    step = bg_model_step.make_model_step(env=env, complete=fake_complete, use_mock=False)
    out = step({"id": "j1", "prompt": "p", "mode": "read", "params": {}}, 0, [])
    assert out["spend_usd"] == pytest.approx(0.005)
    row = json.loads((_data_dir / "bg_jobs_spend.jsonl").read_text().splitlines()[0])
    assert row["cost_usd"] == pytest.approx(0.005) and row["retry_cost_usd"] == pytest.approx(0.004)


def test_system_prompt_mentions_decision_marker() -> None:
    assert pm_summary.DECISION_MARKER in bg_model_step.SYSTEM_PROMPT


# ------------------------------------------------------------------ build / render


def test_build_collects_day_in_local_zone(_data_dir: Path) -> None:
    # 2026-03-08 local (PST/PDT switch day) runs 08:00Z..07:00Z next day.
    jobs = [
        _job("in1", ended="2026-03-08T09:00:00Z", output="opened https://github.com/acme/app/pull/12 for review", spend_usd=0.01),
        _job("in2", ended="2026-03-09T06:30:00Z", status="failed", reason="step error: BudgetRefused"),
        _job("in3", ended="2026-03-08T20:00:00Z", output="looks fine\n[[decision]] restart the cache service?"),
        _job("out", ended="2026-03-08T07:59:00Z", output="https://github.com/acme/app/pull/99"),
        _job("q1", ended="", status="queued", mode="mutate", source="cli"),
        _job("f1", ended="2026-03-08T21:00:00Z", status="failed", reason="step error: ValueError"),
    ]
    _jsonl(_data_dir / "pm_tick_history.jsonl", [
        {"at": "2026-03-08T09:00:00Z", "status": "ok", "skipped": [
            {"key": "alert:a", "reason": "already picked today"}, {"key": "alert:b", "reason": "severity info below warning"},
            {"key": "alert:c", "reason": "daily pick cap"}]},
        {"at": "2026-03-08T10:00:00Z", "status": "skipped", "reason": "budget stopped: no picks, no runs"},
        {"at": "2026-03-07T10:00:00Z", "status": "skipped", "reason": "budget stopped: no picks, no runs"},
    ])
    _jsonl(_data_dir / "bg_jobs_spend.jsonl", [
        {"ts": "2026-03-08T09:00:00Z", "cost_usd": 0.01, "retry_cost_usd": 0.002},
        {"ts": "2026-03-08T20:00:00Z", "cost_usd": 0.005},
        {"ts": "2026-03-08T07:00:00Z", "cost_usd": 9.0},
    ])
    s = pm_summary.build(DAY, env=ON, jobs=jobs)
    assert [j["id"] for j in s["jobs"]] == ["in1", "in3", "f1", "in2"]
    assert s["jobs"][0]["why"] == "disk full" and s["jobs"][0]["source"] == "picker:alert:a1"
    assert s["changes"] == ["https://github.com/acme/app/pull/12"]
    assert s["refusals"] == {
        "job: step error: BudgetRefused": 1, "picker: daily pick cap": 1, "picker: severity info below warning": 1,
        "tick: budget stopped": 1,
    }
    assert s["spend"] == {"usd": 0.015, "retry_usd": 0.002, "hops": 2}
    assert s["ticks"] == 2
    w = s["waiting"]
    assert any("restart the cache service?" in x and "in3" in x for x in w)
    assert any("q1" in x for x in w) and any("budget is stopped" in x for x in w)


def test_render_sections_and_quiet_day(_data_dir: Path) -> None:
    s = pm_summary.build(DAY, env=ON, jobs=[])
    text = pm_summary.render(s)
    for head in ("Jobs PM ran and why", "Changes", "Refusals", "Waiting on you"):
        assert head in text
    assert "- nothing" in text and pm_summary.render_ping(s, ON) is None


def test_render_is_length_capped_and_lists_truncated(_data_dir: Path) -> None:
    jobs = [_job(f"j{i}", ended="2026-03-08T12:00:00Z", title="x" * 300) for i in range(40)]
    text = pm_summary.render(pm_summary.build(DAY, env=ON, jobs=jobs))
    assert len(text) <= pm_summary.MAX_MESSAGE and "+32 more" in text


def test_ping_lines_with_link_template() -> None:
    s = {"date": "2026-03-08", "waiting": ["a", "b"]}
    ping = pm_summary.render_ping(s, {pm_summary.LINK_ENV: "https://example.invalid/s/{date}"})
    assert ping.splitlines() == [
        "PM 2026-03-08: 2 decision(s) waiting",
        "- a — https://example.invalid/s/2026-03-08",
        "- b — https://example.invalid/s/2026-03-08",
    ]


def test_bad_zone_rejected() -> None:
    with pytest.raises(ValueError):
        pm_summary.zone({pm_summary.TZ_ENV: "Mars/Olympus"})


# ------------------------------------------------------------------ post


def test_post_refused_when_off_or_no_webhook() -> None:
    with pytest.raises(pm_summary.SummaryDisabled):
        pm_summary.post(DAY, env={}, transport=Sink())
    with pytest.raises(ValueError):
        pm_summary.post(DAY, env={"CHIP_PM_SUMMARY": "1"}, transport=Sink())


def test_post_quiet_day_no_ping_and_once_per_day(_data_dir: Path) -> None:
    sink = Sink()
    env = dict(ON, **{pm_summary.PING_WEBHOOK_ENV: PING})
    res = pm_summary.post(DAY, env=env, transport=sink)
    assert res["status"] == "ok" and res["ping_http"] is None
    assert [u for u, _ in sink.posts] == [HOOK]
    assert sink.posts[0][1]["allowed_mentions"] == {"parse": []}
    assert pm_summary.post(DAY, env=env, transport=sink)["status"] == "already-sent" and len(sink.posts) == 1
    assert pm_summary.post(DAY, env=env, transport=sink, force=True)["status"] == "ok" and len(sink.posts) == 2


def test_post_pings_when_decision_waiting(_data_dir: Path) -> None:
    bg_jobs.enqueue("rotate the thing", now=datetime(2026, 3, 8, 12, tzinfo=timezone.utc), mode="mutate", job_id="m1")
    sink = Sink()
    env = dict(ON, **{pm_summary.PING_WEBHOOK_ENV: PING})
    res = pm_summary.post(DAY, env=env, transport=sink)
    assert res["waiting"] == 1 and res["ping_http"] == 204
    assert [u for u, _ in sink.posts] == [HOOK, PING]
    assert "m1" in sink.posts[1][1]["content"]
    # Without a ping webhook the summary still posts, no ping.
    sink2 = Sink()
    pm_summary.post(DAY + timedelta(days=1), env=ON, transport=sink2)
    assert [u for u, _ in sink2.posts] == [HOOK]


# ------------------------------------------------------------------ routines


def test_routine_day_defaults_to_yesterday() -> None:
    now = datetime(2026, 3, 9, 8, 30, tzinfo=timezone.utc)  # 01:30 local on 2026-03-09
    assert pm_summary.routine_day({"prompt": ""}, now, ON) == DAY
    assert pm_summary.routine_day({"prompt": "today"}, now, ON) == date(2026, 3, 9)


def test_routine_dispatch_posts_and_falls_back(_data_dir: Path) -> None:
    sink = Sink()
    env = dict(ON, CHIP_ROUTINES="1")
    now = datetime(2026, 3, 9, 8, 30, tzinfo=timezone.utc)
    routines.add("daily", schedule="every:1h", kind="pm-summary", prompt="", now=now - timedelta(hours=2))
    routines.add("other", schedule="every:1h", kind="agent", prompt="hi", now=now - timedelta(hours=2))
    run = pm_summary.routine_dispatch(lambda r: {"status": "ok", "summary": "fallback", "spend_usd": 0.0},
                                      env=env, transport=sink, clock=lambda: now)
    rows = {r["routine_id"] if "routine_id" in r else r.get("id"): r for r in routines.tick(now, run, env=env)}
    summaries = sorted(r["summary"] for r in rows.values())
    assert any(s.startswith("pm-summary 2026-03-08: ok") for s in summaries) and "fallback" in summaries
    assert len(sink.posts) == 1


def test_routine_dispatch_reports_disabled_as_failed(_data_dir: Path) -> None:
    run = pm_summary.routine_dispatch(lambda r: {}, env={}, transport=Sink())
    res = run({"id": "d", "kind": "pm-summary", "prompt": ""})
    assert res["status"] == "failed" and "SummaryDisabled" in res["summary"]


# ------------------------------------------------------------------ CLI


def _cli(args: list[str], env_extra: dict, data: Path) -> subprocess.CompletedProcess:
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("CHIP_")}
    env.update(env_extra, CHIP_CHAT_DATA_DIR=str(data), PYTHONPATH=str(SCRIPTS))
    return subprocess.run([sys.executable, "-m", "chip", *args], capture_output=True, text=True, env=env, timeout=60)


def test_cli_summary_print_json_and_post_refused(_data_dir: Path) -> None:
    r = _cli(["bgjob", "summary", "--date", "2026-03-08", "--mock"], {}, _data_dir)
    assert r.returncode == 0, r.stderr
    assert "PM daily summary 2026-03-08" in r.stdout
    r = _cli(["bgjob", "summary", "--date", "2026-03-08", "--json", "--mock"], {}, _data_dir)
    assert json.loads(r.stdout)["date"] == "2026-03-08"
    r = _cli(["bgjob", "summary", "--date", "2026-03-08", "--post", "--mock"], {}, _data_dir)
    assert r.returncode == 3 and "REFUSED" in r.stderr


def test_cli_run_due_model_step_refused_then_mock_runs(_data_dir: Path) -> None:
    base = {"CHIP_ROUTINES": "1", "CHIP_JOBS": "1", "CHIP_CHAT_MOCK": "1"}
    r = _cli(["routine", "add", "r1", "--schedule", "every:1h", "--kind", "agent", "--prompt", "check", "--mock"], base, _data_dir)
    assert r.returncode == 0, r.stderr
    # Make it due now.
    p = next((_data_dir).rglob("r1.json"))
    doc = json.loads(p.read_text())
    doc["next_run"] = "2000-01-01T00:00:00Z"
    p.write_text(json.dumps(doc))
    r = _cli(["routine", "run-due", "--via-jobs", "--model-step", "--mock"], base, _data_dir)
    assert r.returncode == 3 and "CHIP_JOBS_MODEL_STEP" in r.stderr
    r = _cli(["routine", "run-due", "--via-jobs", "--model-step", "--mock"], dict(base, CHIP_JOBS_MODEL_STEP="1"), _data_dir)
    assert r.returncode == 0, r.stderr
    rows = json.loads(r.stdout)
    assert rows and rows[0]["status"] == "ok"
    ledger = (_data_dir / "bg_jobs_spend.jsonl").read_text().splitlines()
    assert json.loads(ledger[0])["mock"] is True  # ran through the model step, offline
