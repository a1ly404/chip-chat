"""Ladder telemetry: durable spend-by-tier, lower-only per-tier caps, morning table.

Everything is derived from ``case_intake/ladder_receipts.jsonl`` (append-only, survives
restarts). Caps live in ``case_intake/ladder_caps.json`` and may only be lowered.
Breaches are appended to ``case_intake/ladder_cap_breaches.jsonl`` — never sent.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from chip.store import append_jsonl
from chip_relay.case_intake_ladder import VERDICTS

DEFAULT_CAPS_USD: dict[str, float] = {"pm": 0.50, "executor": 2.00, "verifier": 0.75}
REOPEN_WINDOW_S = 7 * 86400


def _root(data_root: Path) -> Path:
    return data_root / "case_intake"


def caps_path(data_root: Path) -> Path:
    return _root(data_root) / "ladder_caps.json"


def breaches_path(data_root: Path) -> Path:
    return _root(data_root) / "ladder_cap_breaches.jsonl"


def load_caps(data_root: Path) -> dict[str, float]:
    caps = dict(DEFAULT_CAPS_USD)
    p = caps_path(data_root)
    if p.is_file():
        try:
            stored = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stored = {}
        for k, v in stored.items():
            if k in caps:
                caps[k] = min(caps[k], float(v))
    return caps


def lower_cap(data_root: Path, tier: str, usd: float) -> dict[str, float]:
    """Lower one tier cap. Raising is refused (caps are lower-only)."""
    caps = load_caps(data_root)
    if tier not in caps:
        raise KeyError(tier)
    if float(usd) > caps[tier]:
        raise ValueError(f"cap raise refused: {tier} {caps[tier]} -> {usd}")
    caps[tier] = float(usd)
    p = caps_path(data_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(caps, indent=2) + "\n", encoding="utf-8")
    return caps


def read_rows(data_root: Path, *, month: str | None = None) -> list[dict[str, Any]]:
    p = _root(data_root) / "ladder_receipts.jsonl"
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if month and time.strftime("%Y-%m", time.gmtime(float(r.get("ts") or 0))) != month:
            continue
        rows.append(r)
    return rows


def spend_by_tier(rows: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        if r.get("tier") in DEFAULT_CAPS_USD:
            out[r["tier"]] += float(r.get("cost_usd") or 0.0)
    return dict(out)


def cap_allows(data_root: Path, tier: str, *, case_id: str = "") -> bool:
    """Pre-call gate. False (and a breach row on disk) when the tier's month spend >= cap."""
    if tier not in DEFAULT_CAPS_USD:
        return True
    month = time.strftime("%Y-%m", time.gmtime())
    spent = spend_by_tier(read_rows(data_root, month=month)).get(tier, 0.0)
    cap = load_caps(data_root)[tier]
    if spent < cap:
        return True
    append_jsonl(breaches_path(data_root), {
        "ts": time.time(), "tier": tier, "month": month, "spent_usd": round(spent, 6),
        "cap_usd": cap, "case_id": case_id, "sent": False,
    })
    return False


def record_outcome(data_root: Path, case_id: str, confirmed_verdict: str, *, source: str,
                   synthetic: bool = False) -> dict[str, Any]:
    """Human/outcome confirmation joined on case_id by ``accuracy`` (WI-366)."""
    row: dict[str, Any] = {"ts": time.time(), "case_id": case_id, "tier": "outcome", "event": "outcome",
                           "confirmed_verdict": confirmed_verdict, "source": source}
    if synthetic:
        row["synthetic"] = True
    append_jsonl(_root(data_root) / "ladder_receipts.jsonl", row)
    return row


OUTCOME_RE = re.compile(r"^\s*outcome:\s*(\S+)\s+(" + "|".join(VERDICTS) + r")\s*$", re.IGNORECASE)


def parse_outcome_line(text: str) -> tuple[str, str] | None:
    """``outcome: <case_id> <verdict>`` (frozen band only) → (case_id, verdict)."""
    m = OUTCOME_RE.match(text or "")
    return (m.group(1), m.group(2).lower()) if m else None


def cases_dir(data_root: Path) -> Path:
    return _root(data_root) / "cases"


def attach_outcome_to_case_file(data_root: Path, case_id: str, outcome: dict[str, Any]) -> bool:
    """Write ``outcome`` onto the stored case JSON when a GO/confirmation lands."""
    root = cases_dir(data_root)
    if not root.is_dir():
        return False
    for path in root.glob("*.json"):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(body.get("id") or "") != case_id:
            continue
        body["outcome"] = outcome
        path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        return True
    return False


def confirm_outcome(data_root: Path, case_id: str, verdict: str, *, source: str) -> dict[str, Any]:
    """Record a confirmation for a case the ladder summarized; synthetic flag inherited from the summary."""
    summaries = [r for r in read_rows(data_root) if r.get("event") == "summary" and str(r.get("case_id")) == case_id]
    if not summaries:
        return {"ok": False, "case_id": case_id, "error": "no ladder summary for case_id"}
    summary = summaries[-1]
    row = record_outcome(data_root, case_id, verdict, source=source,
                         synthetic=bool(summary.get("synthetic")))
    outcome_doc = {
        "confirmed_verdict": verdict,
        "source": source,
        "ts": row["ts"],
        "ladder_verdict": summary.get("verdict"),
        "correct": summary.get("verdict") == verdict,
        "synthetic": bool(summary.get("synthetic")),
    }
    wrote = attach_outcome_to_case_file(data_root, case_id, outcome_doc)
    return {"ok": True, "row": row, "ladder_verdict": summary.get("verdict"),
            "correct": summary.get("verdict") == verdict, "case_file_updated": wrote}


