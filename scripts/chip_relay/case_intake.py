"""Freeform channel text → case file. Mock parser. No Linear HTTP."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from chip import pack_values, system_pack

REPO = Path(__file__).resolve().parents[2]
REQUIRED = ("id", "description", "repro_context", "evidence_path")
PENDING = "PENDING_CLARIFY"

Parser = Callable[[str, str], dict[str, Any]]


def load_limits(path: Path | None = None) -> dict[str, Any]:
    raw = (path or system_pack.surface("case_intake", system_pack.load_pack())).read_text(encoding="utf-8")
    out: dict[str, Any] = {}
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, val = line.split(":", 1)
        val = val.strip().strip('"')
        if re.fullmatch(r"-?\d+", val):
            out[key.strip()] = int(val)
        elif re.fullmatch(r"-?\d+\.\d+", val):
            out[key.strip()] = float(val)
        else:
            out[key.strip()] = val
    override = os.environ.get("CHIP_CASE_INTAKE_WRITE_ROOT", "").strip()
    if override:
        out["write_root"] = override
    elif out.get("write_root") == "@repo":
        out["write_root"] = str(REPO)
    return out


def load_taxonomy(path: Path | None = None) -> set[str]:
    data = json.loads((path or system_pack.surface("case_taxonomy", system_pack.load_pack())).read_text(encoding="utf-8"))
    return set(data["classes"])


@dataclass
class Receipt:
    model: str
    tokens: int
    ts: float
    blocked: bool = False
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "tokens": self.tokens,
            "ts": self.ts,
            "blocked": self.blocked,
            "reason": self.reason,
        }


@dataclass
class CaseFile:
    id: str
    description: str
    repro_context: str
    evidence_path: str
    fingerprint_class: str = ""
    related_ticket: str = ""
    receipt: Receipt | None = None
    synthetic: bool = False

    def as_dict(self) -> dict[str, Any]:
        ticket_field = pack_values.case_intake_ticket_field_name()
        body = {
            "id": self.id,
            "description": self.description,
            "repro_context": self.repro_context,
            "evidence_path": self.evidence_path,
            "fingerprint_class": self.fingerprint_class,
            ticket_field: self.related_ticket,
        }
        if self.receipt:
            body["receipt"] = self.receipt.as_dict()
        if self.synthetic:
            body["synthetic"] = True
        return body


@dataclass
class PendingClarify:
    missing: list[str]
    cycles: int
    receipt: Receipt


@dataclass
class IntakeResult:
    kind: str
    case: CaseFile | None = None
    pending: PendingClarify | None = None
    receipt: Receipt | None = None
    note: str = ""
    posts: list[str] = field(default_factory=list)


def _receipt(model: str, tokens: int, *, blocked: bool = False, reason: str = "", now: float | None = None) -> Receipt:
    return Receipt(model=model, tokens=tokens, ts=now if now is not None else time.time(), blocked=blocked, reason=reason)


def budget_decision(monthly_usd: float, limits: dict[str, Any]) -> str:
    if monthly_usd >= float(limits["budget_hard_usd"]):
        return "block"
    if monthly_usd >= float(limits["budget_warn_usd"]):
        return "warn"
    return "ok"


def parse_freeform(
    msg: str,
    *,
    parser: Parser,
    limits: dict[str, Any],
    monthly_usd: float = 0.0,
    model: str | None = None,
    now: float | None = None,
    skip_spend_gate: bool = False,
) -> IntakeResult:
    """Fail closed. Budget checked before parser runs unless skip_spend_gate (operator-approved window)."""
    primary = model or str(limits["primary_model"])
    decision = "ok" if skip_spend_gate else budget_decision(monthly_usd, limits)
    if decision == "block":
        rec = _receipt(primary, 0, blocked=True, reason="budget_hard", now=now)
        return IntakeResult(kind="blocked", receipt=rec, note="budget hard cap")
    rec_warn = decision == "warn"
    try:
        payload = parser(msg, primary)
    except RuntimeError as exc:
        if "non-200" not in str(exc):
            rec = _receipt(primary, 0, blocked=True, reason="parse_error", now=now)
            return IntakeResult(kind="dead_letter", receipt=rec, note=str(exc))
        fallback = str(limits["fallback_model"])
        try:
            payload = parser(msg, fallback)
            primary = fallback
        except Exception as exc2:  # noqa: BLE001 — one retry then dead-letter
            rec = _receipt(fallback, 0, blocked=True, reason="fallback_failed", now=now)
            return IntakeResult(kind="dead_letter", receipt=rec, note=str(exc2))
    if not isinstance(payload, dict):
        rec = _receipt(primary, 0, blocked=True, reason="non_object", now=now)
        return IntakeResult(kind="dead_letter", receipt=rec, note="parser output not object")
    tokens = int(payload.get("_tokens") or 0)
    rec = _receipt(primary, tokens, blocked=False, reason="warn" if rec_warn else "", now=now)
    if payload.get("marker") == PENDING or any(not str(payload.get(k) or "").strip() for k in REQUIRED):
        missing = [k for k in REQUIRED if not str(payload.get(k) or "").strip()]
        return IntakeResult(
            kind="pending",
            pending=PendingClarify(missing=missing or ["unknown"], cycles=int(payload.get("_cycles") or 1), receipt=rec),
            receipt=rec,
        )
    case = CaseFile(
        id=str(payload["id"]).strip(),
        description=str(payload["description"]).strip(),
        repro_context=str(payload["repro_context"]).strip(),
        evidence_path=str(payload["evidence_path"]).strip(),
        fingerprint_class=str(payload.get("fingerprint_class") or "").strip(),
        related_ticket=pack_values.related_ticket_from_mapping(payload),
        receipt=rec,
    )
    return IntakeResult(kind="case", case=case, receipt=rec)


def match_taxonomy(case: CaseFile, classes: set[str]) -> str:
    if case.fingerprint_class in classes:
        return "known_class"
    return "novel"


def local_dedupe(case: CaseFile, cache: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in cache:
        if row.get("id") == case.id or (
            case.fingerprint_class and row.get("fingerprint_class") == case.fingerprint_class and row.get("description") == case.description
        ):
            return row
    return None


def correlate_dark(cases: list[CaseFile], *, now: float, limits: dict[str, Any]) -> CaseFile | None:
    window = float(limits["correlated_dark_window_s"])
    need = int(limits["correlated_dark_max_cases"])
    fresh = [c for c in cases if c.receipt and now - c.receipt.ts <= window and not c.fingerprint_class]
    shapes: dict[str, list[CaseFile]] = {}
    for case in fresh:
        shapes.setdefault(case.description, []).append(case)
    for group in shapes.values():
        if len(group) >= need:
            return group[0]
    return None


def echo_for_correction(
    case: CaseFile,
    *,
    actor: str,
    originator: str,
    operator_ids: set[str],
    elapsed_s: float,
    limits: dict[str, Any],
    correction: str = "",
) -> str:
    if elapsed_s > float(limits["echo_window_s"]):
        return "park"
    if not correction:
        return "waiting"
    if actor == originator or actor in operator_ids:
        return "accepted"
    return "ignored"


class PendingStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def put(self, key: str, *, now: float, cycles: int) -> None:
        self.rows[key] = {"now": now, "cycles": cycles}

    def tick(self, key: str, *, now: float, limits: dict[str, Any]) -> str:
        row = self.rows.get(key)
        if row is None:
            return "absent"
        if now - float(row["now"]) > float(limits["pending_clarify_ttl_s"]):
            return "dead_letter"
        if int(row["cycles"]) >= int(limits["pending_clarify_max_cycles"]):
            return "dead_letter"
        return "open"


class StrikeBook:
    def __init__(self) -> None:
        self.blocked_until: dict[str, float] = {}

    def record_failure(self, case_class: str, *, now: float, limits: dict[str, Any], log_path: Path, detail: str) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": now, "case_class": case_class, "detail": detail, "model": limits["primary_model"]})
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self.blocked_until[case_class] = now + float(limits["one_strike_block_s"])

    def allowed(self, case_class: str, *, now: float) -> bool:
        return now >= self.blocked_until.get(case_class, 0.0)


class OperatorPager:
    def __init__(self) -> None:
        self.last: dict[str, float] = {}

    def ping(self, case_class: str, summary: str, link: str, *, now: float, limits: dict[str, Any]) -> dict[str, Any] | None:
        from chip import system_pack

        prev = self.last.get(case_class)
        if prev is not None and now - prev < float(limits["page_once_per_class_s"]):
            return None
        self.last[case_class] = now
        mention = system_pack.pack_identity_string("pager_mention", default="@oncall")
        return {"to": mention, "case_class": case_class, "summary": summary, "link": link}


def dead_letter_path(case_id: str, ts: float, root: Path | None = None) -> Path:
    stamp = str(int(ts))
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", case_id) or "unknown"
    return (root or REPO / "docs" / "cases" / "dead_letter") / f"{safe}_{stamp}.json"


def write_dead_letter(case_id: str, body: dict[str, Any], *, ts: float, root: Path | None = None) -> Path:
    path = dead_letter_path(case_id, ts, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return path


def enabled(environ: dict[str, str]) -> bool:
    return environ.get("CASE_INTAKE_ENABLED", "0").strip() == "1"
