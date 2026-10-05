"""W-010 service-recovery skill — restart escalation for monitoring-bot failures.

The razzorstack monitoring bot auto-restarts down containers; when a restart
errors it posts "❌ Auto-Restart Failed" to Discord and (since W-010) POSTs the
same event to chip-relay's alert ingest (``POST /alert``, key
``auto-restart-failed``). This skill is the one executor wiring for those
events.

Scope walls (all enforced in code, before any action):
- trigger: ONLY ``auto-restart-failed`` alerts whose detail carries a
  ``container=<name>`` field. Every other class keeps the shadow law.
- classification: the docker error string is classified (daemon-error /
  missing / restart-timeout / unknown) and recorded — it shapes the lesson,
  never widens the action.
- actions: ``docker restart`` of ONE container at a time, ONLY if it is in
  ``service_allowlist`` AND NOT in ``suppressed_services``. Execution goes
  exclusively through homelab-executor (``POST /scripts/run`` with its
  whitelisted ``docker_restart_container`` script — HMAC-signed). The relay
  never touches docker itself for this skill.
- restart-only: no compose, no create, no recreate, no volume, no network.
  The script allowlist on the executor side enforces the same wall.
- fails closed: missing HMAC secret, unparsable law, empty allowlist,
  suppressed/unknown container — every doubt is a refusal with a receipt.
- caps: one attempt per incident fingerprint; hard cap ``max_attempts_24h``
  per rolling 24h.
- report: outcome goes to the monitoring Discord channel (the channel the
  failure came from) via the existing post law, never a new webhook.
- learnings: append-only JSONL at ``<data>/skills/service-recovery/learnings.jsonl``
  with entries ``{timestamp, situation, diagnosis, action, outcome, lesson}``.
  Each run loads the tail (default 20) before deciding; the tail's latest
  lesson is echoed into the dispatch row and the monitoring-channel line.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from chip import config

SKILL_ID = "service-recovery"
STATE_SUBDIR = "skills/service-recovery"
LEARNINGS_TAIL = 20

TARGET_KEY_RE = re.compile(r"^auto-restart-failed$", re.IGNORECASE)
CONTAINER_RE = re.compile(r"container=([A-Za-z0-9_.-]+)")

ENV_EXPIRES = "CHIP_SERVICE_RECOVERY_EXPIRES_ISO"
ENV_MAX_ATTEMPTS = "CHIP_SERVICE_RECOVERY_MAX_ATTEMPTS"
HMAC_SECRET_ENV = "HOMELAB_HMAC_SECRET"

_LOCK = threading.Lock()


def _law_file() -> Path:
    return (
        Path(__file__).resolve().parents[2] / "config" / "chip_relay" / "service_recovery.json"
    )


def law_config(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Law for this skill: config file is the SoT; env vars override for tests/ops."""
    env = environ if environ is not None else os.environ
    law: dict[str, Any] = {}
    if _law_file().is_file():
        try:
            law = json.loads(_law_file().read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            law = {}
    if not law:
        law = {
            "skill_id": SKILL_ID,
            "enabled": False,
            "expires_iso": "",
            "max_attempts_24h": 3,
            "service_allowlist": [],
            "suppressed_services": [],
            "executor_base_url": "",
            "executor_script": "docker_restart_container",
        }
    raw_expires = str(env.get(ENV_EXPIRES, "") or "").strip()
    if raw_expires:
        law["expires_iso"] = raw_expires
    raw_max = str(env.get(ENV_MAX_ATTEMPTS, "") or "").strip()
    if raw_max.isdigit():
        law["max_attempts_24h"] = int(raw_max)
    law["max_attempts_24h"] = int(law.get("max_attempts_24h", 3))
    law["service_allowlist"] = [str(s) for s in law.get("service_allowlist", [])]
    law["suppressed_services"] = [str(s) for s in law.get("suppressed_services", [])]
    return law


def skill_dir() -> Path:
    return config.data_dir() / STATE_SUBDIR


def learnings_path() -> Path:
    return skill_dir() / "learnings.jsonl"


def state_path() -> Path:
    return skill_dir() / "state.json"


def load_learnings(limit: int = LEARNINGS_TAIL) -> list[dict[str, Any]]:
    """Tail-load the journal — every run stands on what the runs before it saw."""
    p = learnings_path()
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-max(1, limit):]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def append_learning(entry: dict[str, Any]) -> None:
    p = learnings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": entry.get("timestamp") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "situation": str(entry.get("situation", ""))[:300],
        "diagnosis": str(entry.get("diagnosis", ""))[:300],
        "action": str(entry.get("action", ""))[:300],
        "outcome": str(entry.get("outcome", ""))[:300],
        "lesson": str(entry.get("lesson", ""))[:300],
    }
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def load_state() -> dict[str, Any]:
    p = state_path()
    if not p.is_file():
        return {"attempted_fps": {}, "disarmed": False, "disarm_notice_posted": False}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"attempted_fps": {}, "disarmed": False, "disarm_notice_posted": False}
    raw.setdefault("attempted_fps", {})
    raw.setdefault("disarmed", False)
    raw.setdefault("disarm_notice_posted", False)
    return raw


