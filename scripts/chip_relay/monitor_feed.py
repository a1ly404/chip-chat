"""Phase-A monitoring feed (Grok go-live, ENVELOPES.md phase table).

In-container poller: reads monitoring-api + discord-bot health endpoints from
INSIDE the container (via `host.docker.internal`), derives synthetic alerts, and
pushes them through ``chip.monitor`` (noise floor) — **log-only** during Phase A.

Law (docs/ENVELOPES.md go-live table):
- Phase A is DARK: no agent turns, no delegate hops, no webhook posts, no tool
  executions. The feed only writes receipts + a snapshot under
  ``CHIP_CHAT_DATA_DIR/monitoring/`` so a later fixer phase can rebuild a wake
  payload without re-probing.
- Stand-down respected: classify still runs (state freshness), but receipts are
  tagged ``stand_down: true`` and no wake would ever fire from this module.
- Skip keys (plex/sonarr/radarr native-profile false alerts) never wake.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip import config, monitor
from chip_relay.dispatch import stand_down

DEFAULT_HOST = "host.docker.internal"
DEFAULT_PORT_POLICY = {"monitoring-api": 48082}
DEFAULT_PORT_BOT = {"discord-bot": 48080}
DEFAULT_INTERVAL_S = 300.0

FEED_ENABLED_ENV = "CHIP_FEED_ENABLED"
FEED_HOST_ENV = "CHIP_FEED_HOST"
FEED_INTERVAL_ENV = "CHIP_FEED_INTERVAL_S"
FEED_TARGETS_ENV = "CHIP_FEED_TARGETS"

_DEFAULT_TARGETS: dict[str, int] = {**DEFAULT_PORT_POLICY, **DEFAULT_PORT_BOT}
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def feed_enabled(environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return env.get(FEED_ENABLED_ENV, "").strip().lower() in _TRUE_VALUES


def _targets(environ: dict[str, str] | None = None) -> dict[str, int]:
    env = environ if environ is not None else os.environ
    out = dict(_DEFAULT_TARGETS)
    raw = (env.get(FEED_TARGETS_ENV) or "").strip()
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, _, port = part.partition("=")
            try:
                out[name.strip().lower()] = int(port)
            except ValueError:
                continue
    return out


def _poll_interval(environ: dict[str, str] | None = None) -> float:
    env = environ if environ is not None else os.environ
    raw = (env.get(FEED_INTERVAL_ENV) or "").strip()
    try:
        return max(60.0, float(raw or DEFAULT_INTERVAL_S))
    except ValueError:
        return DEFAULT_INTERVAL_S


def receipts_dir() -> Path:
    return config.data_dir() / "monitoring"


def _fetch_health(host: str, port: int, name: str) -> dict[str, object]:
    """One probe receipt. Never raises: failures are structured receipt data."""
    receipt: dict[str, object] = {
        "target": name,
        "host": host,
        "port": port,
    }
    try:
        timeout = 5
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=timeout) as resp:
            raw = resp.read()
            http_status = int(getattr(resp, "status", 0))
        receipt["http_status"] = http_status
        receipt["raw"] = raw.decode("utf-8", errors="replace")
        try:
            receipt["json"] = json.loads(receipt["raw"])
        except json.JSONDecodeError:
            receipt["parse"] = "raw-kept"
    except Exception as exc:  # noqa: BLE001 — any failure becomes a receipt field
        receipt["http_status"] = 0
        receipt["error"] = f"{exc.__class__.__name__}: {exc}"[:160]
        receipt["json"] = {}
    receipt["ts"] = datetime.now(timezone.utc).isoformat()
    return receipt


def derive_alerts(healths: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    """Synthetic alerts derived from health receipts.

    - monitoring-api: alert unless /health says status == "healthy".
    - discord-bot: alert unless status == "ok" AND loops_ok is True (the gold
      signal ENVELOPES.md predicted; it never flaps silently).
    - A probe that failed entirely (transport error) alerts on "<name>-probe".
    """
    alerts: list[dict[str, object]] = []
    for name, h in healths.items():
        body = h.get("json") if isinstance(h.get("json"), dict) else {}
        status = str(body.get("status") or "").lower()
        if name == "monitoring-api":
            if status != "healthy":
                alerts.append({"key": name, "detail": f"status={status or 'missing'}", "ok": False})
        else:
            loops_ok = body.get("loops_ok")
            # alert unless status == "ok" AND the gold signal is True
            if loops_ok is not True or status != "ok":
                alerts.append(
                    {"key": name, "detail": f"status={status or 'missing'} loops_ok={loops_ok}", "ok": False}
                )
        if h.get("error"):
            alerts.append({"key": f"{name}-probe", "detail": str(h["error"])[:120], "ok": False})
    return alerts


def classify_receipts(
    healths: dict[str, dict[str, object]],
    *,
    environ: dict[str, str] | None = None,
    persist: bool = False,
) -> dict[str, object]:
    """One feed tick: derive alerts → noise floor → log-only receipt."""
    alerts = derive_alerts(healths)
    verdicts: list[dict[str, object]] = []
    for alert in alerts:
        verdict, _ = monitor.classify(alert, persist=persist)
        verdicts.append({**alert, "verdict": verdict})
    receipts = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "stand_down": stand_down(environ),
        "results": {name: h for name, h in healths.items()},
        "verdicts": verdicts,
    }
    return receipts


def tick(*, persist: bool = False, environ: dict[str, str] | None = None) -> dict[str, object]:
    healths: dict[str, dict[str, object]] = {}
    host = _env(FEED_HOST_ENV, DEFAULT_HOST)
    for name, port in _targets(environ).items():
        healths[name] = _fetch_health(host, port, name)
    receipt = classify_receipts(healths, environ=environ, persist=persist)
    save_snapshot(receipt)
    return receipt


def _log(label: str, body: str) -> None:
    print(f"[chip-feeder] {label} {body}", flush=True)


def snapshot_path() -> Path:
    return receipts_dir() / "latest.json"


def save_snapshot(receipt: dict[str, object]) -> None:
    d = receipts_dir()
    d.mkdir(parents=True, exist_ok=True)
    snapshot_path().write_text(json.dumps(receipt, indent=1), encoding="utf-8")


def run_feed_loop(stop_event: threading.Event, *, environ: dict[str, str] | None = None) -> None:
    """Log-only loop. First tick runs immediately; every subsequent tick runs
    after `interval` seconds. Exits when `stop_event` is set."""
    interval = _poll_interval(environ)
    _log("start", f"interval={interval:.0f}s targets={_targets(environ)} host={_env(FEED_HOST_ENV, DEFAULT_HOST)}")
    while not stop_event.is_set():
        started = time.time()
        try:
            tick(persist=True, environ=environ)
            _log("tick", f"interval elapsed step ok")
        except Exception as exc:  # noqa: BLE001 — log-only; never crash the gateway
            _log("error", f"feed tick failed: {exc.__class__.__name__}: {exc}"[:200])
        elapsed = time.time() - started
        stop_event.wait(max(5.0, interval - elapsed))


def start_feed_polling(environ: dict[str, str] | None = None) -> threading.Thread | None:
    """Spawn the dark log-only feed thread. Never raises; returns None when the
    feed is disabled by env. Startup must not block on network."""
    if not feed_enabled(environ):
        return None
    stop_event = threading.Event()
    thread = threading.Thread(
        target=run_feed_loop,
        args=(stop_event,),
        kwargs={"environ": environ},
        name="chip-relay-feed",
        daemon=True,
    )
    thread.start()
    return thread
