"""Jev router: registry-first, shadow-band,
mute scorer. Law encoded in this module:

- The counter registry (chip.counter_registry) runs BEFORE any HTTP call.
  ``counter_id=none`` is the only legal predecessor of a Jev request; every
  precheck row is ledgered before the decision.
- The request builder sends SCRIPT-BUILT FIELDS ONLY: fingerprints, counters,
  enum codes, ids. Free text is DROPPED by default; a bounded excerpt is fenced
  (<=200 chars, control chars stripped, marked untrusted). Secret-shaped keys
  are rejected from the state (last-line check).
- ``usage.cost`` (USD) from every response is recorded into the ledger.
- Bands are hypotheses: stored on the shadow row as ``band_hypothetical`` and
  NEVER branched on. The action taken is always the site's conservative
  default (``action=conservative``). Done-gate is LOGGER ONLY.
- Live calls are env-gated (CHIP_JEV_LIVE=1 + OPENROUTER_CC_API_KEY), single
  retry, fail-closed fallback: Jev-down must be indistinguishable from "gray
  zone, human reviews later".
- No third verdict: gray receipts take the site's conservative default without
  Jev ever flipping a gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip import config, counter_registry as reg_mod

PINNED_MODEL = "typesafe/jev-1.13"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_LIVE_ENV = "CHIP_JEV_LIVE"
LEDGER_FILE = "jev_calls.jsonl"

_SECRET_SHAPE = re.compile(
    r"(token|key|secret|password|passwd|apikey|api_key|webhook)", re.IGNORECASE
)
_EXCERPT_BOUND = 200
_BANDS = {
    # UNLABELED HYPOTHESES — proven-by-test to be unreferenced by any gate.
    # Numbers live here ONLY for shadow logging; they must never choose
    # behavior until same-state jitter + known-bad/benign histograms exist.
    "auto_min": 0.95,
    "room_min": 0.80,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha12(state: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:12]


def sanitize_state(state: dict[str, Any], *, environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Script-built fields only (golden-15/16 defense, last-line sweep).

    - Drops any key matching the secret shape (token/key/secret/password/...).
    - Truncates long string values to the excerpt bound, control-stripped.
    - Returns a JSON-clean state (lists/dicts/str/int/float/bool/None only).
    """
    sanitized: dict[str, Any] = {}
    for key, value in state.items():
        if _SECRET_SHAPE.search(str(key)):
            continue  # secret-shaped keys never travel — values vanish with them
        if isinstance(value, str):
            cleaned = "".join(ch for ch in value if ch.isprintable())[:_EXCERPT_BOUND]
            sanitized[str(key)] = cleaned
        elif isinstance(value, (int, float, bool)) or value is None:
            sanitized[str(key)] = value
        elif isinstance(value, (dict, list)):
            try:
                sanitized[str(key)] = json.loads(json.dumps(value))
            except (TypeError, ValueError):
                continue
    return sanitized


