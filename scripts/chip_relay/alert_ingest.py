"""RAZ-332 Step 3 — monitoring-alert ingest + SHADOW dispatcher (chip lane).

Endpoint: POST /alert on the relay health server (bearer CHIEF env
``CHIP_ALERT_INGEST_TOKEN``). Alert shape: ``{"alert_id": str, "key": str,
"detail": str}`` — same fields the chip-feed derives, so monitoring-api /
feed-side producers can route here unchanged.

LAW (Lyla GO 2026-09-28):
- SHADOW MODE for the first 3 days: dispatch decisions are LOGGED ONLY —
  no auto-responses, no skill execution, no runbook calls (id:auto-heal
  graduation bar applies later, via the registry, never from this module).
- Unmatched alerts are NEVER silently dropped: one #monitoring-logs banner
  line per alert (posted via CHIP_WEBHOOK_MONITORING when set, log-only
  otherwise) labeled ``UNMATCHED-INGEST`` at most once per fingerprint per
  cooldown (existing post law family).
- Dispatch log = append-only JSONL: ``ts, alert_id, key, skill_selected,
  matched/unmatched, shadow: true`` — the record a later graduated phase
  replays to prove what would have fired (verify-before-act for any future
  auto tier).

Route table (docs/MONITOR_CASES case classes; unmatched fail-closed):
  stage1-advisor        -> parks homelab (detect-only; no runner)
  pgs-zero-production   -> standby skill absent -> matched=False (banner)
  standup:{lane}        -> spec task 3 emitter handles; chip lane route
  correlated-dark       -> tell-Ally-once (already the emit path)
  internal-loop-degrade -> monitoring-api-internal-loop (RAZ-330 card; propose)
  gold-signal-down      -> discord-bot-loop-restart (registry propose)
  status-drift          -> re-run-health-probe (registry auto candidate)
  suppression-saturation-> clear-stale-suppress (registry auto candidate)
  skill-runner-fault    -> skill-runner-restart (registry propose)
Unmatched anything else: banner + log (no registry guess) — fail closed.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

ALERT_TOKEN_ENV = "CHIP_ALERT_INGEST_TOKEN"

# case-class -> (skill_selected or None=unmatched, matched: True/False)
# matched = a registry runbook/skill row would claim this class after graduation.
ROUTE_TABLE: dict[str, tuple[str, str]] = {
    "stage1-advisor": ("stage1-advisor (parks homelab — detect-only)", "matched: chip-diag"),
    "pgs-zero-production": ("No skill yet (RAZ-330 standup digest covers)", "unmatched"),
    "standup-stall": ("chip taskflow emitter receipt path", "matched: chip-diag"),
    "correlated-dark": ("correlated-dark finding row (tell-Ally-once)", "matched: chip-diag"),
    "internal-loop-degrade": ("monitoring-api-internal-loop", "matched: razzorstack"),
    "gold-signal-down": ("discord-bot-loop-restart", "matched: razzorstack"),
    "status-drift": ("re-run-health-probe", "matched: razzorstack"),
    "suppression-saturation": ("clear-stale-suppress", "matched: chip"),
    "skill-runner-fault": ("skill-runner-restart", "matched: chip"),
    # W-006 (Ally GO 2026-10-04 21:39 PT): the ONLY non-shadow route —
    # plex-external fix skill, self-disarming after the 24h law window.
    # See chip_relay.plex_fix_skill and docs/PLEX_FIX_SKILL.md.
    "plex-external": ("plex-external-fix (W-006 24h window)", "matched: chip"),
    # W-010 (Ally GO 2026-10-05): restart escalation for monitoring-bot
    # auto-restart failures — one supervised executor restart per incident.
    # See chip_relay.service_recovery_skill.
    "auto-restart-failed": ("service-recovery (W-010 restart escalation)", "matched: razzorstack"),
    # W-013: discord-bot feed-source alerts are watch-only (no skill); routed
    # so they stop shadow-logging + banner-spamming the ingest path.
    "discord-bot": ("discord-bot feed target (watch-only, no skill)", "matched: razzorstack"),
}


def classify(alert: Mapping[str, Any]) -> tuple[str, str]:
    """Route one alert -> (skill_selected, matched|unmatched) per MONITOR_CASES.

    Unmatched = anything outside the known case class keys — the route table
    is the single source of routes; no fuzzy matching (garbage never a skill).
    """
    key = str(alert.get("key") or "").strip()
    for pattern, (skill, verdict) in ROUTE_TABLE.items():
        if re.match(rf"^{re.escape(pattern)}(\b|:)", key):
            return skill, verdict
    skill, verdict = ("UNMATCHED: no route registered", "unmatched")
    # fall back to the '' sentinel case: bare "match" verdicts are cite data
    return skill, verdict


def dispatch_record(alert: Mapping[str, Any], *, now_iso: str | None = None) -> dict[str, Any]:
    skill, verdict = classify(alert)
    return {
        "ts": now_iso or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "alert_id": str(alert.get("alert_id") or ""),
        "key": str(alert.get("key") or ""),
        "detail": str(alert.get("detail") or "")[:160],
        "skill_selected": skill,
        "matched": not verdict.startswith("unmatched"),
        "verdict": verdict,
        "shadow": True,  # 3-day shadow: logged only, never auto-executed
    }


def unmatched_banner(rec: Mapping[str, Any]) -> str | None:
    """One TARGET #monitoring-logs line per unmatched alert (never silent)."""
    if rec.get("matched"):
        return None
    return f"[chip-alert-ingest] UNMATCHED-INGEST no route for {rec['key']!r} — shadow logged, no auto-response standing (3-day shadow window)"


