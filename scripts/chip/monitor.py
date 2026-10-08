"""Noise floor (Grok round-6 #19): classify monitoring alerts before any fixer wakes.

Verdicts: actionable | flap | dedup | dead-letter | skip-key.
SKIP_KEYS = keys that false-alarm because the real service runs on a different
host profile than the probe target (split-stack false positives).
State is disk-persistent under CHIP_CHAT_DATA_DIR/monitor_state.json.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from chip import config, pack_values

SKIP_KEYS = pack_values.monitor_skip_keys()
DEDUP_WINDOW_S = 1800  # same fingerprint coalesces 30m
FLAP_WINDOW_S = 3600
FLAP_THRESHOLD = 4  # ≥4 occurrences inside window = flap
DEADLETTER_AFTER = 10  # suppressed this many times with no human reopen → dead-letter


def state_path() -> Path:
    return config.data_dir() / "monitor_state.json"


def fingerprint_for(key: str, detail: str) -> str:
    raw = f"{key.lower()}|{detail}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def load_state() -> dict:
    p = state_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_state(state: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1), encoding="utf-8")


def mark_human_reopen(fingerprint: str, state: dict | None = None) -> dict:
    """Operator saw it and acted — reset suppression counter (dead-letter escape hatch)."""
    st = state if state is not None else load_state()
    entry = st.get(fingerprint)
    if entry is not None:
        entry["suppressions"] = 0
        entry["dead"] = False
    if state is None:
        save_state(st)
    return st


def classify(
    alert: dict,
    *,
    now: float | None = None,
    state: dict | None = None,
    skip_keys: set[str] | None = None,
    persist: bool = True,
) -> tuple[str, dict]:
    """Classify one alert. Returns (verdict, updated_state).

    Verdicts:
      skip-key     — key is in SKIP_KEYS (native-profile false alert)
      flap         — ≥FLAP_THRESHOLD occurrences inside FLAP_WINDOW_S (suppress, keep counting)
      dedup        — repeat inside DEDUP_WINDOW_S (coalesce; one ticket/claim max)
      dead-letter  — suppressed DEADLETTER_AFTER times with no human reopen
      actionable   — new fingerprint or stale repeat: a fixer may claim it
    """
    now = time.time() if now is None else now
    st = state if state is not None else load_state()
    skip = SKIP_KEYS if skip_keys is None else skip_keys

    key = str(alert.get("key", "")).lower()
    fp = str(alert.get("fingerprint") or fingerprint_for(key, str(alert.get("detail", ""))))
    if key in skip:
        entry = st.setdefault(fp, {"key": key, "count": 0, "times": [], "suppressions": 0, "dead": False})
        entry["last_seen"] = now
        if persist and state is None:
            save_state(st)
        return "skip-key", st

    entry = st.setdefault(
        fp, {"key": key, "count": 0, "times": [], "suppressions": 0, "dead": False}
    )
    if entry.get("dead"):
        entry["last_seen"] = now
        if persist and state is None:
            save_state(st)
        return "dead-letter", st

    entry["count"] = entry.get("count", 0) + 1
    times = [t for t in entry.get("times", []) if now - t <= FLAP_WINDOW_S]
    times.append(now)
    entry["times"] = times[-32:]
    entry["last_seen"] = now

    last_seen = entry.get("last_seen_prev", 0)
    entry["last_seen_prev"] = now
    verdict = "actionable"
    if len(times) >= FLAP_THRESHOLD:
        verdict = "flap"
    elif last_seen and now - last_seen < DEDUP_WINDOW_S:
        verdict = "dedup"

    if verdict != "actionable":
        entry["suppressions"] = entry.get("suppressions", 0) + 1
        if entry["suppressions"] >= DEADLETTER_AFTER:
            entry["dead"] = True
            verdict = "dead-letter"

    if persist and state is None:
        save_state(st)
    return verdict, st