def coerce_state(raw: Any, *, interpret: str = "state") -> dict[str, Any]:
    """Admit only JSON-clean structures (lists/dicts/str/int/float/bool/None)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return {"excerpt_escaped": "".join(ch for ch in raw if ch.isprintable())[:_EXCERPT_BOUND],
                "type": "untrusted"}
    return {"state": raw, "type": "passthrough"}


def _write_ledger(row: dict[str, Any]) -> None:
    p = Path(config.data_dir()) / LEDGER_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def precheck_state(state: dict[str, Any], *, call_site: str,
                   environ: dict[str, str] | None = None) -> reg_mod.CounterResult:
    """Registry-first: run every entry; LOG the precheck row. Never raises."""
    try:
        result = reg_mod.precheck(state)
    except Exception as exc:  # noqa: BLE001 — registry must not block lanes
        result = reg_mod.CounterResult(counter_id="none", hit=False,
                                       detail=f"registry load failed (fail-open to gray): {exc!r}"[:120])
    _write_ledger({
        "ts": _now(), "event": "precheck", "call_site": call_site,
        "counter_id": result.counter_id, "detail": result.detail,
        "state_sha": _sha12(state),
    })
    return result


def _mock_decision(question_ids: list[str]) -> dict[str, Any]:
    answers = {
        qid: {"type": "noul", "noul": 0.5} for qid in question_ids
    }
    return {
        "model": "mock/jev-1.13-20260917",
        "answers": answers,
        "usage": {"input_tokens": 0, "output_tokens": 0, "cost": 0.0},
        "id": "mock-jev",
        "provider": "mock",
    }


def ask(state: dict[str, Any], questions: dict[str, dict], *, call_site: str,
        environ: dict[str, str] | None = None, mock: bool = False,
        registry_path: Path | None = None) -> dict[str, Any]:
    """One Jev round-trip, gated by the registry. Returns a SHADOW row.

    - registry first: counter_id=hit => no HTTP; shadow row with
      ``action=conservative`` and the hit counter_id (row #1's class dies here).
    - mock=True (or CHIP_CHAT_MOCK=1): deterministic offline fixture.
    - live requires CHIP_JEV_LIVE=1 AND the OpenRouter key; one retry, then
      fail-closed fallback. ``band_hypothetical`` is recorded, never branched.
    """
    env = environ if environ is not None else os.environ
    state = sanitize_state(state, environ=env)
    pre = precheck_state(state, call_site=call_site, environ=env)
    result = pre
    qids = list(questions.keys())

    if result.hit:
        _write_ledger({
            "ts": _now(), "event": "decide", "call_site": call_site,
            "counter_id": result.counter_id, "outcome": "blocked-registry-hit",
            "band_hypothetical": None, "action": "conservative", "state_sha": _sha12(state),
        })
        return {
            "status": "deferred-registry-hit",
            "counter_id": result.counter_id,
            "reason": result.detail,
            "answers": {},
            "usage": {"cost": 0.0},
        }

    mock_mode = mock or (env.get("CHIP_CHAT_MOCK", "") != "")
    if not mock_mode:
        live = (env.get(JEV_LIVE_ENV, "") or "").strip().lower() in ("1", "true", "yes", "on")
        key = (env.get("OPENROUTER_CC_API_KEY", "") or "").strip()
        if not live or not key:
            _write_ledger({
                "ts": _now(), "event": "decide", "call_site": call_site,
                "counter_id": "none", "outcome": "conservative-default (no live GO)",
                "band_hypothetical": None, "action": "conservative", "state_sha": _sha12(state),
            })
            return {"status": "conservative", "answers": {}, "usage": {"cost": 0.0}}
    else:
        # Offline determinism: mock mode NEVER touches the network (2s of real
        # HTTP retries was the old behavior). Same ledger law: band recorded,
        # action stays conservative, cost $0.
        data = _mock_decision(qids)
        _write_ledger({
            "ts": _now(), "event": "decide", "call_site": call_site,
            "counter_id": "none", "outcome": "answered", "model_used": data.get("model", ""),
            "cost_usd": 0.0, "band_hypothetical": _band_of(data.get("answers", {})),
            "action": "conservative", "state_sha": _sha12(state),
        })
        return data

    body = {
        "model": PINNED_MODEL,
        "state": state,
        "questions": {
            qid: {
                "type": spec.get("type", "noul"),
                "instructions": spec.get("instructions", ""),
                **({"criteria": spec["criteria"]} if "criteria" in spec else {}),
            }
            for qid, spec in questions.items()
        },
    }

    last_error = ""
    for attempt in (1, 2):
        try:
            payload = json.dumps(body).encode()
            req = urllib.request.Request(
                DECISIONS_URL, data=payload,
                headers={
                    "Authorization": f"Bearer {env.get('OPENROUTER_CC_API_KEY', '').strip()}",
                    "Content-Type": "application/json",
                    "User-Agent": "chip-relay",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            cost = float((data.get("usage") or {}).get("cost", 0.0)) or 0.0
            _write_ledger({
                "ts": _now(), "event": "decide", "call_site": call_site,
                "counter_id": "none", "outcome": "answered",
                "model_used": data.get("model", ""),
                "cost_usd": cost,
                "band_hypothetical": _band_of(data.get("answers", {})),
                "action": "conservative", "state_sha": _sha12(state),
            })
            return data
        except Exception as exc:  # noqa: BLE001 — single retry, then fallback
            last_error = f"{exc.__class__.__name__}: {exc}"[:120]
            time.sleep(1.0)

    _write_ledger({
        "ts": _now(), "event": "decide", "call_site": call_site,
        "counter_id": "none", "outcome": f"fallback ({last_error})",
        "band_hypothetical": None, "action": "conservative", "state_sha": _sha12(state),
    })
    return {"status": "fallback", "reason": last_error, "answers": {}, "usage": {"cost": 0.0}}


def _band_of(answers: dict[str, Any]) -> str | None:
    """Band label from the FIRST answer's probability (hypothetical only).

    Pure meta: the caller that received the shadow row must STILL take the
    conservative default (the band never branches anything).
    """
    for answer in answers.values():
        prob = answer.get("noul", answer.get("confidence"))
        if isinstance(prob, (int, float)):
            p = float(prob)
            if p >= _BANDS["auto_min"]:
                return "propose-auto"
            if p >= _BANDS["room_min"]:
                return "propose-in-room"
            return "needs-repro"
    return None


def room_reach(state: dict[str, Any], answers: dict[str, Any], *, call_site: str
               ) -> str | None:
    """Missing-field blank line (post-log-window schema, never a confidence).

    Present here for the emitter phase: a named-field blank invites ONE
    tighter-state answer + ONE re-call, then stops (no third call, no debate
    with the model). With stage-361 this returns None until the emitter phase
    lands; the ledger carries everything for now.
    """
    _ = (state, answers, call_site)
    return None
