"""W-006 plex-external fix skill — the ONLY executor wiring under the 24h law window.

Ally GO 2026-10-04 21:39 PT (watch log W-006): a RAZ-63 mutability exception,
Plex-external-only, time-boxed to 24h, self-disarming. Outside that window the
standing law holds: no auto-restarts, classify+tell only.

Scope walls (all enforced in code, before any action):
- trigger: ONLY ``plex-external*`` alerts whose detail carries the
  530 / identity / health-down fingerprint. Every other class keeps the
  shadow law exactly (alert_ingest route table unchanged for them).
- actions: ``docker restart`` of the ``service_allowlist`` containers ONLY
  (caddy, cloudflared — the plex.razzormail.com origin chain). No other
  container, no volume, no network, no host service.
- native Windows Plex service restart: SKIPPED by design — the relay stack
  has no safe host-execution path (homelab-executor exists as an image but
  is not running/wired; the relay holds a docker proxy socket only). This
  limitation is recorded as a learning on every journal init and stated in
  every tell.
- expiry: checked before ANY action. After ``expires_iso`` the skill disarms
  itself and posts ONE disarm notice to #chip-dev.
- caps: one attempt per incident fingerprint; hard cap ``max_attempts_24h``
  fix attempts per rolling 24h. Every attempt (or refusal) writes a
  dispatch row to the alert_dispatch ledger and one learning reflection.
- learnings: append-only JSONL at ``<data>/skills/plex-fix/learnings.jsonl``
  with entries ``{timestamp, situation, diagnosis, action, outcome, lesson}``.
  Each run loads the tail (default 20) before deciding; the tail's latest
  lesson is echoed into the dispatch row and tell so the load is observable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from chip import config

SKILL_ID = "plex-external-fix"
STATE_SUBDIR = "skills/plex-fix"
LEARNINGS_TAIL = 20

TARGET_KEY_RE = re.compile(r"^plex-external", re.IGNORECASE)
TARGET_DETAIL_RE = re.compile(r"530|identity|health-?down", re.IGNORECASE)

ENV_EXPIRES = "CHIP_PLEX_FIX_EXPIRES_ISO"
ENV_MAX_ATTEMPTS = "CHIP_PLEX_FIX_MAX_ATTEMPTS"

_LOCK = threading.Lock()


def _law_file() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "chip_relay" / "plex_fix.json"


def law_config(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Law for this window: config file is the SoT; env vars override for tests/ops."""
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
            "external_probe_url": "",
        }
    raw_expires = str(env.get(ENV_EXPIRES, "") or "").strip()
    if raw_expires:
        law["expires_iso"] = raw_expires
    raw_max = str(env.get(ENV_MAX_ATTEMPTS, "") or "").strip()
    if raw_max.isdigit():
        law["max_attempts_24h"] = int(raw_max)
    law["max_attempts_24h"] = int(law.get("max_attempts_24h", 3))
    law["service_allowlist"] = [str(s) for s in law.get("service_allowlist", [])]
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
        TARGET_DETAIL_RE.search(detail or "")
    )


