"""Run generated histories through the REAL skill gates and compare with the
reference model.

The harness never touches the network and never touches the live data dir:
- the skill state dir is forced to a fresh temp subdir per scenario
  (CHIP_CHAT_DATA_DIR);
- the report/webhook path is a recorder, except in ``check_mock_boundary``
  where the DEFAULT report path runs under ``CHIP_CHAT_MOCK=1`` and the live
  posting function is monkeypatched to a counting sentinel (that sentinel
  MUST stay at zero calls — the W-013 live-post regression);
- the executor default is monkeypatched to a recording sentinel in every run
  that can reach it, so no test can ever POST to homelab-executor.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from chip_relay import service_recovery_skill

from proptest import reference


class ReportRecorder:
    """Stands in for the monitoring-channel report path. Records, never posts."""

    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, environ, line):
        self.lines.append(line)
        return True


class ExecutorSentinel:
    """Stands in for the HMAC executor call. Records, never POSTs."""

    def __init__(self, policy: list):
        self.policy = policy
        self.calls: list[str] = []

    def __call__(self, base_url, script, container, secret, timeout_s):
        index = len(self.calls)
        self.calls.append(container)
        return self._ok(index), f"executor restarted {container}"

    def _ok(self, index: int) -> bool:
        kind = self.policy[0]
        if kind == "always_ok":
            return True
        if kind == "always_fail":
            return False
        if kind == "fail_first":
            return index >= int(self.policy[1])
        raise ValueError(f"unknown executor_policy: {self.policy}")


def _pm_decider_factory(policy: list):
    calls = {"n": 0}

    def decider(law, incident, environ):
        calls["n"] += 1
        kind = policy[0]
        if kind == "always_go":
            return {"go": True, "playbook": "service-recovery", "reason": "prop-go"}
        if kind == "always_refuse":
            return {"go": False, "playbook": "service-recovery", "reason": "prop-no"}
        if kind == "refuse_first":
            go = calls["n"] > int(policy[1])
            return {"go": go, "playbook": "service-recovery", "reason": "prop"}
        if kind == "fault":
            raise RuntimeError("pm fault injected by proptest")
        raise ValueError(f"unknown pm_policy: {policy}")

    return decider, calls


def _qwen_recorder():
    calls = {"n": 0}

    def runner(law, incident, learnings, environ):
        calls["n"] += 1
        return "prop run sheet"

    return runner, calls


@contextlib.contextmanager
def _isolated_skill_env(root_dir: Path):
    """Fresh skill state dir + patched law-file path; restores on exit."""
    token = uuid.uuid4().hex[:12]
    data_dir = Path(root_dir) / f"proptest-{token}"
    data_dir.mkdir(parents=True, exist_ok=True)
    old_data = os.environ.get("CHIP_CHAT_DATA_DIR")
    os.environ["CHIP_CHAT_DATA_DIR"] = str(data_dir)
    old_law_file = service_recovery_skill._law_file
    law_path = data_dir / "law.json"
    service_recovery_skill._law_file = lambda: law_path
    try:
        yield data_dir, law_path
    finally:
        service_recovery_skill._law_file = old_law_file
        if old_data is None:
            os.environ.pop("CHIP_CHAT_DATA_DIR", None)
        else:
            os.environ["CHIP_CHAT_DATA_DIR"] = old_data


@contextlib.contextmanager
def _patched(owner, attr, value):
    old = getattr(owner, attr)
    setattr(owner, attr, value)
    try:
        yield value
    finally:
        setattr(owner, attr, old)


def _law_bytes(episode: dict) -> str:
    if episode.get("law_valid_json", True):
        return json.dumps(episode["law"])
    return "{garbage not json"


def _environ_for(episode: dict) -> dict:
    env: dict[str, str] = {}
    if episode.get("secret", False):
        env["HOMELAB_HMAC_SECRET"] = "prop-test-secret"
    if episode.get("mock", False):
        env["CHIP_CHAT_MOCK"] = "1"
    return env


def _alert_for(event: dict) -> dict:
    alert = {
        "alert_id": event.get("alert_id", ""),
        "key": event.get("key", ""),
        "detail": event.get("detail", ""),
    }
    if event.get("fingerprint") is not None:
        alert["fingerprint"] = event["fingerprint"]
    return alert


def run_scenario(scenario: dict, root_dir: Path) -> dict:
    """Run the real gate pipeline over every event; collect the actual trace."""
    real: dict = {
        "statuses": [],
        "fingerprints": [],
        "executor_calls": [],
        "report_posts": 0,
        "pm_calls": 0,
        "qwen_calls": 0,
    }
    report = ReportRecorder()
    with _isolated_skill_env(root_dir) as (_data_dir, law_path):
        for ep_index, episode in enumerate(scenario["episodes"]):
            law_path.write_text(_law_bytes(episode), encoding="utf-8")
            executor = ExecutorSentinel(episode.get("executor_policy", ["always_ok"]))
            decider, pm_calls = _pm_decider_factory(episode.get("pm_policy", ["always_go"]))
            qwen, qwen_calls = _qwen_recorder()
            env = _environ_for(episode)
            for event in episode["events"]:
                now = reference.parse_dt(event["ts"])
                assert now is not None, f"event ts unparsable: {event['ts']!r}"
                out = service_recovery_skill.maybe_run(
                    _alert_for(event),
                    environ=env,
                    now=now,
                    report=report,
                    executor_runner=executor,
                    pm_decider=decider,
                    qwen_runner=qwen,
                )
                real["statuses"].append(out["status"] if out else None)
                real["fingerprints"].append(out.get("fingerprint") if out else None)
                if out and out.get("status") in ("recovery-attempted", "recovery-failed"):
                    real["executor_calls"].append(
                        {
                            "container": out["container"],
                            "failure_class": out["failure_class"],
                            "ts": event["ts"],
                            "fingerprint": out.get("fingerprint"),
                            "episode": ep_index,
                        }
                    )
            real["pm_calls"] += pm_calls["n"]
            real["qwen_calls"] += qwen_calls["n"]
    real["report_posts"] = len(report.lines)
    return real


def check_invariants(scenario: dict, real: dict) -> None:
    """Invariants checked directly on real behavior (independent of the
    reference model, so a shared misconception cannot hide a violation)."""
    flat_events = [
        (ep_index, episode, event)
        for ep_index, episode in enumerate(scenario["episodes"])
        for event in episode["events"]
    ]
    assert len(flat_events) == len(real["statuses"]), "event/trace length mismatch"

    calls = real["executor_calls"]
    # 1. zero executor calls outside the allowlist, inside suppression, or on
    #    native (missing-container) targets
    for c in calls:
        episode = scenario["episodes"][c["episode"]]
        law = episode["law"] if episode.get("law_valid_json", True) else {}
        allowlist = {str(s) for s in law.get("service_allowlist", [])}
        suppressed = {str(s) for s in law.get("suppressed_services", [])}
        assert c["container"] in allowlist, (
            f"executor called on non-allowlisted container {c['container']!r} "
            f"(episode {c['episode']}, event ts {c['ts']})"
        )
        assert c["container"] not in suppressed, (
            f"executor called on suppressed container {c['container']!r}"
        )
        assert c["failure_class"] != "missing-container", (
            f"executor called on native target {c['container']!r}"
        )

    # 2. zero duplicate attempts: the same incident fingerprint is never
    #    executed twice within the rolling 24h window
    by_fp: dict[str, list[datetime]] = {}
    for c in calls:
        fp = c["fingerprint"]
        assert fp, "executor receipt missing incident fingerprint"
        by_fp.setdefault(fp, []).append(reference.parse_dt(c["ts"]))
    for fp, stamps in by_fp.items():
        stamps.sort()
        for earlier, later in zip(stamps, stamps[1:]):
            gap = (later - earlier).total_seconds()
            assert gap > reference.WINDOW_S, (
                f"duplicate attempt for fingerprint {fp} within 24h window (gap {gap}s)"
            )

    # 3. every status is one of the documented receipt statuses (or None)
    for s in real["statuses"]:
        assert s is None or s in reference.KNOWN_STATUSES, f"unknown receipt status {s!r}"


def check_scenario(scenario: dict, root_dir: Path) -> None:
    """The invariant property: real gate pipeline behavior == reference model."""
    expected = reference.simulate(scenario)
    real = run_scenario(scenario, root_dir)
    ctx = json.dumps(scenario)[:1200]
    assert real["statuses"] == expected["statuses"], (
        f"gate pipeline diverged from reference model\n"
        f"expected statuses: {expected['statuses']}\n"
        f"actual statuses:   {real['statuses']}\nscenario: {ctx}"
    )
    assert real["fingerprints"] == expected["fingerprints"], (
        f"fingerprint attribution diverged\n"
        f"expected: {expected['fingerprints']}\nactual: {real['fingerprints']}\n"
        f"scenario: {ctx}"
    )
    assert [c["container"] for c in real["executor_calls"]] == [
        c["container"] for c in expected["executor_calls"]
    ], (
        f"executor call sequence diverged\nexpected: {expected['executor_calls']}\n"
        f"actual: {real['executor_calls']}\nscenario: {ctx}"
    )
    for key in ("report_posts", "pm_calls", "qwen_calls"):
        assert real[key] == expected[key], (
            f"{key} diverged: expected {expected[key]}, got {real[key]}\nscenario: {ctx}"
        )
    check_invariants(scenario, real)


def check_mock_boundary(scenario: dict, root_dir: Path) -> None:
    """W-013 live-post regression, as a property: with CHIP_CHAT_MOCK=1 and the
    DEFAULT report path, NO generated history may ever reach the live channel,
    and gate outcomes are otherwise unchanged."""
    from chip import monitor_watchdog

    live_posts: list[str] = []

    def _counting_post(text, *, environ=None):
        live_posts.append(str(text))
        return True, "posted"

    forced_fail = {
        "episodes": [
            {**episode, "executor_policy": ["always_fail"]}
            for episode in scenario["episodes"]
        ]
    }
    expected = reference.simulate(forced_fail)
    real: dict = {"statuses": [], "executor_calls": []}
    with _isolated_skill_env(root_dir) as (_data_dir, law_path):
        with _patched(
            monitor_watchdog, "post_to_monitoring", _counting_post
        ), _patched(
            service_recovery_skill,
            "_default_executor_restart",
            ExecutorSentinel(["always_fail"]),  # sentinel: record, never POST
        ):
            for episode in scenario["episodes"]:
                law_path.write_text(_law_bytes(episode), encoding="utf-8")
                decider, _pm = _pm_decider_factory(episode.get("pm_policy", ["always_go"]))
                qwen, _q = _qwen_recorder()
                env = _environ_for({**episode, "mock": True})  # mock forced, secret per law
                for event in episode["events"]:
                    now = reference.parse_dt(event["ts"])
                    assert now is not None
                    out = service_recovery_skill.maybe_run(
                        _alert_for(event), environ=env, now=now, report=None,
                        pm_decider=decider, qwen_runner=qwen,
                    )
                    real["statuses"].append(out["status"] if out else None)
                    if out and out.get("status") in ("recovery-attempted", "recovery-failed"):
                        real["executor_calls"].append(out["container"])
    assert live_posts == [], (
        f"mock-mode harness reached the live monitoring channel {len(live_posts)}x "
        f"(W-013 regression): {live_posts[:2]!r}"
    )
    assert real["statuses"] == expected["statuses"], (
        f"mock-mode gate outcomes diverged from reference\n"
        f"expected: {expected['statuses']}\nactual: {real['statuses']}"
    )


def check_malformed_alerts(alerts: list[dict], root_dir: Path) -> None:
    """Fail-closed property: malformed alerts never execute; the executor
    stays silent even with a permissive law and an always-go PM."""
    now = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
    with _isolated_skill_env(root_dir) as (_data_dir, law_path):
        law_path.write_text(json.dumps(reference.default_law()), encoding="utf-8")
        executor = ExecutorSentinel(["always_ok"])
        decider, _pm = _pm_decider_factory(["always_go"])
        report = ReportRecorder()
        env = {"HOMELAB_HMAC_SECRET": "prop-test-secret"}
        for alert in alerts:
            out = service_recovery_skill.maybe_run(
                {k: v for k, v in alert.items() if k != "ts"},
                environ=env, now=now, report=report,
                executor_runner=executor, pm_decider=decider, qwen_runner=lambda *a: "rs",
            )
            if out is not None:
                assert out["status"] not in ("recovery-attempted", "recovery-failed"), (
                    f"malformed alert executed: {alert!r} -> {out}"
                )
        assert executor.calls == [], f"executor ran on malformed alerts: {executor.calls}"
