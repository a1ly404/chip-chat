"""CHIP_FEED_TEXT_SOURCES — structured text-source ingestion (2026-09-28).

The feed has real structured sources (health endpoints). For text-side lanes
(advisor tools, PGS scheduler logs, standup emitters), this module ingests an
append-only **JSONL** watch file per source — one JSON object per line, no
prose scraping (a prose-only source is ``source=silent`` by the emission
contract, docs/STANDUP_EMISSION_SPEC.md; MONITOR_CASES signature classes).

Lines are UNTRUSTED DATA (id:untrusted-data): scanned at most for the three
machine-shaped signatures below, never executed, never narrated.

Signature classes:
1. ``advisor-timeout-banner`` — bounded-fail banner text:
   ``Command [...] timed out after <N> seconds`` -> alert key ``stage1-advisor``,
   detail ``timeout=<N>s`` (chip DETECTS only; diagnosis parks homelab).
2. ``pgs-zero-production`` — structured counters ``ran`` true AND
   ``ok==0 AND skipped==0 AND error==0`` -> a zero-progress night: first
   night log-only (streak state), **two consecutive zero-nights = alert**
   (tell-Ally-once via the feed's existing cooldown + verify-twice laws).
   Any night with real production resets that source's streak.
3. ``standup-stall`` — a validated standup receipt with ``source=silent``
   -> per-lane stall alert carrying ``payload_age``; if >=2 lanes are silent
   in the same window, ``chip.standup.correlated_dark`` collapses them into
   ONE alert (key ``correlated-dark``) — the correlated-dark law.

Alert shape is the feed's own ``{key, detail, ok: False}`` so the EXISTING
noise floor, cooldown and post laws apply unchanged — no new posting path.

Determinism: per-line class order (advisor banners then pgs counters for
that line; standup/correlated-dark appended once at end of the pass);
given identical input files the output is identical. Fail-closed:
malformed lines are counted in ``ignored`` (visible in the ingest
receipt), never dropped silently.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from chip import standup

TEXT_SOURCES_ENV = "CHIP_FEED_TEXT_SOURCES"

# The advisor bounded-fail banner (observed 2026-09-27 wall).
_ADVISOR_RE = re.compile(r"Command \[.{0,200}?\] timed out after (\d{1,5}) seconds")


def _path(path: str) -> Path:
    return Path(path)


def parse_jsonl(text: str) -> tuple[list[dict[str, Any]], int]:
    """Parse a JSONL blob -> (objects, ignored_count). Malformed lines counted."""
    objs: list[dict[str, Any]] = []
    ignored = 0
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            ignored += 1
            continue
        if isinstance(obj, dict):
            objs.append(obj)
        else:
            ignored += 1
    return objs, ignored


def _advisor_alerts(obj: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = obj.get("text")
    if not isinstance(text, str):
        return []
    m = _ADVISOR_RE.search(text)
    if not m:
        return []
    return [{"key": "stage1-advisor", "detail": f"timeout={m.group(1)}s", "ok": False}]


def _pgs_zero(obj: Mapping[str, Any]) -> bool | None:
    """True = zero-production night; False = real production night; None = not a pgs receipt."""
    needed = ("ran", "ok", "skipped", "error")
    if not all(k in obj for k in needed):
        return None
    ran, ok, skipped, error = obj["ran"], obj["ok"], obj["skipped"], obj["error"]
    if not isinstance(ran, bool) or not all(
        isinstance(v, int) and not isinstance(v, bool) for v in (ok, skipped, error)
    ):
        return None
    return bool(ran) and ok == 0 and skipped == 0 and error == 0


def ingest_lines(
    lines: Iterable[Any],
    prev_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify JSONL objects -> feed alerts for the three signature classes.

    Deterministic class order: advisor banners, pgs zero-production,
    standup stalls / correlated-dark.
    """
    streaks: dict[str, int] = dict((prev_state or {}).get("pgs_zero_streak", {}))
    alerts: list[dict[str, Any]] = []
    ignored = 0
    fired_pgs: set[str] = set()  # one pgs alert per source per pass (a tail can carry 2 zero-nights)
    lanes: list[dict[str, Any]] = []
    for obj in lines:
        if not isinstance(obj, Mapping):
            ignored += 1
            continue
        alerts.extend(_advisor_alerts(obj))
        blob = json.dumps(obj, default=str)
        if "No such image" in blob:
            alerts.append({
                "key": "no-such-image",
                "detail": "tool:text-source.no-such-image",
                "ok": False,
                "receipt": (
                    "claim: monitoring log line No such image\n"
                    "status: refused\n"
                    "evidence: tool:text-source.no-such-image\n"
                    "next: al-9501-dead-image stays propose"
                ),
            })
        zero = _pgs_zero(obj)
        if zero is not None:
            sid = str(obj.get("source_id") or "pgs")
            if not isinstance(streaks.get(sid), int):
                streaks[sid] = 0
            streaks[sid] = streaks[sid] + 1 if zero else 0
            if zero and streaks[sid] >= 2 and sid not in fired_pgs:
                fired_pgs.add(sid)
                alerts.append({
                    "key": "pgs-zero-production",
                    "detail": (
                        f"{sid}: zero-progress night {streaks[sid]} in a row "
                        f"(ran=true ok=0 skipped=0 error=0)"
                    ),
                    "ok": False,
                })
        rec = obj.get("standup_receipt")
        if rec is None:
            lane = None
        elif isinstance(rec, Mapping):
            fin = standup.finalize_receipt(dict(rec))
            lane = fin if fin["_schema_errors"] == [] and fin["payload"]["source"] == "silent" else None
            if fin["_schema_errors"]:
                ignored += 1  # receipt-shaped garbage is visible, never silent
        else:
            ignored += 1
            fin, lane = None, None
        if lane is not None:
            lanes.append(lane)
    cd = standup.correlated_dark(lanes)
    if cd["silent"]:
        worst = cd["worst_age_h"]
        age_txt = "never" if worst is None else f"age_h={worst:g}h"
        if cd["correlated"]:
            ids = "+".join(cd["silent"])  # already sorted by the detector
            alerts.append({
                "key": "correlated-dark",
                "detail": f"{ids} — payload pipeline silent ({age_txt}) — one root cause",
                "ok": False,
            })
        else:
            lane = lanes[0]
            age = lane["payload"]["age_h"]
            lane_age = "never" if age is None else f"{age:g}h"
            alerts.append({
                "key": f"standup:{lane['standup']}",
                "detail": f"stall source=silent payload_age={lane_age}",
                "ok": False,
            })
    return {"alerts": alerts, "state": {"pgs_zero_streak": streaks}, "ignored": ignored + cd.get("ignored", 0)}