def accuracy(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-lane first-try metrics: first-pass verify rate, bounces/case, verdict correctness."""
    lane_of: dict[str, str] = {}
    first_verify: dict[str, bool] = {}
    confirmed: dict[str, str] = {}
    for r in rows:
        cid = str(r.get("case_id") or "")
        if r.get("tier") == "pm" and isinstance(r.get("frame"), dict):
            lane_of.setdefault(cid, str(r["frame"].get("lane") or "unknown"))
        elif r.get("tier") == "verifier" and cid not in first_verify:
            first_verify[cid] = r.get("passed") is True
        elif r.get("event") == "outcome":
            confirmed[cid] = str(r.get("confirmed_verdict"))
    out: dict[str, dict[str, Any]] = {}
    for s in (r for r in rows if r.get("event") == "summary"):
        cid = str(s.get("case_id") or "")
        m = out.setdefault(lane_of.get(cid, "unknown"), {"cases": 0, "verified_attempted": 0, "first_pass": 0,
                                                         "bounces": 0, "confirmed": 0, "correct": 0})
        m["cases"] += 1
        m["bounces"] += int(s.get("bounces") or 0)
        if cid in first_verify:
            m["verified_attempted"] += 1
            m["first_pass"] += int(first_verify[cid])
        if cid in confirmed:
            m["confirmed"] += 1
            m["correct"] += int(s.get("verdict") is not None and str(s.get("verdict")) == confirmed[cid])
    verified = [s for s in rows if s.get("event") == "summary" and s.get("status") == "verified" and s.get("fingerprint")]
    for s in verified:
        later = [x for x in rows if x.get("event") == "summary" and x is not s
                 and x.get("fingerprint") == s["fingerprint"] and bool(x.get("synthetic")) == bool(s.get("synthetic"))
                 and 0 < float(x.get("ts") or 0) - float(s.get("ts") or 0) <= REOPEN_WINDOW_S]
        m = out[lane_of.get(str(s.get("case_id") or ""), "unknown")]
        m["verified_fp"] = m.get("verified_fp", 0) + 1
        m["reopened"] = m.get("reopened", 0) + int(bool(later))
    for m in out.values():
        m.setdefault("verified_fp", 0)
        m.setdefault("reopened", 0)
        m["reopen_rate"] = round(m["reopened"] / m["verified_fp"], 3) if m["verified_fp"] else None
        m["first_pass_rate"] = round(m["first_pass"] / m["verified_attempted"], 3) if m["verified_attempted"] else None
        m["bounces_per_case"] = round(m["bounces"] / m["cases"], 3)
        m["verdict_accuracy"] = round(m["correct"] / m["confirmed"], 3) if m["confirmed"] else None
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = [r for r in rows if r.get("event") == "summary"]
    defects: Counter[str] = Counter()
    for r in rows:
        if r.get("tier") == "verifier":
            for d in r.get("defects") or []:
                defects[d.split(":", 2)[1] if d.startswith("claim[") else d.split(":", 1)[0]] += 1
            if not r.get("defects") and r.get("passed") is False:
                defects["model_bounce"] += 1
    return {
        "cases": len(summaries),
        "synthetic_cases": sum(1 for s in summaries if s.get("synthetic")),
        "status": dict(Counter(str(s.get("status")) for s in summaries)),
        "bounces": sum(int(s.get("bounces") or 0) for s in summaries),
        "pages_dry_run": sum(1 for r in rows if r.get("event") == "page_dry_run"),
        "defects": dict(defects),
        "spend_by_tier": {k: round(v, 6) for k, v in spend_by_tier(rows).items()},
        "accuracy_by_lane": accuracy(rows),
    }


def render_table(s: dict[str, Any]) -> str:
    spend = " · ".join(f"{k} ${v:.4f}" for k, v in sorted(s["spend_by_tier"].items())) or "$0"
    status = ", ".join(f"{k}={v}" for k, v in sorted(s["status"].items())) or "none"
    defects = ", ".join(f"{k}={v}" for k, v in sorted(s["defects"].items())) or "none"
    return "\n".join([
        "| workstream | tier | status | live-verified | spend by tier | pages | bounces | defects |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        f"| case-intake ladder | pm/executor/verifier | {status} ({s['cases']} cases, {s['synthetic_cases']} synthetic) "
        f"| {'y' if s['spend_by_tier'].get('verifier') else 'n'} | {spend} | {s['pages_dry_run']} (dry-run) "
        f"| {s['bounces']} | {defects} |",
        "",
        "| lane | cases | first-pass verify | bounces/case | verdict accuracy (confirmed) | reopened ≤7d (verified) |",
        "| --- | --- | --- | --- | --- | --- |",
        *(f"| {lane} | {m['cases']} | {_pct(m['first_pass_rate'])} ({m['first_pass']}/{m['verified_attempted']}) "
          f"| {m['bounces_per_case']} | {_pct(m['verdict_accuracy'])} ({m['correct']}/{m['confirmed']}) "
          f"| {_pct(m['reopen_rate'])} ({m['reopened']}/{m['verified_fp']}) |"
          for lane, m in sorted(s.get("accuracy_by_lane", {}).items())),
    ])


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0%}"
