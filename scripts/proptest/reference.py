"""Reference model of the service-recovery skill gate pipeline.

A plain-Python, dependency-light simulation of what the REAL gates in
``chip_relay.service_recovery_skill.maybe_run`` are documented to do (the
docstring's gate order is the law: target -> expiry -> enabled -> suppression
-> allowlist -> native -> one-per-incident -> 24h cap -> secret -> PM
go/no-go -> action). The property tests in ``scripts/test_property_gates.py``
run generated histories through BOTH this model and the real gate code and
require the action traces to match exactly.

The model is deliberately dumb: no locks, no files, no JSON round-trips —
just a dict of attempted fingerprints and the gate rules. Where the real
implementation has a quirk the law depends on, the model mirrors it and
notes it inline:

- dedup is checked against the PERSISTED fingerprint set BEFORE the rolling
  window is pruned, so an incident fingerprint older than 24h still blocks a
  repeat until some later successful attempt saves a pruned state;
- attempt stamps are persisted at seconds precision, so microseconds vanish;
- unparsable/absent expiry means expired (fail-closed), and the expiry gate
  runs BEFORE the enabled gate, so a malformed law yields ``disarmed``, not
  ``disabled``;
- the disarm notice is posted once per state-file lifetime, not per event.

Scenario schema (plain dicts so Hypothesis shrinks and prints them, and so a
failing example can be dumped as JSON — see proptest/README.md):

.. code-block:: python

    scenario = {
        "episodes": [
            {
                "law": {                       # written to the law JSON file
                    "enabled": True,
                    "expires_iso": "2027-01-01T00:00:00+00:00",
                    "max_attempts_24h": 3,
                    "service_allowlist": ["caddy"],
                    "suppressed_services": [],
                },
                "law_valid_json": True,        # False = garbage file (fail-closed)
                "secret": True,                # HOMELAB_HMAC_SECRET in environ
                "mock": False,                 # CHIP_CHAT_MOCK in environ
                "pm_policy": ["always_go"],    # or ["always_refuse"] /
                                               #     ["refuse_first", k] / ["fault"]
                "executor_policy": ["always_ok"],  # or ["always_fail"] /
                                                   #    ["fail_first", k]
                "events": [
                    {"ts": "2026-10-05T00:00:00+00:00",
                     "alert_id": "a1",
                     "key": "auto-restart-failed",
                     "detail": "container=caddy error: 500 Server Error",
                     "fingerprint": None},
                ],
            },
        ]
    }

``simulate`` returns a trace with the same shape as the real harness in
``proptest.harness``: statuses per event (None = not our alert), executor
calls, report post count, PM/qwen call counts, and fingerprints.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

TARGET_KEY_RE = re.compile(r"^auto-restart-failed$", re.IGNORECASE)
CONTAINER_RE = re.compile(r"container=([A-Za-z0-9_.-]+)")
WINDOW_S = 86400

# Receipt statuses the real skill can return (None = alert not ours).
KNOWN_STATUSES = frozenset(
    {
        "disarmed",
        "disabled",
        "suppressed",
        "not-a-container",
        "not-allowlisted",
        "incident-repeat-skipped",
        "cap-reached",
        "no-credentials",
        "pm-refused",
        "recovery-attempted",
        "recovery-failed",
    }
)


def parse_dt(iso: str) -> datetime | None:
    """Mirror the skill's parse_expires: Z-suffix ok, naive -> UTC, junk -> None."""
    raw = (iso or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_target_alert(key: str, detail: str) -> bool:
    return bool(TARGET_KEY_RE.match((key or "").strip())) and bool(
        CONTAINER_RE.search(detail or "")
    )


def classify_failure(detail: str) -> str:
    """Docker error classification; precedence is the law (missing > timeout > daemon)."""
    text = (detail or "").lower()
    if "no such container" in text or "not found" in text:
        return "missing-container"
    if "timed out" in text or "timeout" in text:
        return "restart-timeout"
    if "500" in text or "server error" in text:
        return "daemon-error"
    return "unknown"


def incident_fingerprint(key: str, container: str, failure_class: str) -> str:
    """W-013 identity: container + class, never the raw (varying) error text."""
    raw = f"{(key or '').lower()}|{container}|{failure_class}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _seconds_iso(now: datetime) -> str:
    """The skill persists attempt stamps at seconds precision."""
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def _attempt_ts(iso: str, now: datetime) -> float:
    dt = parse_dt(iso)
    if dt is None:
        return now.timestamp()  # unparsable persisted stamp -> kept (mirrors impl)
    return dt.timestamp()


def _pm_go(policy: list, pm_index: int) -> bool:
    """pm_index = 0-based count of PM calls already made this episode.
    ``refuse_first`` k refuses the first k PM calls (1-based), then goes."""
    kind = policy[0]
    if kind == "always_go":
        return True
    if kind == "always_refuse":
        return False
    if kind == "refuse_first":
        return (pm_index + 1) > int(policy[1])
    if kind == "fault":
        return False  # a faulting decider is a no-go (fail closed, mirrors impl)
    raise ValueError(f"unknown pm_policy: {policy}")


def _executor_ok(policy: list, executor_index: int) -> bool:
    kind = policy[0]
    if kind == "always_ok":
        return True
    if kind == "always_fail":
        return False
    if kind == "fail_first":
        return executor_index >= int(policy[1])
    raise ValueError(f"unknown executor_policy: {policy}")


def _effective_law(episode: dict) -> dict:
    """Mirror law_config's key defaults for a VALID law file (keys may be missing)."""
    law = dict(episode["law"]) if episode.get("law_valid_json", True) else {}
    # env overrides are never set by the harness; the file is the single SoT
    law["max_attempts_24h"] = int(law.get("max_attempts_24h", 3))
    law["service_allowlist"] = [str(s) for s in law.get("service_allowlist", [])]
    law["suppressed_services"] = [str(s) for s in law.get("suppressed_services", [])]
    return law


def simulate(scenario: dict) -> dict:
    """Expected behavior trace for the whole scenario (all episodes, in order)."""
    trace: dict = {
        "statuses": [],
        "fingerprints": [],
        "executor_calls": [],  # {container, failure_class, ts, fingerprint}
        "report_posts": 0,
        "pm_calls": 0,
        "qwen_calls": 0,
    }
    disk_fps: dict[str, str] = {}  # persisted state.json: fp -> iso stamp
    disarm_notice_posted = False

    for ep_idx, episode in enumerate(scenario["episodes"]):
        law = _effective_law(episode)
        expires_dt = parse_dt(str(law.get("expires_iso", "")))
        allowlist = set(law["service_allowlist"])
        suppressed = set(law["suppressed_services"])
        cap = law["max_attempts_24h"]
        env_secret = bool(episode.get("secret", False))
        episode_executor_calls = 0
        episode_pm_calls = 0

        for event_index, event in enumerate(episode["events"]):
            now = parse_dt(event["ts"])
            assert now is not None, f"event ts unparsable: {event['ts']!r}"
            key = str(event.get("key", ""))
            detail = str(event.get("detail", ""))

            pm_calls_before = trace["pm_calls"]
            status, fp, acted = _simulate_event(
                event=event, key=key, detail=detail, now=now,
                law=law, expires_dt=expires_dt, allowlist=allowlist,
                suppressed=suppressed, cap=cap, env_secret=env_secret,
                episode=episode, event_index=event_index,
                executor_index=episode_executor_calls, disk_fps=disk_fps,
                trace=trace, disarm_notice_posted=disarm_notice_posted,
                episode_index=ep_idx, pm_index=episode_pm_calls,
            )
            if acted == "disarm-notice":
                disarm_notice_posted = True
            elif acted == "executor":
                episode_executor_calls += 1
            if trace["pm_calls"] > pm_calls_before:
                episode_pm_calls += 1  # gate 9 was reached (go or refuse)
            trace["statuses"].append(status)
            trace["fingerprints"].append(fp)
    return trace


def _simulate_event(
    *,
    event: dict,
    key: str,
    detail: str,
    now: datetime,
    law: dict,
    expires_dt: datetime | None,
    allowlist: set,
    suppressed: set,
    cap: int,
    env_secret: bool,
    episode: dict,
    event_index: int,
    executor_index: int,
    disk_fps: dict,
    trace: dict,
    disarm_notice_posted: bool,
    episode_index: int,
    pm_index: int,
):
    """One maybe_run call. Returns (status, fingerprint-or-None, side_effect)."""
    if not is_target_alert(key, detail):
        return None, None, None

    container_m = CONTAINER_RE.search(detail)
    container = container_m.group(1) if container_m else ""
    failure_class = classify_failure(detail)
    stub = event.get("fingerprint")
    fp = str(stub) if stub else incident_fingerprint(key, container, failure_class)

    # gate 1: expiry (before ANY action; unparsable/absent = expired, fail-closed)
    if expires_dt is None or now >= expires_dt:
        if not disarm_notice_posted:
            trace["report_posts"] += 1
            return "disarmed", None, "disarm-notice"
        return "disarmed", None, None  # save_state happens; content unchanged

    # gate 2: enabled
    if not law.get("enabled", False):
        return "disabled", None, None

    # gate 3: suppression wall
    if container in suppressed:
        return "suppressed", None, None

    # gate 4: native wall (missing-container never reaches the executor)
    if failure_class == "missing-container":
        return "not-a-container", None, None

    # gate 5: allowlist wall
    if container not in allowlist:
        return "not-allowlisted", None, None

    # gate 6: one attempt per incident fingerprint — checked against the
    # PERSISTED set, BEFORE any window pruning (impl quirk the law depends on)
    if fp in disk_fps:
        return "incident-repeat-skipped", None, None

    # gate 7: rolling 24h cap — prune the working copy first; the pruned set
    # is only persisted if an attempt actually happens below
    cutoff = now.timestamp() - WINDOW_S
    pruned = {k: v for k, v in disk_fps.items() if _attempt_ts(v, now) >= cutoff}
    if len(pruned) >= cap:
        trace["report_posts"] += 1
        return "cap-reached", None, None

    # gate 8: executor credential (fail closed)
    if not env_secret:
        return "no-credentials", None, None

    # gate 9: PM go/no-go (fail closed on no-go or decider fault)
    trace["pm_calls"] += 1
    if not _pm_go(episode.get("pm_policy", ["always_go"]), pm_index):
        return "pm-refused", None, None

    # action: qwen run-sheet + the single executor restart
    trace["qwen_calls"] += 1
    ok = _executor_ok(episode.get("executor_policy", ["always_ok"]), executor_index)
    trace["executor_calls"].append(
        {
            "container": container,
            "failure_class": failure_class,
            "ts": event["ts"],
            "fingerprint": fp,
            "episode": episode_index,
        }
    )
    trace["report_posts"] += 1
    disk_fps.clear()
    disk_fps.update(pruned)
    disk_fps[fp] = _seconds_iso(now)
    status = "recovery-attempted" if ok else "recovery-failed"
    return status, fp, "executor"


def default_law(**overrides) -> dict:
    law = {
        "skill_id": "service-recovery",
        "enabled": True,
        "expires_iso": "2099-01-01T00:00:00+00:00",
        "max_attempts_24h": 3,
        "service_allowlist": ["caddy", "bazarr", "ombi"],
        "suppressed_services": ["wireguard"],
        "executor_base_url": "http://homelab-executor:48100",
        "executor_script": "docker_restart_container",
    }
    law.update(overrides)
    return law
