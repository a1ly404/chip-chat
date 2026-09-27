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
import urllib.error
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
FEED_TIMEOUT_ENV = "CHIP_FEED_TIMEOUT_S"
WEBHOOK_ENV = "CHIP_WEBHOOK_DEV"

_POST_COOLDOWN_S = 4 * 3600  # one #chip-dev line per fingerprint per 4h
_POSTABLE_VERDICTS = ("actionable", "dead-letter")

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off", ""}


def _probe_timeout() -> float:
    try:
        return max(1.0, float(os.environ.get(FEED_TIMEOUT_ENV, "") or 5.0))
    except ValueError:
        return 5.0

_DEFAULT_TARGETS: dict[str, int] = {**DEFAULT_PORT_POLICY, **DEFAULT_PORT_BOT}


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
    """One probe receipt. Never raises: failures are structured receipt data.

    Bug fix (soak 2026-09-27): transient transport blips produced false
    actionable verdicts — retry transport errors once (HTTPError status codes
    are real app responses, never retried).
    """
    receipt: dict[str, object] = {
        "target": name,
        "host": host,
        "port": port,
    }
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=_probe_timeout()) as resp:
                raw = resp.read()
                http_status = int(getattr(resp, "status", 0))
            receipt["http_status"] = http_status
            receipt["raw"] = raw.decode("utf-8", errors="replace")
            receipt["attempts"] = attempt + 1
            try:
                receipt["json"] = json.loads(receipt["raw"])
            except json.JSONDecodeError:
                receipt["parse"] = "raw-kept"
            last_error = None
            break
        except urllib.error.HTTPError as exc:
            receipt["http_status"] = int(exc.code)
            receipt["error"] = f"HTTPError: {exc.code}"
            receipt["json"] = {}
            last_error = None
            break
        except Exception as exc:  # noqa: BLE001 — transport blip: retry once
            last_error = exc
            receipt["attempts"] = attempt + 1
            if attempt == 0:
                time.sleep(1.0)
    if last_error is not None:
        receipt["http_status"] = 0
        receipt["error"] = f"{last_error.__class__.__name__}: {last_error}"[:160]
        receipt["json"] = {}
    receipt["ts"] = datetime.now(timezone.utc).isoformat()
    return receipt


def derive_alerts(healths: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    """Synthetic alerts derived from health receipts.

    - monitoring-api: alert unless /health says status == "healthy".
    - discord-bot: alert unless status == "ok" AND loops_ok is True (the gold
      signal ENVELOPES.md predicted; it never flaps silently).
    - ONE alert per target per tick: a transport failure carries
      ``transport: <error>`` in the detail (no duplicated "<name>-probe" key —
      soak 2026-09-27: double lines amplified every real outage).
    """
    alerts: list[dict[str, object]] = []
    for name, h in healths.items():
        detail: str | None = None
        err = h.get("error")
        if err:
            detail = f"transport: {str(err)[:110]}"
        else:
            body = h.get("json") if isinstance(h.get("json"), dict) else {}
            status = str(body.get("status") or "").lower()
            if name == "monitoring-api":
                if status != "healthy":
                    detail = f"status={status or 'missing'}"
            else:
                # real payload nests the gold signal: {"standup_schedule": {"loops_ok": true, ...}}
                sched = body.get("standup_schedule") if isinstance(body.get("standup_schedule"), dict) else {}
                loops_ok = body.get("loops_ok", sched.get("loops_ok"))
                # alert unless status == "ok" AND the gold signal is True
                if loops_ok is not True or status != "ok":
                    detail = f"status={status or 'missing'} loops_ok={loops_ok}"
        if detail is not None:
            alerts.append({"key": name, "detail": detail, "ok": False})
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


def _post_state_path() -> Path:
    return config.data_dir() / "chip_state" / "feed_posts.json"


def _load_post_state() -> dict[str, float]:
    p = _post_state_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def _save_post_state(state: dict[str, float]) -> None:
    p = _post_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1), encoding="utf-8")


def _truthy(env: dict[str, str]) -> bool:
    return (env.get(FEED_ENABLED_ENV, "") or "").strip().lower() in _TRUE_VALUES


def post_alerts(
    receipt: dict[str, object],
    *,
    environ: dict[str, str] | None = None,
    webhook: Any | None = None,
    now: float | None = None,
    only_fps: set[str] | None = None,
) -> int:
    """Verdict-transition lines to #chip-dev (Ally GO 2026-09-27 routing slice).

    Law kept intact:
    - actionable/dead-letter verdicts only; dedup/flap/skip stay log-only.
    - max one line per fingerprint per POST_COOLDOWN_S (noise floor + Discord law).
    - verify-twice (soak 2026-09-27): when ``only_fps`` is given, ONLY fingerprints
      present in the PREVIOUS tick may post — single-tick transients stay silent;
      a real outage posts once, ≤1 interval late.
    - stand-down → no posts (pauses feed tell-tier; never wakes fixers either).
    - system line, no LLM spend: ``[chip-feed] <key> <verdict> (<detail>)``.
    Returns the number of posts actually sent.
    """
    env = environ if environ is not None else os.environ
    now = time.time() if now is None else now
    if not _truthy(env) or stand_down(env):
        return 0
    url = (env.get(WEBHOOK_ENV, "") or "").strip()
    if not url:
        return 0
    if webhook is None:
        from chip_relay.gateway import post_webhook

        webhook = post_webhook
    state = _load_post_state()
    posted = 0
    for verdict in receipt.get("verdicts", []):
        if verdict.get("verdict") not in _POSTABLE_VERDICTS:
            continue
        fp = monitor.fingerprint_for(str(verdict.get("key", "")), str(verdict.get("detail", "")))
        if only_fps is not None and fp not in only_fps:
            continue
        last = state.get(fp)
        if last is not None and now - float(last) < _POST_COOLDOWN_S:
            continue
        line = (
            f"[chip-feed] {verdict.get('key')} {verdict.get('verdict')} "
            f"({str(verdict.get('detail', ''))[:120]}) — dark: log-only, no recover without Ally ack"
        )
        result = webhook(url, "chip-relay", line[:1900])
        status = int(getattr(result, "status", 0))
        if 200 <= status < 300:
            state[fp] = now
            posted += 1
    if posted:
        _save_post_state(state)
    return posted


def run_feed_loop(stop_event: threading.Event, *, environ: dict[str, str] | None = None) -> None:
    """Log-only loop. First tick runs immediately; every subsequent tick runs
    after `interval` seconds. Exits when `stop_event` is set."""
    interval = _poll_interval(environ)
    _log("start", f"interval={interval:.0f}s targets={_targets(environ)} host={_env(FEED_HOST_ENV, DEFAULT_HOST)}")
    prev_alert_fps: set[str] = set()
    while not stop_event.is_set():
        started = time.time()
        try:
            receipt = tick(persist=True, environ=environ)
            verdicts = receipt.get("verdicts") or []
            _log("tick", f"{len(verdicts)} alert(s)")
            # verify-twice: only alerts that persisted from the previous tick post
            current_fps = {monitor.fingerprint_for(str(v.get("key", "")), str(v.get("detail", ""))) for v in verdicts}
            confirmed = current_fps & prev_alert_fps
            prev_alert_fps = current_fps
            post_count = post_alerts(receipt, environ=environ, only_fps=confirmed)
            if post_count:
                _log("post", f"{post_count} alert line(s) -> #chip-dev")
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
