"""Standup signal writer (RAZ-329/RAZ-331 contract; Lyla binding 2026-09-28).

The chip emitter produces the daily Chip Chat standup signal that Oracle's
silence probe reads. Consumer precedence (their side): CHIP_CHAT_STANDUP_SIGNAL_URL
-> CHIP_CHAT_STANDUP_SIGNAL_FILE -> legacy URL -> legacy file. This writer
satisfies the FILE side always and the URL side when the env is configured.

BINDING ANTI-SILENCE LAW (Lyla 2026-09-28): "an rc=3 correlated-dark day is
NOT silence — absence of a signal file is the one thing that is never
allowed." The signal is therefore written on EVERY run, dry-run included;
only the SITREP/Discord post keeps its --post gate.

JSON shape (docs/ops/CHIP_CHAT_STANDUP_SIGNAL.md): top-level
``source: "chip_chat"`` + PT calendar date keys (YYYY-MM-DD); per-kind fields
``received`` / ``verified_as_of`` (ISO-8601 UTC) / ``missed_payload``;
``collectors_live`` on gymlog during soak.

Semantics (contract text as-written; the received-vs-present collision is
FILED on RAZ-329, not resolved here):
- ``received: true`` = the digest job completed for this kind — an honest
  MISS day still receives the report (banner, not poison). Lyla's critical
  item: rc=3 must write received:true + missed_payload:true + verified_as_of.
- ``missed_payload: true`` = explicit miss banner (stall / garbage / silent).
- ``verified_as_of`` = ISO-8601 UTC of this writer pass (only with received).
- ``collectors_live`` = true ONLY when the gymlog kind's report came from
  real collector receipts (never stub silence).
- A lane absent from the run = ``received: false`` (contract-allowed, soak-safe:
  "must set missed_payload: true or leave received: false") — an honest miss
  is on-record, never off-record.

Fail-closed: garbage receipts make the kind's fields HONEST (banner), never
crash the writer, never skip the file write. Signal kinds are restricted to
homelab|gymlog (the razzorstack StandupKind contract); other lanes stay in
the emitter's stdout report but are EXCLUDED from the signal (an empty fields
set beats a wrong-shape one).
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from chip import standup

SIGNAL_SOURCE = "chip_chat"
SIGNAL_KINDS = ("homelab", "gymlog")

SIGNAL_FILE_ENV = "CHIP_CHAT_STANDUP_SIGNAL_FILE"
SIGNAL_URL_ENV = "CHIP_CHAT_STANDUP_SIGNAL_URL"


def _pt_now() -> datetime:
    # PDT/UTC-7 for the soak window; 11:25 PT emission lands mid-day so the
    # DST hour never straddles the PT date boundary here (documented).
    return datetime.now(timezone(timedelta(hours=-7)))


def _iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _config_path(environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    return (env.get(SIGNAL_FILE_ENV, "") or "").strip()


def build_signal(
    receipts: list[Mapping[str, Any]],
    *,
    verified_as_of: str | None = None,
    pt_date: str | None = None,
) -> dict[str, Any]:
    """Contract JSON for one emission window (deterministic under fixed fields).

    ``receipts`` = finished emitter receipts for the window (chip.standup
    shapes, finalized). A RECEIPT-GARBAGE or silent lane still renders
    received=true + missed_payload=true — the digest JOB ran and produced an
    honest report; that is not silence (CRITICAL alignment item).
    """
    date_key = pt_date or _pt_now().strftime("%Y-%m-%d")
    day: dict[str, Any] = {}
    for lane in SIGNAL_KINDS:
        rec = next((r for r in receipts if r.get("standup") == lane), None)
        if rec is None:
            day[lane] = {"received": False}
            continue
        fin = standup.finalize_receipt(dict(rec))
        if fin["_schema_errors"]:
            # Digest job ran but lane receipt garbage — still signal (anti-silence).
            day[lane] = {
                "received": True,
                "verified_as_of": verified_as_of,
                "missed_payload": True,
            }
            if lane == "gymlog":
                day[lane]["collectors_live"] = False
            continue
        if not verified_as_of:
            raise ValueError(
                "verified_as_of: caller must supply a real ISO-8601 UTC stamp "
                "when a kind is received — a placeholder would be golden-1 fiction"
            )
        entry: dict[str, Any] = {
            "received": True,
            "verified_as_of": verified_as_of,
            "missed_payload": bool(fin["payload"]["source"] != "live"),
        }
        if lane == "gymlog":
            entry["collectors_live"] = bool(fin["payload"]["source"] == "live")
        day[lane] = entry
    return {"source": SIGNAL_SOURCE, **{date_key: day}}


def merge_signal(existing: Any, update: Mapping[str, Any]) -> dict[str, Any]:
    """Read-modify-write: today's date key REPLACES exactly; old days persist.

    Garbage existing file is treated as absent (fail-recoverable; the write
    never dies because of yesterday's malformed history).
    """
    if not isinstance(existing, dict):
        return dict(update)
    out = dict(existing)
    out.setdefault("source", update.get("source", SIGNAL_SOURCE))
    for key in update:
        if key == "source" and out.get("source") != update["source"]:
            out[key] = update[key]  # contract keeps chip_chat as the producer's own stamp
        else:
            out[key] = update[key]
    return out


def write_signal(
    signal: Mapping[str, Any],
    path: str | None,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Write/merge the signal file (mkdir -p). Empty path AND no env ->
    visible skip note (caller relays it in the SITREP; it is never silent)."""
    target = path or _config_path(environ)
    if not target:
        return {"wrote": "", "note": "signal file skipped: no path/no env"}
    p = os.path.expanduser(target)
    parent = os.path.dirname(p)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        existing = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        existing = None
    merged = merge_signal(existing, signal)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return {"wrote": p, "note": "signal written"}


def post_signal_url(
    signal: Mapping[str, Any],
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Expose the signal via URL when CHIP_CHAT_STANDUP_SIGNAL_URL is set
    (env-present = intent; same explicitness discipline as --post)."""
    env = environ if environ is not None else os.environ
    url = (env.get(SIGNAL_URL_ENV, "") or "").strip()
    if not url:
        return {"url": "", "note": "url skipped: no env"}
    body = json.dumps(signal, sort_keys=True).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "chip-relay"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        ok = 200 <= resp.status < 300
    return {"url": url, "note": "url posted" if ok else f"url http {resp.status}"}
