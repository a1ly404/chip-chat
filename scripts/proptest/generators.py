"""Hypothesis strategies for chip-relay skill-gate histories.

Generators embed the interaction patterns that historically broke the gates
(the article's "make operations interact"): duplicate alert storms with
varying raw error text, floods of distinct incidents, interleaved containers,
allowlist/suppression churn mid-history (episode boundaries rewrite the law
file), clock jumps across the 24h window, native-vs-container confusion,
malformed alerts and malformed law files, and executor/PM faults injected
mid-sequence.

All strategies emit plain-JSON-able scenario dicts matching the schema in
``proptest.reference`` — a failing example can be pasted straight into a
regression test or replayed with ``proptest.harness.run_scenario``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hypothesis import strategies as st

from proptest import reference

BASE_TS = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)

# Pool layout mirrors the deploy law: allowlisted containers, one suppressed,
# native Windows services that must never be docker-restarted, unknown names.
ALLOWLIST_POOL = ["caddy", "bazarr", "ombi", "calibre-web", "flaresolverr"]
SUPPRESSED_POOL = ["wireguard"]
NATIVE_POOL = ["sonarr", "radarr", "plex"]
UNKNOWN_POOL = ["lumo-tamer", "qbittorrent", "jellyseerr"]
ALL_CONTAINERS = ALLOWLIST_POOL + SUPPRESSED_POOL + NATIVE_POOL + UNKNOWN_POOL

# Event-count spans keep examples fast while allowing long interleavings.
EVENT_COUNTS = st.integers(min_value=1, max_value=14)
EPISODE_COUNTS = st.integers(min_value=1, max_value=3)


# --- timestamps -----------------------------------------------------------

@st.composite
def _clock_walk(draw, n: int):
    """Cumulative second offsets: jitter, small backward jumps, and occasional
    forward/backward jumps that cross the rolling 24h window."""
    deltas = draw(
        st.lists(
            st.one_of(
                st.integers(min_value=1, max_value=1800),        # steady drift
                st.integers(min_value=-600, max_value=-1),       # small backward jump
                st.integers(min_value=43200, max_value=129600),  # 12h-36h jump
            ),
            min_size=n,
            max_size=n,
        )
    )
    micros = draw(st.sampled_from([0, 0, 0, 500000]))  # seconds-precision coverage
    ts, out = BASE_TS, []
    for d in deltas:
        ts = ts + timedelta(seconds=d, microseconds=micros)
        out.append(ts)
    return out


# --- alerts ----------------------------------------------------------------

@st.composite
def _failure_detail(draw, container: str):
    """Error text for a container. The class tokens decide classify_failure —
    mixed-token texts deliberately exercise the precedence order."""
    n = draw(st.integers(min_value=0, max_value=999999))
    shape = draw(
        st.sampled_from(
            [
                "daemon-error",
                "restart-timeout",
                "missing-container",
                "unknown",
                "mixed-timeout+missing",  # missing wins (checked first)
                "mixed-timeout+daemon",   # timeout wins (checked before daemon)
            ]
        )
    )
    if shape == "daemon-error":
        return f"container={container} error: 500 Server Error for url http://x (req id {n})"
    if shape == "restart-timeout":
        return f"container={container} error: restart timed out after 30s (try {n})"
    if shape == "missing-container":
        return draw(
            st.sampled_from(
                [
                    f"container={container} error: 404 Client Error: Not Found for url http://x ({n})",
                    f"container={container} error: No such container: {container}",
                ]
            )
        )
    if shape == "unknown":
        return f"container={container} weird failure code {n}"
    if shape == "mixed-timeout+missing":
        return f"container={container} error: No such container: {container} after restart timed out ({n})"
    return f"container={container} error: 500 Server Error after restart timed out ({n})"


@st.composite
def _keys(draw):
    return draw(
        st.sampled_from(
            [
                "auto-restart-failed",
                "auto-restart-failed",
                "auto-restart-failed",  # weight the valid key
                "AUTO-RESTART-FAILED",  # IGNORECASE match
                " auto-restart-failed",  # .strip() match
                "auto-restart-failed\n",  # regex $ allows trailing newline
                "auto-restart-failedx",  # near-miss
                "discord-bot",
                "plex-external",
            ]
        )
    )


@st.composite
def _alerts(draw, container=None):
    """One alert. Sometimes malformed (missing container=, junk fields)."""
    container = container or draw(st.sampled_from(ALL_CONTAINERS))
    key = draw(_keys())
    has_container_field = draw(st.booleans())
    if has_container_field:
        detail = draw(_failure_detail(container))
    else:
        detail = draw(
            st.sampled_from(
                [
                    "monitoring bot could not restart the service",
                    f"service {container} is down (no container field)",
                    "",
                    "container= error: 500 Server Error",  # empty name parses to ""
                ]
            )
        )
    alert = {
        "alert_id": f"a-{draw(st.integers(0, 10**9))}",
        "key": key,
        "detail": detail,
    }
    stub = draw(
        st.sampled_from([None, None, None, "", "fp-collide-1", "fp-collide-2"])
    )
    if stub is not None:
        alert["fingerprint"] = stub
    return {"ts": None, **alert}  # ts filled by the history builders


# --- histories (interaction patterns) --------------------------------------

@st.composite
def _storm_history(draw, n: int, container=None, classes=("daemon-error", "restart-timeout", "missing-container")):
    """Duplicate storm: SAME container + failure class, varying raw error text
    (the monitor re-pushes with docker IDs embedded). The W-013 duplicate-post
    bug lived exactly here."""
    container = container or draw(
        st.sampled_from(ALLOWLIST_POOL + NATIVE_POOL + SUPPRESSED_POOL)
    )
    cls_text = {
        "daemon-error": "500 Server Error",
        "restart-timeout": "restart timed out after 30s",
        "missing-container": "No such container: {c}",
    }[draw(st.sampled_from(classes))]
    events = []
    for i in range(n):
        detail = (
            f"container={container} error: {cls_text.format(c=container)} "
            f"(req id {draw(st.integers(0, 10**9))})"
        )
        events.append(
            {
                "ts": None,
                "alert_id": f"storm-{i}",
                "key": "auto-restart-failed",
                "detail": detail,
            }
        )
    return events


@st.composite
def _flood_history(draw, n: int):
    """Flood: distinct incidents (one per container), exercises the 3/24h cap."""
    events = []
    for i in range(n):
        container = draw(st.sampled_from(ALL_CONTAINERS))
        events.append(
            {
                "ts": None,
                "alert_id": f"flood-{i}",
                "key": "auto-restart-failed",
                "detail": f"container={container} error: 500 Server Error (req id {i})",
            }
        )
    return events


@st.composite
def _interleaved_history(draw, n: int):
    """Interleaved containers with dedup-relevant repeats: the same incident
    re-appears between other incidents, plus native confusion and junk."""
    events = []
    for i in range(n):
        if draw(st.booleans()):
            events.append(draw(_alerts()))
        else:
            c = draw(st.sampled_from(ALL_CONTAINERS))
            events.append(
                {
                    "ts": None,
                    "alert_id": f"mix-{i}",
                    "key": "auto-restart-failed",
                    "detail": f"container={c} error: 500 Server Error (req {draw(st.integers(0, 9999))})",
                }
            )
    return events


_HISTORY_SHAPES = ("storm", "flood", "interleaved", "plain")


@st.composite
def _events_for_episode(draw):
    n = draw(EVENT_COUNTS)
    shape = draw(st.sampled_from(_HISTORY_SHAPES))
    if shape == "storm":
        events = draw(_storm_history(n))
    elif shape == "flood":
        events = draw(_flood_history(n))
    elif shape == "interleaved":
        events = draw(_interleaved_history(n))
    else:
        events = [draw(_alerts()) for _ in range(n)]
    stamps = draw(_clock_walk(len(events)))
    for event, ts in zip(events, stamps):
        event["ts"] = ts.isoformat()
    return events


# --- law + policies ---------------------------------------------------------

_EXPIRY_MODES = ("never", "always-expired", "unparsable", "absent")


@st.composite
def _law(draw, event_stamps):
    mode = draw(st.sampled_from(list(_EXPIRY_MODES) + ["mid-history"] * 3))
    enabled = draw(st.sampled_from([True, True, True, False]))
    allowlist = draw(
        st.sets(
            st.sampled_from(ALLOWLIST_POOL),
            min_size=draw(st.integers(0, 3)),
            max_size=5,
        )
    )
    suppressed = draw(
        st.sets(st.sampled_from(SUPPRESSED_POOL + UNKNOWN_POOL + NATIVE_POOL), max_size=2)
    )
    law = reference.default_law(
        enabled=enabled,
        service_allowlist=sorted(allowlist),
        suppressed_services=sorted(suppressed),
        max_attempts_24h=draw(st.integers(0, 4)),
    )
    if mode == "never":
        pass  # default 2099 expiry
    elif mode == "always-expired":
        law["expires_iso"] = "2020-01-01T00:00:00Z"
    elif mode == "unparsable":
        law["expires_iso"] = "not-a-timestamp"
    elif mode == "absent":
        law.pop("expires_iso")
    else:  # mid-history: expiry lands on/next to a real event timestamp
        anchor = draw(st.sampled_from(event_stamps))
        jitter_s = draw(st.sampled_from([-1, 0, 1, 600]))
        anchor_dt = reference.parse_dt(anchor)
        assert anchor_dt is not None
        law["expires_iso"] = (anchor_dt + timedelta(seconds=jitter_s)).isoformat()
    return law


_PM_POLICIES = st.sampled_from(
    [
        ["always_go"],
        ["always_go"],
        ["always_go"],  # weight go (exercises the executor lane)
        ["always_refuse"],
        ["refuse_first", 0],
        ["refuse_first", 1],
        ["refuse_first", 3],
        ["fault"],
    ]
)

_EXECUTOR_POLICIES = st.sampled_from(
    [
        ["always_ok"],
        ["always_ok"],
        ["always_ok"],  # weight success
        ["always_fail"],
        ["fail_first", 1],
        ["fail_first", 2],
    ]
)

_QWEN_POLICIES = st.sampled_from(
    [
        ["always_ok"],
        ["always_ok"],
        ["always_ok"],  # weight the working run-sheet lane
        ["fault"],
    ]
)

# W-019 verify-before-act: None = cannot verify (proceed on alert evidence),
# True = verifiably RUNNING (stale alert — never restart), False = down.
_TARGET_RUNNING = st.sampled_from([None, None, None, True, False])


@st.composite
def scenarios(draw):
    """Full scenario: 1-3 episodes with law churn between them (allowlist and
    suppression entries reinserted/removed mid-history; secrets and mock mode
    toggled; skill state carries across episodes)."""
    episodes = []
    n_episodes = draw(EPISODE_COUNTS)
    for _ in range(n_episodes):
        events = draw(_events_for_episode())
        stamps = [e["ts"] for e in events]
        episodes.append(
            {
                "law": draw(_law(stamps)),
                "law_valid_json": draw(st.sampled_from([True, True, True, True, False])),
                "secret": draw(st.sampled_from([True, True, True, False])),
                "mock": draw(st.sampled_from([False, False, False, True])),
                "pm_policy": draw(_PM_POLICIES),
                "executor_policy": draw(_EXECUTOR_POLICIES),
                "qwen_policy": draw(_QWEN_POLICIES),
                "target_running": draw(_TARGET_RUNNING),
                "events": events,
            }
        )
    return {"episodes": episodes}


@st.composite
def _key_churn_events(draw, n: int, container: str, failure_text: str):
    """One incident, VARYING valid keys per cycle (per-cycle key churn, the
    W-019 storm shape) plus per-cycle fingerprint stubs (corroboration only)."""
    events = []
    for i in range(n):
        key = draw(
            st.sampled_from(
                [
                    "auto-restart-failed",
                    "AUTO-RESTART-FAILED",
                    " auto-restart-failed",
                    "auto-restart-failed\n",
                ]
            )
        )
        event = {
            "ts": None,
            "alert_id": f"churn-{i}",
            "key": key,
            "detail": f"container={container} error: {failure_text} (req id {i})",
        }
        stub = draw(st.sampled_from([None, None, "fp-collide-1", f"per-cycle-{i}"]))
        if stub is not None:
            event["fingerprint"] = stub
        events.append(event)
    return events


@st.composite
def key_churn_scenarios(draw):
    """W-019 regression shape: one allowlisted container, one failure class,
    key churn every cycle, inside and across the 24h window. Identity must be
    key-independent: repeats dedupe, the cap binds per container."""
    container = draw(st.sampled_from(ALLOWLIST_POOL))
    failure_text = draw(st.sampled_from(["500 Server Error", "restart timed out after 30s"]))
    n = draw(st.integers(min_value=2, max_value=10))
    events = draw(_key_churn_events(n, container, failure_text))
    stamps = draw(_clock_walk(len(events)))
    for event, ts in zip(events, stamps):
        event["ts"] = ts.isoformat()
    return {
        "episodes": [
            {
                "law": reference.default_law(
                    enabled=True,
                    service_allowlist=[container, "other-allowlisted"],
                    suppressed_services=[],
                ),
                "law_valid_json": True,
                "secret": True,
                "mock": False,
                "pm_policy": ["always_go"],
                "executor_policy": ["always_ok"],
                "qwen_policy": ["always_ok"],
                "target_running": draw(st.sampled_from([None, False])),
                "events": events,
            }
        ]
    }


@st.composite
def fault_scenarios(draw):
    """W-019 fail-closed shape: an executor-path fault (PM fault, qwen fault,
    or executor failure) injected on one key-churned incident. No restart may
    proceed on a fault; failures stay open and retry paced within caps."""
    container = draw(st.sampled_from(ALLOWLIST_POOL))
    n = draw(st.integers(min_value=1, max_value=6))
    events = draw(
        _key_churn_events(n, container, draw(st.sampled_from(["500 Server Error", "restart timed out after 30s"])))
    )
    stamps = draw(_clock_walk(len(events)))
    # keep timestamps inside one 24h window and past the backoff on retries
    base = BASE_TS + timedelta(minutes=5)
    for i, event in enumerate(events):
        event["ts"] = (base + timedelta(minutes=11 * i)).isoformat()
    fault = draw(st.sampled_from(["pm", "qwen", "executor"]))
    episode = {
        "law": reference.default_law(
            enabled=True,
            service_allowlist=[container],
            suppressed_services=[],
        ),
        "law_valid_json": True,
        "secret": True,
        "mock": False,
        "pm_policy": ["always_go"],
        "executor_policy": ["always_ok"],
        "qwen_policy": ["always_ok"],
        "target_running": None,
        "events": events,
    }
    if fault == "pm":
        episode["pm_policy"] = ["fault"]
    elif fault == "qwen":
        episode["qwen_policy"] = ["fault"]
    else:
        episode["executor_policy"] = ["always_fail"]
    return {"episodes": [episode], "_fault_kind": fault}


@st.composite
def storm_scenarios(draw):
    """Regression-shaped scenario for the W-013 duplicate-post bug: one stable
    enabling law, an allowlisted container, one failure class, a duplicate
    storm with varying error text, PM always-go, jittered timestamps."""
    container = draw(st.sampled_from(ALLOWLIST_POOL))
    n = draw(st.integers(min_value=2, max_value=8))
    # restartable classes only: the regression property asserts the ONE
    # attempt lands on the executor lane (missing-container storms die at the
    # native wall, which the pipeline property already covers)
    events = draw(_storm_history(n, container, classes=("daemon-error", "restart-timeout")))
    stamps = draw(_clock_walk(len(events)))
    for event, ts in zip(events, stamps):
        event["ts"] = ts.isoformat()
    return {
        "episodes": [
            {
                "law": reference.default_law(
                    enabled=True,
                    service_allowlist=[container, "other-allowlisted"],
                    suppressed_services=[],
                ),
                "law_valid_json": True,
                "secret": True,
                "mock": False,
                "pm_policy": ["always_go"],
                "executor_policy": ["always_ok"],
                "events": events,
            }
        ]
    }


@st.composite
def malformed_alert_lists(draw):
    """Alerts that can NEVER legally reach the executor: wrong/near-miss keys,
    missing container field, or an unparsable empty container name."""
    n = draw(st.integers(min_value=1, max_value=6))
    events = []
    for i in range(n):
        kind = draw(
            st.sampled_from(
                [
                    "wrong-key",
                    "near-miss-key",
                    "no-container-field",
                    "empty-container-name",
                ]
            )
        )
        valid_detail = f"container=caddy error: 500 Server Error (req {draw(st.integers(0, 9999))})"
        if kind == "wrong-key":
            key, detail = draw(
                st.sampled_from(["discord-bot", "plex-external", "", "monitor-x"])
            ), valid_detail
        elif kind == "near-miss-key":
            key, detail = draw(
                st.sampled_from(
                    ["auto-restart-failedx", "auto-restart-failer", "xauto-restart-failed"]
                )
            ), valid_detail
        elif kind == "no-container-field":
            key, detail = "auto-restart-failed", draw(
                st.sampled_from(
                    [
                        "monitoring bot could not restart the service",
                        "service caddy is down",
                        "",
                        "restart failed for an unnamed target",
                    ]
                )
            )
        else:  # empty-container-name: "container=" followed by a non-name char
            key, detail = "auto-restart-failed", draw(
                st.sampled_from(
                    [
                        "container= error: 500 Server Error",
                        "container= is gone",
                        "container=",
                    ]
                )
            )
        events.append(
            {
                "ts": None,
                "alert_id": f"mal-{i}",
                "key": key,
                "detail": detail,
            }
        )
    stamps = draw(_clock_walk(len(events)))
    for event, ts in zip(events, stamps):
        event["ts"] = ts.isoformat()
    return events


@st.composite
def garbage_law_scenarios(draw):
    """Scenarios where every episode's law file is garbage JSON — the skill
    must fail closed (disarmed, zero executor calls) for the whole history."""
    scenario = draw(scenarios())
    return {
        "episodes": [{**episode, "law_valid_json": False} for episode in scenario["episodes"]]
    }