def _append_dispatch_row(row: dict[str, Any]) -> None:
    from chip_relay import alert_ingest

    path = alert_ingest.logged_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _default_docker(restart_targets: list[str], timeout_s: int) -> tuple[bool, str]:
    """Restart the allowlisted containers through the mounted docker socket.

    Uses the static docker CLI baked into the image; the socket is mounted
    read-only at /var/run/docker.sock by the deploy. No other docker verbs.
    """
    try:
        proc = subprocess.run(
            ["docker", "restart", *restart_targets],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError:
        return False, "docker CLI missing in image"
    except subprocess.TimeoutExpired:
        return False, f"docker restart timed out after {timeout_s}s"
    if proc.returncode != 0:
        return False, f"docker restart rc={proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:160]}"
    return True, f"restarted {','.join(restart_targets)}"


def _default_probe(url: str, timeout_s: int) -> str:
    """One-shot external verify. Any HTTP status is an answer; no retries, no loops."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "chip-relay plex-fix"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return f"reachable:{int(getattr(resp, 'status', 0))}"
    except urllib.error.HTTPError as exc:
        return f"reachable:{int(exc.code)}"
    except Exception as exc:  # noqa: BLE001 — one receipt, never a loop
        return f"unreachable:{exc.__class__.__name__}"


def _tell(environ: dict[str, str], line: str, webhook: Callable[..., Any] | None) -> bool:
    url = (environ.get("CHIP_WEBHOOK_DEV", "") or "").strip()
    if not url:
        return False
    if webhook is None:
        from chip_relay.gateway import post_webhook as webhook
    result = webhook(url, "chip-relay", line[:1900])
    return 200 <= int(getattr(result, "status", 0)) < 300


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
    webhook: Callable[..., Any] | None = None,
    docker_runner: Callable[[list[str], int], tuple[bool, str]] | None = None,
    prober: Callable[[str, int], str] | None = None,
) -> dict[str, Any] | None:
    """Entry point. Returns a machine-checkable receipt dict, or None if not ours.

    Order is law, not style: expiry gate -> allowlist prep -> caps -> action.
    Nothing executes unless every gate passes.
    """
    env = environ if environ is not None else os.environ
    now = now or datetime.now(timezone.utc)
    key = str(alert.get("key", ""))
    detail = str(alert.get("detail", ""))
    if not is_target_alert(key, detail):
        return None

    law = law_config(env)
    docker_runner = docker_runner or _default_docker
    prober = prober or _default_probe
    fp_stub = alert.get("fingerprint")
    from chip import monitor as _monitor

    fp = str(fp_stub or _monitor.fingerprint_for(key, detail))

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
                line = (
                    f"[plex-fix] DISARMED: law window expired ({law.get('expires_iso')}) — "
                    "no fix actions will run; standing law (classify+tell only) restored"
                )
                posted = _tell(env, line, webhook)
            save_state(state)
            _append_learning_and_row(
                alert, fp, row_verdict, action, outcome, now,
                lesson="window expired; skill self-disarmed before any action",
                last_lesson=last_lesson, posted_notice=posted,
            )
            return {"status": "disarmed", "expires_iso": law.get("expires_iso"), "notice_posted": posted}

        # --- gate 2: enabled flag ---
        if not law.get("enabled", False):
            _append_learning_and_row(
                alert, fp, "disabled", "none", "law config has enabled=false", now,
                lesson="skill present but disabled by config; classify+tell law intact",
                last_lesson=last_lesson, posted_notice=False,
            )
            return {"status": "disabled"}

        # --- gate 3: one attempt per incident fingerprint ---
        if fp in state.get("attempted_fps", {}):
            _append_learning_and_row(
                alert, fp, "incident-repeat-skipped", "none",
                f"already attempted at {state['attempted_fps'][fp]}", now,
                lesson="one fix attempt per incident fingerprint; repeat suppressed",
                last_lesson=last_lesson, posted_notice=False,
            )
            return {"status": "incident-repeat-skipped", "attempted_at": state["attempted_fps"][fp]}

        # --- gate 4: rolling 24h attempt cap ---
        _prune_window(state, now)
        if len(state.get("attempted_fps", {})) >= int(law.get("max_attempts_24h", 3)):
            line = (
                f"[plex-fix] cap reached ({law.get('max_attempts_24h')}/24h) — "
                f"no fix for {key} {detail[:80]}; standing law resumes (classify+tell only)"
            )
            posted = _tell(env, line, webhook)
            _append_learning_and_row(
                alert, fp, "cap-reached", "none", f"cap {law.get('max_attempts_24h')}/24h reached", now,
                lesson="hard cap held; no attempt beyond the human-authorized budget",
                last_lesson=last_lesson, posted_notice=posted,
            )
            return {"status": "cap-reached", "attempts_used": len(state.get("attempted_fps", {}))}

        # --- action: allowlist wall (the ONLY mutation this skill can make) ---
        targets = list(law.get("service_allowlist", []))
        if not targets:
            _append_learning_and_row(
                alert, fp, "no-allowlist", "none", "service_allowlist empty — refuse", now,
                lesson="empty allowlist = no mutation; fail closed",
                last_lesson=last_lesson, posted_notice=False,
            )
            return {"status": "no-allowlist"}
        ok, docker_outcome = docker_runner(targets, int(law.get("restart_timeout_s", 120)))
        verify = prober(str(law.get("external_probe_url", "")), int(law.get("probe_timeout_s", 15)))
        outcome = f"docker={docker_outcome}; external_probe={verify}"
        verdict = "fix-attempted" if ok else "fix-failed"

        state.setdefault("attempted_fps", {})[fp] = now.isoformat(timespec="seconds")
        save_state(state)

        attempt_no = len(state["attempted_fps"])
        lesson = (
            "restart of origin chain executed; external verify is the ground truth"
            if ok
            else f"docker restart failed ({docker_outcome[:80]}) — never retry same incident"
        )
        line = (
            f"[plex-fix] attempt {attempt_no}/{law.get('max_attempts_24h')} (W-006 window, "
            f"expires {law.get('expires_iso')}): restarted {','.join(targets)}; "
            f"docker={docker_outcome[:80]}; external_probe={verify}; "
            f"native-Plex-restart=skipped (no safe host-exec path in relay stack); "
            f"last_learning={last_lesson}"
        )
        posted = _tell(env, line, webhook)

        _append_learning_and_row(
            alert, fp, verdict, f"docker restart {','.join(targets)}", outcome, now,
            lesson=lesson, last_lesson=last_lesson, posted_notice=posted,
        )
        return {
            "status": verdict,
            "fingerprint": fp,
            "docker_outcome": docker_outcome,
            "external_probe": verify,
            "tell_posted": posted,
            "attempt": f"{attempt_no}/{law.get('max_attempts_24h')}",
        }


def _append_learning_and_row(
    alert: dict[str, Any],
    fp: str,
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
    diagnosis = "plex.razzormail.com 530/identity down — origin chain (caddy/cloudflared) suspect"
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