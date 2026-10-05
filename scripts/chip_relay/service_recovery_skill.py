"""W-010/W-013 service-recovery skill — restart escalation for monitoring-bot failures.

The razzorstack monitoring bot auto-restarts down containers; when a restart
errors it posts "❌ Auto-Restart Failed" to Discord and (since W-010) POSTs the
same event to chip-relay's alert ingest (``POST /alert``, key
``auto-restart-failed``). This skill is the one executor wiring for those
events.

Scope walls (all enforced in code, before any action):
- trigger: ONLY ``auto-restart-failed`` alerts whose detail carries a
  ``container=<name>`` field. Every other class keeps the shadow law.
- classification: the docker error string is classified (daemon-error /
  missing-container / restart-timeout / unknown) and recorded. A
  ``missing-container`` classification means the named target is NOT a
  container (native Windows service or stale entry) — docker restarts can
  never help, so the skill refuses with one report (W-013, the sonarr case).
- incident identity (W-013 dedup fix): the incident fingerprint is
  ``sha256(key|container|failure_class)`` — NOT the raw detail. Repeat
  ingests of the same failing container+class (the monitor re-pushes every
  cycle, and docker error strings embed varying IDs) dedupe to ONE attempt
  and ONE message per rolling 24h window.
- actions: ``docker restart`` of ONE container at a time, ONLY if it is in
  ``service_allowlist`` AND NOT in ``suppressed_services``. Execution goes
  exclusively through homelab-executor (``POST /scripts/run`` with its
  whitelisted ``docker_restart_container`` script — HMAC-signed). The relay
  never touches docker itself for this skill.
- restart-only: no compose, no create, no recreate, no volume, no network.
  The script allowlist on the executor side enforces the same wall.
- model routing (W-013, Ally GO: "cheap qwen dev bots do the recovery after
  the PM tells them to"): after the code gates pass, a cheap PM-classifier
  call makes the go/no-go decision (structured JSON: playbook + go), then a
  qwen executor-model call runs the playbook narrative with the learnings
  tail loaded. Both calls see ONLY the playbook + redacted incident context
  (container name, failure class, error excerpt scrubbed of token-like
  strings) — never secrets, never env, never ledger data. The models decide
  and narrate only; the actual mutation stays this module's single
  code-enforced executor restart, so the blast radius is unchanged. PM
  unparsable/refused = no action (fail closed); qwen failure is recorded
  but does not block the PM-authorized restart.
- fails closed: missing HMAC secret, unparsable law, empty allowlist,
  suppressed/unknown/native container, PM no-go — every doubt is a refusal
  with a receipt.
- caps: one attempt per incident fingerprint; hard cap ``max_attempts_24h``
  per rolling 24h.
- report: outcome goes to the monitoring Discord channel (the channel the
  failure came from) via the existing post law, never a new webhook. The
  report path no-ops under ``CHIP_CHAT_MOCK`` so harness runs can never
  reach the live channel (W-013: five live posts escaped from state-isolated
  test contexts).
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
PLAYBOOK_NAME = "service-recovery"

TARGET_KEY_RE = re.compile(r"^auto-restart-failed$", re.IGNORECASE)
CONTAINER_RE = re.compile(r"container=([A-Za-z0-9_.-]+)")

ENV_EXPIRES = "CHIP_SERVICE_RECOVERY_EXPIRES_ISO"
ENV_MAX_ATTEMPTS = "CHIP_SERVICE_RECOVERY_MAX_ATTEMPTS"
HMAC_SECRET_ENV = "HOMELAB_HMAC_SECRET"
MOCK_ENV = "CHIP_CHAT_MOCK"

# Secret-shaped strings never leave the relay (defense in depth for model calls).
_REDACT_PATTERNS = (
    re.compile(r"(?i)\b(token|secret|password|passwd|api[-_]?key|authorization)\b[=:]\s*\S+"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}\b"),  # long base64/hex blobs
)


def _redact(text: str) -> str:
    out = str(text or "")
    for pat in _REDACT_PATTERNS:
        out = pat.sub("<redacted>", out)
    return out


_LOCK = threading.Lock()


def _law_file() -> Path:
    return (
        Path(__file__).resolve().parents[2] / "config" / "chip_relay" / "service_recovery.json"
    )


def _playbook_file() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "config"
        / "chip_relay"
        / "service_recovery_playbook.md"
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
            "executor_model": "",
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


def playbook_text() -> str:
    try:
        if _playbook_file().is_file():
            return _playbook_file().read_text(encoding="utf-8")[:4000]
    except OSError:
        pass
    return f"playbook {PLAYBOOK_NAME}: restart the failed container once via the executor; report and stop."


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
    """Classify the docker error string. Shapes the gates; never widens the action."""
    text = (detail or "").lower()
    if "no such container" in text or "not found" in text:
        return "missing-container"
    if "timed out" in text or "timeout" in text:
        return "restart-timeout"
    if "500" in text or "server error" in text:
        return "daemon-error"
    return "unknown"


def incident_fingerprint(key: str, container: str, failure_class: str) -> str:
    """Stable incident identity: container + class, NOT the raw error text.

    W-013: docker error strings embed varying IDs, so every monitor re-push
    used to mint a new fingerprint and re-fire. Same container + same class
    within the rolling window = the SAME incident.
    """
    raw = f"{(key or '').lower()}|{container}|{failure_class}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _report_to_monitoring(environ: dict[str, str], line: str) -> bool:
    """Post the outcome to the monitoring Discord channel (existing post law).

    No-ops under CHIP_CHAT_MOCK — harness runs must never reach the live
    channel (W-013: five live posts escaped from state-isolated test runs).
    """
    if (environ.get(MOCK_ENV, "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
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


def _openrouter_chat(model: str, messages: list[dict[str, str]], environ: dict[str, str], max_tokens: int) -> str:
    """One OpenRouter chat call via the existing openrouter module. Never raises."""
    from chip import openrouter

    key = environ.get("OPENROUTER_CC_API_KEY", "") or openrouter.require_api_key()
    payload = openrouter.chat_completion(key, model=model, messages=messages, max_tokens=max_tokens)
    text = openrouter.extract_assistant_text(payload)
    if not text.strip():
        raise RuntimeError("empty assistant text")
    return text


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """First JSON object in the reply (tolerates code fences / prose prefix)."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def default_pm_decide(
    law: dict[str, Any],
    incident: dict[str, Any],
    environ: dict[str, str],
) -> dict[str, Any]:
    """PM-classifier call: cheap, structured — {"playbook": ..., "go": ..., "reason": ...}.

    Fail-closed: any error, unparsable reply, wrong playbook, or missing go
    returns {"go": False, ...} — no executor call happens without an
    explicit structured GO from the PM model.
    """
    pm_model = str(law.get("pm_model", "") or "").strip()
    if not pm_model:
        pm_model = config.default_model()
    prompt = (
        "You are the PM triage layer of a homelab recovery relay. Decide whether "
        "the on-call dev bot may run the restart playbook for this incident.\n"
        f"Playbook: {PLAYBOOK_NAME}\n"
        "Incident (redacted context only):\n"
        f"- container: {incident.get('container', '')}\n"
        f"- failure_class: {incident.get('failure_class', '')}\n"
        f"- error_excerpt: {str(incident.get('error_excerpt', ''))[:200]}\n"
        f"- allowlisted: {incident.get('allowlisted')}\n"
        f"- attempts_used_24h: {incident.get('attempts_used_24h')}\n"
        f"- last_lesson: {str(incident.get('last_lesson', ''))[:160]}\n"
        'Reply with ONLY a JSON object: {"playbook": "service-recovery", "go": true|false, "reason": "<= 120 chars"}\n'
        "Rules: go=true ONLY for restartable containers in the allowlist; "
        "go=false for anything native, unknown, suppressed, or already attempted."
    )
    try:
        text = _openrouter_chat(pm_model, [{"role": "user", "content": prompt}], environ, 200)
    except Exception as exc:  # noqa: BLE001 — fail closed, record the fault
        return {"go": False, "playbook": "", "reason": f"pm-call-fault: {exc.__class__.__name__}"}
    obj = _extract_json_object(text)
    if not obj:
        return {"go": False, "playbook": "", "reason": "pm-reply-unparsable"}
    go = obj.get("go")
    playbook = str(obj.get("playbook", "")).strip()
    if playbook != PLAYBOOK_NAME or not isinstance(go, bool) or not go:
        return {
            "go": False,
            "playbook": playbook,
            "reason": str(obj.get("reason", ""))[:120] or "pm-no-go",
        }
    return {"go": True, "playbook": playbook, "reason": str(obj.get("reason", ""))[:120]}