# W-013: the ingest ledger + banner cannot flood. Alerts that produce NO
# skill receipt (shadow and watch-only routes) dedupe per key per window —
# one row + one banner per source per 15 min; repeats are silent.
SHADOW_DEDUPE_WINDOW_S = 900


def _shadow_state_path() -> Any:
    from chip import config

    return config.data_dir() / "chip_state" / "shadow_dedupe.json"


def _shadow_recent(key: str, *, now_ts: float | None = None) -> bool:
    import time

    if not key:
        return False
    path = _shadow_state_path()
    state: dict[str, Any] = {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    now_ts = time.time() if now_ts is None else now_ts
    # prune expired entries opportunistically
    state = {
        k: ts
        for k, ts in state.items()
        if isinstance(ts, (int, float)) and now_ts - float(ts) < SHADOW_DEDUPE_WINDOW_S
    }
    last = state.get(key)
    return isinstance(last, (int, float)) and now_ts - float(last) < SHADOW_DEDUPE_WINDOW_S


def _shadow_mark(key: str, *, now_ts: float | None = None) -> None:
    import time

    if not key:
        return
    path = _shadow_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    state[key] = float(time.time() if now_ts is None else now_ts)
    path.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def logged_path() -> Any:
    from chip import config

    return config.data_dir() / "chip_state" / "alert_dispatch.jsonl"


def handle_alert(alert: Any, *, environ: dict[str, str] | None = None, log_row: dict[str, Any] | None = None) -> dict[str, Any]:
    """One ingest turn: validate -> dispatch -> log -> (banner) — SHADOW only.

    Garbage body -> structured 400-style error dict (fail-closed, no crash,
    no silent drop). Returns the machine-checkable receipt for the caller.
    Alerts that produce no skill receipt dedupe per key per window (W-013):
    one ledger row + one banner per source, repeats silent.
    """
    if not isinstance(alert, Mapping) or not str(alert.get("alert_id") or "").strip():
        return {"ok": False, "status": 400, "error": "alert requires non-empty alert_id"}
    rec = log_row or dispatch_record(alert)
    # W-006 law window: the ONLY executor wiring in the ingest path.
    # plex_fix_skill.maybe_run returns None for every non-plex-external alert,
    # checks its own expiry/caps before any action, and never raises past here.
    plex_fix_receipt: dict[str, Any] | None = None
    try:
        from chip_relay import plex_fix_skill

        plex_fix_receipt = plex_fix_skill.maybe_run(dict(alert), environ=environ)
    except Exception as exc:  # noqa: BLE001 — never crash the ingest; fault stays visible
        plex_fix_receipt = {"status": "error", "error": f"{exc.__class__.__name__}: {exc}"[:160]}
    # W-010: restart escalation for monitoring-bot auto-restart failures.
    # service_recovery_skill.maybe_run returns None for every other key,
    # checks its own expiry/suppression/allowlist/caps before any action,
    # and never raises past here.
    service_recovery_receipt: dict[str, Any] | None = None
    try:
        from chip_relay import service_recovery_skill

        service_recovery_receipt = service_recovery_skill.maybe_run(dict(alert), environ=environ)
    except Exception as exc:  # noqa: BLE001 — never crash the ingest; fault stays visible
        service_recovery_receipt = {"status": "error", "error": f"{exc.__class__.__name__}: {exc}"[:160]}

    skill_responded = plex_fix_receipt is not None or service_recovery_receipt is not None
    deduped = False
    if skill_responded or not _shadow_recent(str(rec.get("key", ""))):
        path = logged_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        if not skill_responded:
            _shadow_mark(str(rec.get("key", "")))
        banner = unmatched_banner(rec)
        posted = banner is not None and bool(log_banner_row(banner, environ=environ))
    else:
        deduped = True
        banner = None
        posted = False
    return {
        "ok": True,
        "status": 202,
        "record": dict(rec),
        "banner_posted": bool(posted),
        "shadow_deduped": deduped,
        "plex_fix": plex_fix_receipt,
        "service_recovery": service_recovery_receipt,
    }


def log_banner_row(text: str, environ: dict[str, str] | None = None) -> bool:
    """Post the unmatched banner via the EXISTING post law (no webhooks added)."""
    from chip import config
    from chip.monitor_watchdog import post_to_monitoring

    ok, _ = post_to_monitoring(text, environ=environ)
    return ok