def read_new_lines(path: str, offset: int) -> tuple[list[dict[str, Any]], int, int]:
    """Tail an append-only JSONL file from ``offset`` (rotation-safe).

    - Complete lines only: a trailing partial line (no terminating newline)
      is held back until the producer finishes it.
    - Rotation/truncation (file shrinks below the stored offset) -> reprocess
      the whole current file from 0.
    Returns (objects, new_offset, ignored).
    """
    p = _path(path)
    if not p.is_file():
        return [], offset, 0
    raw = p.read_bytes()
    size = len(raw)
    if size < offset:
        offset = 0
    chunk = raw[offset:]
    if not chunk:
        return [], offset, 0
    if chunk.endswith(b"\n"):
        complete, new_bytes = chunk, len(chunk)
    elif b"\n" in chunk:
        head, _, _tail = chunk.rpartition(b"\n")
        complete, new_bytes = head + b"\n", head.count(b"\n") + 1
    else:
        return [], offset, 0  # nothing complete yet
    objs, ignored = parse_jsonl(complete.decode("utf-8", errors="replace"))
    return objs, offset + new_bytes, ignored


def ingest_text_files(
    paths: Iterable[str],
    state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read new JSONL lines from every watch file -> alerts + new state.

    Persisted state shape (chip_state/text_sources.json for the caller):
    ``{"offsets": {path: int}, "pgs_zero_streak": {source_id: int}}``.
    Files that vanish or produce nothing are reported as ``skipped``, never
    dropped silently (golden 5).
    """
    offsets = dict((state or {}).get("offsets", {}))
    carried = {"pgs_zero_streak": dict((state or {}).get("pgs_zero_streak", {}))}
    alerts: list[dict[str, Any]] = []
    ignored = 0
    skipped: list[str] = []
    for p in paths:
        objs, new_offset, ign = read_new_lines(p, int(offsets.get(str(p), 0)))
        if ign or objs or new_offset != offsets.get(str(p), 0):
            ignored += ign
            offsets[str(p)] = new_offset
        else:
            skipped.append(str(p))
        out = ingest_lines(objs, carried)
        alerts.extend(out["alerts"])
        carried = out["state"]
    return {
        "alerts": alerts,
        "state": {"offsets": offsets, "pgs_zero_streak": carried["pgs_zero_streak"]},
        "ignored": ignored,
        "skipped": skipped,
    }