def default_qwen_run_playbook(
    law: dict[str, Any],
    incident: dict[str, Any],
    learnings: list[dict[str, Any]],
    environ: dict[str, str],
) -> str:
    """Qwen executor-model call: playbook + learnings tail + redacted context.

    The reply is the dev bot's run sheet — narration for the receipt and the
    learnings journal. It executes nothing here; the code performs the one
    allowlisted executor restart either way.
    """
    qwen_model = str(law.get("executor_model", "") or "").strip()
    if not qwen_model:
        return "qwen executor-model not configured; PM-authorized restart proceeds without run-sheet"
    lessons = "\n".join(
        f"- {str(e.get('lesson', ''))[:140]}" for e in learnings[-5:]
    ) or "- journal empty (first run)"
    prompt = (
        f"You are the dev bot executing the {PLAYBOOK_NAME} playbook.\n"
        f"--- playbook ---\n{playbook_text()}\n--- learnings tail ---\n{lessons}\n"
        "--- incident (redacted) ---\n"
        f"container: {incident.get('container', '')}\n"
        f"failure_class: {incident.get('failure_class', '')}\n"
        f"error_excerpt: {str(incident.get('error_excerpt', ''))[:200]}\n"
        "Reply with a short run sheet (max 4 lines): what you will do, the single "
        "restart you authorize, what you will verify, and when you stop."
    )
    # Primary = the intended cheap dev-bot model; fallback = the configured
    # stand-in used while the workspace guardrail blocks the primary provider
    # (W-013: OpenRouter guardrails exclude the Qwen provider). Both faults
    # are recorded and never block the PM-authorized restart.
    for model in (qwen_model, str(law.get("executor_model_fallback", "") or "").strip()):
        if not model:
            continue
        try:
            text = _openrouter_chat(model, [{"role": "user", "content": prompt}], environ, 300)
            return " | ".join(line.strip() for line in text.splitlines() if line.strip())[:300]
        except Exception as exc:  # noqa: BLE001 — one receipt, never a loop
            last_fault = f"model {model} fault: {exc.__class__.__name__}"
    return f"qwen-call-fault: {last_fault} (restart proceeds per PM decision)"


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
    pm_decider: Callable[[dict[str, Any], dict[str, Any], dict[str, str]], dict[str, Any]] | None = None,
    qwen_runner: Callable[[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, str]], str] | None = None,
) -> dict[str, Any] | None:
    """Entry point. Returns a machine-checkable receipt dict, or None if not ours.

    Order is law, not style: target -> expiry -> enabled -> suppression ->
    allowlist -> native check -> one-per-incident -> 24h cap -> secret ->
    PM go/no-go -> action. Nothing executes unless every gate passes.
    """
    env = environ if environ is not None else os.environ
    now = now or datetime.now(timezone.utc)
    key = str(alert.get("key", ""))
    detail = str(alert.get("detail", ""))
    if not is_target_alert(key, detail):
        return None

    report = report or _report_to_monitoring
    law = law_config(env)
    container_match = CONTAINER_RE.search(detail)
    container = container_match.group(1) if container_match else ""
    failure_class = classify_failure(detail)
    fp_stub = alert.get("fingerprint")
    fp = str(fp_stub or incident_fingerprint(key, container, failure_class))

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

        # --- gate 4: native wall (W-013 — sonarr/radarr/plex are native services) ---
        if failure_class == "missing-container":
            _append_learning_and_row(
                alert, fp, container, failure_class, "not-a-container", "none",
                f"{container} is not a docker container (native service or stale entry) — no restart", now,
                lesson="missing-container class never reaches the executor; native targets are human work",
                last_lesson=last_lesson, posted_notice=False,
            )
            return {"status": "not-a-container", "container": container, "failure_class": failure_class}

        # --- gate 5: allowlist wall (one named container, allowlist only) ---
        if container not in law.get("service_allowlist", []):
            _append_learning_and_row(
                alert, fp, container, failure_class, "not-allowlisted", "none",
                f"{container or '<unparsed>'} not in service_allowlist — refuse", now,
                lesson="container outside allowlist = no mutation; fail closed",
                last_lesson=last_lesson, posted_notice=False,
            )
            return {"status": "not-allowlisted", "container": container}

        # --- gate 6: one attempt per incident fingerprint (stable identity) ---
        if fp in state.get("attempted_fps", {}):
            _append_learning_and_row(
                alert, fp, container, failure_class, "incident-repeat-skipped", "none",
                f"already attempted at {state['attempted_fps'][fp]}", now,
                lesson="one recovery attempt per incident fingerprint; repeat suppressed",
                last_lesson=last_lesson, posted_notice=False,
            )
            return {"status": "incident-repeat-skipped", "attempted_at": state["attempted_fps"][fp]}

        # --- gate 7: rolling 24h attempt cap ---
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

        # --- gate 8: executor credential (fail closed without it) ---
        secret = (env.get(HMAC_SECRET_ENV, "") or "").strip()
        if not secret:
            _append_learning_and_row(
                alert, fp, container, failure_class, "no-credentials", "none",
                f"{HMAC_SECRET_ENV} unset — executor unreachable, refuse", now,
                lesson="no executor HMAC secret = no mutation; fail closed",
                last_lesson=last_lesson, posted_notice=False,
            )
            return {"status": "no-credentials"}

        # --- gate 9: PM go/no-go (cheap classifier call, fail-closed) ---
        incident = {
            "container": container,
            "failure_class": failure_class,
            "error_excerpt": _redact(detail)[:200],
            "allowlisted": True,
            "attempts_used_24h": len(state.get("attempted_fps", {})),
            "last_lesson": last_lesson,
        }
        decide = pm_decider or default_pm_decide
        try:
            pm = decide(law, incident, env)
        except Exception as exc:  # noqa: BLE001 — a faulting decider is a no-go
            pm = {"go": False, "playbook": "", "reason": f"pm-call-fault: {exc.__class__.__name__}"}
        if not pm.get("go"):
            reason = str(pm.get("reason", "pm-no-go"))[:120]
            _append_learning_and_row(
                alert, fp, container, failure_class, "pm-refused", "none",
                f"PM classifier refused: {reason}", now,
                lesson="PM no-go = no mutation; the classifier gates the executor lane",
                last_lesson=last_lesson, posted_notice=False,
            )
            return {"status": "pm-refused", "container": container, "pm_reason": reason}

        # --- action: qwen run-sheet + the ONLY mutation this skill can make ---
        run_qwen = qwen_runner or default_qwen_run_playbook
        try:
            run_sheet = run_qwen(law, incident, learnings, env)
        except Exception as exc:  # noqa: BLE001 — recorded; never blocks the PM-authorized restart
            run_sheet = f"qwen-call-fault: {exc.__class__.__name__} (restart proceeds per PM decision)"
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
            f"PM=go ({str(pm.get('reason', ''))[:60]}); qwen run-sheet: {run_sheet[:100]}; "
            f"one executor restart sent — {executor_outcome}; "
            f"last_learning={last_lesson}"
        )
        posted = report(env, line)

        _append_learning_and_row(
            alert, fp, container, failure_class, verdict,
            f"executor {script} --container {container} (pm=go, qwen run-sheet recorded)", outcome, now,
            lesson=lesson, last_lesson=last_lesson, posted_notice=posted,
        )
        return {
            "status": verdict,
            "fingerprint": fp,
            "container": container,
            "failure_class": failure_class,
            "pm_reason": str(pm.get("reason", "")),
            "run_sheet": run_sheet,
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
