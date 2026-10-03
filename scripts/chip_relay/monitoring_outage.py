"""Outage soak gate — fixes only after an alert class stays down >= 10 minutes."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Mapping

from chip import config, monitor

DEFAULT_OUTAGE_SOAK_S = 600.0
INCIDENT_GAP_RESET_S = 900.0

# Back-compat for tests importing OUTAGE_SOAK_S
OUTAGE_SOAK_S = DEFAULT_OUTAGE_SOAK_S


@dataclass(frozen=True)
class OutageSighting:
    class_key: str
    first_seen_ts: float
    last_seen_ts: float
    fix_ready: bool
    elapsed_s: float
    remaining_s: float


def _state_path():
    return config.data_dir() / "chip_state" / "monitoring_outages.json"


def _load() -> dict[str, dict]:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(state: dict[str, dict]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def alert_class_key(skill_id: str, alert: Mapping[str, str]) -> str:
    key = str(alert.get("key") or skill_id).strip() or skill_id
    detail = str(alert.get("detail") or alert.get("raw") or "")[:160]
    fp = monitor.fingerprint_for(key, detail)
    return f"{skill_id}:{fp}"


def note_outage_sighting(
    skill_id: str,
    alert: Mapping[str, str],
    *,
    now: float | None = None,
    soak_seconds: float | None = None,
) -> OutageSighting:
    """Record this alert class sighting; return whether the fix soak gate is open."""
    now = time.time() if now is None else now
    soak = DEFAULT_OUTAGE_SOAK_S if soak_seconds is None else float(soak_seconds)
    class_key = alert_class_key(skill_id, alert)
    state = _load()
    row = state.get(class_key)
    if not isinstance(row, dict):
        row = {}
    last = float(row.get("last_seen_ts") or 0)
    if last and now - last > INCIDENT_GAP_RESET_S:
        row = {}
    first = float(row.get("first_seen_ts") or 0)
    if not first:
        first = now
    row["first_seen_ts"] = first
    row["last_seen_ts"] = now
    state[class_key] = row
    _save(state)
    elapsed = max(0.0, now - first)
    fix_ready = elapsed >= soak
    remaining = max(0.0, soak - elapsed)
    return OutageSighting(
        class_key=class_key,
        first_seen_ts=first,
        last_seen_ts=now,
        fix_ready=fix_ready,
        elapsed_s=elapsed,
        remaining_s=remaining,
    )