def save_state(state: dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def parse_expires(iso: str) -> datetime | None:
    raw = (iso or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def is_expired(law: dict[str, Any], now: datetime | None = None) -> bool:
    dt = parse_expires(str(law.get("expires_iso", "")))
    if dt is None:
        return True  # unparsable/absent expiry = expired (fail-closed)
    now = now or datetime.now(timezone.utc)
    return now >= dt


def is_target_alert(key: str, detail: str) -> bool:
    return bool(TARGET_KEY_RE.match((key or "").strip())) and bool(
        CONTAINER_RE.search(detail or "")
    )


def classify_failure(detail: str) -> str:
    """Classify the docker error string. Shapes the lesson; never the action."""
    text = (detail or "").lower()
    if "no such container" in text or "not found" in text:
        return "missing-container"
    if "timed out" in text or "timeout" in text:
        return "restart-timeout"
    if "500" in text or "server error" in text:
        return "daemon-error"
    return "unknown"


def _report_to_monitoring(environ: dict[str, str], line: str) -> bool:
    """Post the outcome to the monitoring Discord channel (existing post law)."""
    from chip.monitor_watchdog import post_to_monitoring

    ok, _ = post_to_monitoring(line[:1900], environ=environ)
    return ok


def _default_executor_restart(
    base_url: str,
    script: str,
    container: str,
    secret: str,
    timeout_s: int,
) -> tuple[bool, str]:
    """One HMAC-signed POST /scripts/run to homelab-executor. Restart-only.

    The executor-side whitelist script re-checks its own container allowlist
    and never runs compose — this call carries no shell, no interpolation.
    """
    body = json.dumps(
        {"name": script, "args": [f"--container={container}"]}
    ).encode()
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/scripts/run",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": str(ts),
            "X-Signature": sig,
            "User-Agent": "chip-relay service-recovery",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 — one receipt, never a loop
        return False, f"executor http error: {exc.__class__.__name__}"
    ok = bool(payload.get("ok")) and int(payload.get("exit_code", 1)) == 0
    if not ok and payload.get("error"):
        return False, f"executor refused: {payload['error']}"
    if not ok:
        return False, f"executor script failed rc={payload.get('exit_code')}"
    return True, f"executor restarted {container}"


def _prune_window(state: dict[str, Any], now_dt: datetime) -> None:
    """Rolling 24h window: drop fingerprints older than the cap horizon."""
    cutoff = now_dt.timestamp() - 86400
    state["attempted_fps"] = {
        fp: iso
        for fp, iso in state.get("attempted_fps", {}).items()
        if _attempt_ts(iso, now_dt) >= cutoff
    }


def _attempt_ts(iso: str, now_dt: datetime) -> float:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return now_dt.timestamp()


def maybe_run(
    alert: dict[str, Any],
    *,
    environ: dict[str, str] | None = None,
    now: datetime | None = None,
    report: Callable[[dict[str, str], str], bool] | None = None,
    executor_runner: Callable[[str, str, str, str, int], tuple[bool, str]] | None = None,
) -> dict[str, Any] | None:
    """Entry point. Returns a machine-checkable receipt dict, or None if not ours.

    Order is law, not style: target -> expiry -> enabled -> suppression ->
    allowlist -> one-per-incident -> 24h cap -> secret -> action. Nothing
    executes unless every gate passes.
    """
    env = environ if environ is not None else os.environ
    now = now or datetime.now(timezone.utc)
    key = str(alert.get("key", ""))
    detail = str(alert.get("detail", ""))
    if not is_target_alert(key, detail):
        return None

    report = report or _report_to_monitoring
    law = law_config(env)
    fp_stub = alert.get("fingerprint")
    from chip import monitor as _monitor

    fp = str(fp_stub or _monitor.fingerprint_for(key, detail))
    container_match = CONTAINER_RE.search(detail)
    container = container_match.group(1) if container_match else ""
    failure_class = classify_failure(detail)

    with _LOCK:
        state = load_state()
        learnings = load_learnings()
        last_lesson = str(learnings[-1].get("lesson", ""))[:160] if learnings else "journal empty (first run)"

        # --- gate 1: expiry (self-disarm before ANY action) ---
        if is_expired(law, now):
            row_verdict = "disarmed"
            action = "none (expired)"
            outcome = "no action: law window expired"
            posted = False
            if not state.get("disarm_notice_posted"):
                state["disarmed"] = True
                state["disarm_notice_posted"] = True
                posted = report(
                    env,
                    f"[service-recovery] DISARMED: law window expired ({law.get('expires_iso')}) — "
                    f"{container} restart escalation refused; monitoring-bot failure is human work now",
                )
            save_state(state)
            _append_learning_and_row(
                alert, fp, container, failure_class, row_verdict, action, outcome, now,
                lesson="window expired; skill self-disarmed before any action",
                last_lesson=last_lesson, posted_notice=posted,
            )
            return {"status": "disarmed", "expires_iso": law.get("expires_iso"), "notice_posted": posted}

        # --- gate 2: enabled flag ---
        if not law.get("enabled", False):
            _append_learning_and_row(
                alert, fp, container, failure_class, "disabled", "none",
                "law config has enabled=false", now,
                lesson="skill present but disabled by config; classify+tell law intact",
                last_lesson=last_lesson, posted_notice=False,
            )
            return {"status": "disabled"}

        # --- gate 3: suppression wall (post-wipe holds — never restarted) ---
        if container in law.get("suppressed_services", []):
            _append_learning_and_row(
                alert, fp, container, failure_class, "suppressed", "none",
                f"{container} is in suppressed_services — mute holds", now,
                lesson="suppression wall held; known-broken container stays human-owned",
                last_lesson=last_lesson, posted_notice=False,
            )
            return {"status": "suppressed", "container": container}

        # --- gate 4: allowlist wall (one named container, allowlist only) ---
        if container not in law.get("service_allowlist", []):
            _append_learning_and_row(
                alert, fp, container, failure_class, "not-allowlisted", "none",
                f"{container or '<unparsed>'} not in service_allowlist — refuse", now,
                lesson="container outside allowlist = no mutation; fail closed",
                last_lesson=last_lesson, posted_notice=False,
            )
            return {"status": "not-allowlisted", "container": container}

        # --- gate 5: one attempt per incident fingerprint ---
        if fp in state.get("attempted_fps", {}):
            _append_learning_and_row(
                alert, fp, container, failure_class, "incident-repeat-skipped", "none",
                f"already attempted at {state['attempted_fps'][fp]}", now,
                lesson="one recovery attempt per incident fingerprint; repeat suppressed",
                last_lesson=last_lesson, posted_notice=False,
            )
            return {"status": "incident-repeat-skipped", "attempted_at": state["attempted_fps"][fp]}

        # --- gate 6: rolling 24h attempt cap ---
        _prune_window(state, now)
        if len(state.get("attempted_fps", {})) >= int(law.get("max_attempts_24h", 3)):
            posted = report(
                env,
                f"[service-recovery] cap reached ({law.get('max_attempts_24h')}/24h) — "
                f"no recovery for {container} ({failure_class}); classify+tell only",
            )
            _append_learning_and_row(
                alert, fp, container, failure_class, "cap-reached", "none",
                f"cap {law.get('max_attempts_24h')}/24h reached", now,
                lesson="hard cap held; no attempt beyond the human-authorized budget",
                last_lesson=last_lesson, posted_notice=posted,
            )
            return {"status": "cap-reached", "attempts_used": len(state.get("attempted_fps", {}))}

        # --- gate 7: executor credential (fail closed without it) ---
        secret = (env.get(HMAC_SECRET_ENV, "") or "").strip()
        if not secret:
            _append_learning_and_row(
                alert, fp, container, failure_class, "no-credentials", "none",
                f"{HMAC_SECRET_ENV} unset — executor unreachable, refuse", now,
                lesson="no executor HMAC secret = no mutation; fail closed",
                last_lesson=last_lesson, posted_notice=False,
            )
            return {"status": "no-credentials"}

        # --- action: the ONLY mutation this skill can make ---
        base_url = str(law.get("executor_base_url", "")).strip()
        script = str(law.get("executor_script", "docker_restart_container"))
        ok, executor_outcome = executor_runner(
            base_url, script, container, secret, int(law.get("restart_timeout_s", 120))
        ) if executor_runner else _default_executor_restart(
            base_url, script, container, secret, int(law.get("restart_timeout_s", 120))
        )
        outcome = f"failure_class={failure_class}; executor={executor_outcome}"
        verdict = "recovery-attempted" if ok else "recovery-failed"

        state.setdefault("attempted_fps", {})[fp] = now.isoformat(timespec="seconds")
        save_state(state)

        attempt_no = len(state["attempted_fps"])
        lesson = (
            "restart-only escalation executed; monitoring channel is the ground truth"
            if ok
            else f"executor restart failed ({executor_outcome[:80]}) — never retry same incident"
        )
        line = (
            f"[service-recovery] attempt {attempt_no}/{law.get('max_attempts_24h')}: "
            f"monitoring bot could not restart **{container}** ({failure_class}); "
            f"one executor restart sent — {executor_outcome}; "
            f"last_learning={last_lesson}"
        )
        posted = report(env, line)

        _append_learning_and_row(
            alert, fp, container, failure_class, verdict,
            f"executor {script} --container {container}", outcome, now,
            lesson=lesson, last_lesson=last_lesson, posted_notice=posted,
        )
        return {
            "status": verdict,
            "fingerprint": fp,
            "container": container,
            "failure_class": failure_class,
            "executor_outcome": executor_outcome,
            "report_posted": posted,
            "attempt": f"{attempt_no}/{law.get('max_attempts_24h')}",
        }


def _append_learning_and_row(
    alert: dict[str, Any],
    fp: str,
    container: str,
    failure_class: str,
    verdict: str,
    action: str,
    outcome: str,
    now: datetime,
    *,
    lesson: str,
    last_lesson: str,
    posted_notice: bool,
) -> None:
    situation = f"{alert.get('key')} {str(alert.get('detail', ''))[:140]} (fp={fp})"
    diagnosis = (
        f"monitoring-bot auto-restart failed for {container or '<unparsed>'} "
        f"(class: {failure_class})"
    )
    append_learning(
        {
            "timestamp": now.isoformat(timespec="seconds"),
            "situation": situation,
            "diagnosis": diagnosis,
            "action": action,
            "outcome": outcome,
            "lesson": lesson,
        }
    )
    _append_dispatch_row(
        {
            "ts": now.isoformat(timespec="seconds"),
            "alert_id": str(alert.get("alert_id", "")),
            "key": str(alert.get("key", "")),
            "detail": str(alert.get("detail", ""))[:160],
            "skill_selected": SKILL_ID,
            "matched": True,
            "verdict": verdict,
            "shadow": False,
            "action": action,
            "outcome": outcome,
            "learnings_loaded": last_lesson,
            "tell_posted": bool(posted_notice),
        }
    )


def _append_dispatch_row(row: dict[str, Any]) -> None:
    from chip_relay import alert_ingest

    path = alert_ingest.logged_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
