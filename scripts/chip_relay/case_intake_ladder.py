"""Delegation ladder v1: PM → Executor → Verifier (Grokbot parity window).

Read-only verdict ladder. Tiers *reach* a verdict; they never redefine one —
the verdict set is the frozen gray band (``needs-repro / propose-in-room /
propose-auto``) and no tier executes actions (action law stays with the jev
kit). ``CASE_INTAKE_LADDER`` defaults to **0**.

Tier 3 runs a deterministic receipt bar *before* any model call: every
Executor claim must carry a receipt (kind + ref + excerpt; file refs must
exist). A structural miss bounces without spending tokens. After
``bounce_limit`` bounces the case climbs to the page rung, which is
dry-run only (``escalate_to=operator`` receipt, nothing sent).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from chip.store import append_jsonl
from chip_relay.case_intake_escalation import parse_stage_payload

LADDER_ENV = "CASE_INTAKE_LADDER"
VERDICTS = ("needs-repro", "propose-in-room", "propose-auto")
RECEIPT_KINDS = ("file", "command_output", "case_field")

TIERS: dict[str, dict[str, Any]] = {
    "pm": {
        "model": "z-ai/glm-5.3-flash",
        "max_tokens": 1200,
        "system": (
            "You are the PM tier of a synthetic case ladder. Frame the case for an executor. "
            "Output JSON only: {\"goal\": str, \"acceptance\": [str], \"lane\": str}."
        ),
    },
    "executor": {
        "model": "deepseek/deepseek-v4.1-flash",
        "max_tokens": 2400,
        "system": (
            "You are the Executor tier. Read-only: never propose deletes or destructive fixes. "
            "Using ONLY the case fields given, propose a verdict. Every claim MUST cite a receipt with "
            "kind \"case_field\" and ref EXACTLY one of \"description\" or \"repro_context\" (bare key, "
            "no prefix). excerpt MUST be a short verbatim substring copied character-for-character from "
            "that field (no paraphrase, no added quotes). At most 4 claims; keep summary under 40 words. "
            "No markdown fences. Output JSON only: {\"verdict\": "
            "\"needs-repro\"|\"propose-in-room\"|\"propose-auto\", \"summary\": str, \"claims\": "
            "[{\"claim\": str, \"receipt\": {\"kind\": str, \"ref\": str, \"excerpt\": str}}]}."
        ),
    },
    "verifier": {
        "model": "qwen/qwen3.7-flash",
        "max_tokens": 1500,
        "system": (
            "You are the Verifier tier. Check that each claim is supported by its receipt excerpt and "
            "the verdict follows from the claims. Be strict; unsupported = bounce. Output JSON only: "
            "{\"pass\": bool, \"reason\": str}."
        ),
    },
}

class TierCapBreach(RuntimeError):
    """Per-tier monthly cap reached; breach row already written to file."""


StageFn = Callable[[str, str, dict[str, Any]], dict[str, Any]]


def ladder_enabled(environ: dict[str, str]) -> bool:
    return environ.get(LADDER_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def receipts_path(data_root: Path) -> Path:
    return data_root / "case_intake" / "ladder_receipts.jsonl"


def structural_check(handoff: dict[str, Any], *, case: dict[str, Any], repo_root: Path) -> list[str]:
    """Deterministic Tier-3 receipt bar. Returns defects (empty = pass)."""
    defects: list[str] = []
    verdict = handoff.get("verdict")
    if verdict not in VERDICTS:
        defects.append(f"verdict_not_in_frozen_band:{verdict!r}")
    claims = handoff.get("claims")
    if not isinstance(claims, list) or not claims:
        defects.append("no_claims")
        return defects
    for i, c in enumerate(claims):
        r = c.get("receipt") if isinstance(c, dict) else None
        if not isinstance(r, dict):
            defects.append(f"claim[{i}]:missing_receipt")
            continue
        kind, ref, excerpt = r.get("kind"), str(r.get("ref") or ""), str(r.get("excerpt") or "")
        if kind not in RECEIPT_KINDS:
            defects.append(f"claim[{i}]:bad_receipt_kind:{kind!r}")
        if not ref.strip() or not excerpt.strip():
            defects.append(f"claim[{i}]:empty_receipt")
            continue
        if kind == "file":
            p = Path(ref) if Path(ref).is_absolute() else repo_root / ref
            try:
                if not p.is_file() or excerpt.strip()[:80] not in p.read_text(encoding="utf-8", errors="replace"):
                    defects.append(f"claim[{i}]:file_receipt_unverified:{ref}")
            except OSError:
                defects.append(f"claim[{i}]:file_receipt_unreadable:{ref}")
        elif kind == "case_field":
            if excerpt.strip()[:80] not in str(case.get(ref) or ""):
                defects.append(f"claim[{i}]:case_field_excerpt_mismatch:{ref}")
    return defects


def make_openrouter_stage_fn(*, command: str = "case-intake-ladder") -> StageFn:
    """Live tier call: spend guard before, ledger record after (monthly gate)."""

    def _fn(tier: str, payload: str, cfg: dict[str, Any]) -> dict[str, Any]:
        from chip import openrouter, spend

        key = openrouter.require_api_key()
        spend.guard_before_live_completion(command, key)
        model = str(cfg["model"])
        completion = openrouter.chat_completion(
            key,
            model=model,
            messages=[{"role": "system", "content": cfg["system"]}, {"role": "user", "content": payload}],
            max_tokens=int(cfg["max_tokens"]),
        )
        spend.record_after_live_completion(command, model, completion, key=key)
        cost, _src, pt, ct = spend.cost_from_completion_payload(completion, model)
        try:
            parsed = parse_stage_payload(openrouter.extract_assistant_text(completion))
        except (ValueError, json.JSONDecodeError) as exc:
            finish = ((completion.get("choices") or [{}])[0] or {}).get("finish_reason")
            parsed = {"_parse_error": f"finish={finish}: {exc}"[:200]}
        _trim_receipts(parsed)
        parsed["_finish"] = finish if (finish := ((completion.get("choices") or [{}])[0] or {}).get("finish_reason")) else None
        parsed["_completion_tokens"] = int(ct)
        parsed["_model"] = model
        parsed["_cost_usd"] = float(cost)
        parsed["_tokens"] = int(pt) + int(ct)
        return parsed

    return _fn


def _trim_receipts(parsed: dict[str, Any]) -> None:
    """Strip surrounding whitespace and CRLF→LF on receipt ref/excerpt; content otherwise untouched."""
    for c in parsed.get("claims") or []:
        r = c.get("receipt") if isinstance(c, dict) else None
        if isinstance(r, dict):
            for k in ("ref", "excerpt"):
                if isinstance(r.get(k), str):
                    r[k] = r[k].replace("\r\n", "\n").strip()


def make_mock_stage_fn(*, plant_defect: bool = False) -> StageFn:
    """Deterministic tiers for CI/drill. ``plant_defect`` strips the first Tier-2 receipt once."""
    planted = {"done": False}

    def _fn(tier: str, payload: str, cfg: dict[str, Any]) -> dict[str, Any]:
        body = json.loads(payload)
        base = {"_model": f"mock/{tier}", "_cost_usd": 0.0, "_tokens": 0}
        if tier == "pm":
            return {**base, "goal": "triage", "acceptance": ["receipted verdict"], "lane": "chipchatdev"}
        if tier == "executor":
            case = body["case"]
            out = {
                **base,
                "verdict": "needs-repro",
                "summary": "mock executor",
                "claims": [{"claim": "case described", "receipt": {
                    "kind": "case_field", "ref": "description", "excerpt": str(case.get("description", ""))[:60]}}],
            }
            if plant_defect and not planted["done"]:
                planted["done"] = True
                out["claims"][0].pop("receipt")
            return out
        return {**base, "pass": True, "reason": "mock verifier ok"}

    return _fn


def _row(case_id: str, tier: str, synthetic: bool, **kw: Any) -> dict[str, Any]:
    row = {"ts": time.time(), "case_id": case_id, "tier": tier, **kw}
    if synthetic:
        row["synthetic"] = True
    return row


def _write(case_path: Path | None, data_root: Path, rows: list[dict[str, Any]],
           context: dict[str, Any] | None = None) -> None:
    for r in rows:
        append_jsonl(receipts_path(data_root), r)
    if case_path is None:
        return
    try:
        body = json.loads(case_path.read_text(encoding="utf-8")) if case_path.is_file() else {}
    except json.JSONDecodeError:
        body = {}
    body.setdefault("ladder_receipts", []).extend(rows)
    if context is not None:
        body["ladder_context"] = context
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def run_ladder(
    case: dict[str, Any],
    *,
    stage_fn: StageFn,
    data_root: Path,
    repo_root: Path,
    case_path: Path | None = None,
    evidence: str = "",
    bounce_limit: int = 2,
    per_case_usd_cap: float = 0.05,
    synthetic: bool = False,
    tiers: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One case through PM → Executor ⇄ Verifier. Never raises; returns summary."""
    tiers = tiers or TIERS
    cid = str(case.get("id") or "unknown")
    rows: list[dict[str, Any]] = []
    spend_by_tier: dict[str, float] = {}
    bounces = 0
    outcome: dict[str, Any] = {"case_id": cid, "synthetic": synthetic}
    context: dict[str, Any] = {"evidence": evidence[:4000], "frame": {}}

    def call(tier: str, payload: dict[str, Any]) -> dict[str, Any]:
        from chip_relay.ladder_telemetry import cap_allows

        if not cap_allows(data_root, tier, case_id=cid):
            raise TierCapBreach(tier)
        out = stage_fn(tier, json.dumps(payload), tiers[tier])
        spend_by_tier[tier] = spend_by_tier.get(tier, 0.0) + float(out.get("_cost_usd") or 0.0)
        return out

    try:
        frame = call("pm", {"case": case})
        context["frame"] = {k: frame.get(k) for k in ("goal", "acceptance", "lane")}
        rows.append(_row(cid, "pm", synthetic, model=frame.get("_model"), cost_usd=frame.get("_cost_usd"),
                         frame={k: frame.get(k) for k in ("goal", "acceptance", "lane")}))
        feedback = ""
        while True:
            if sum(spend_by_tier.values()) > per_case_usd_cap:
                outcome.update(status="spend_cap", verdict=None)
                rows.append(_row(cid, "ladder", synthetic, event="per_case_usd_cap", cap=per_case_usd_cap))
                break
            handoff = call("executor", {"case": case, "frame": frame, "evidence": evidence[:4000],
                                        "verifier_feedback": feedback})
            rows.append(_row(cid, "executor", synthetic, model=handoff.get("_model"), cost_usd=handoff.get("_cost_usd"),
                             verdict=handoff.get("verdict"), claims=handoff.get("claims"),
                             parse_error=handoff.get("_parse_error"), finish_reason=handoff.get("_finish"),
                             completion_tokens=handoff.get("_completion_tokens")))
            defects = structural_check(handoff, case=case, repo_root=repo_root)
            if defects:
                verdict_row = {"pass": False, "reason": "structural:" + ";".join(defects), "_model": "deterministic"}
            else:
                verdict_row = call("verifier", {"case": case, "handoff": {k: handoff.get(k) for k in ("verdict", "summary", "claims")}})
            passed = bool(verdict_row.get("pass")) and not defects
            rows.append(_row(cid, "verifier", synthetic, model=verdict_row.get("_model"),
                             cost_usd=verdict_row.get("_cost_usd", 0.0), passed=passed,
                             defects=defects, reason=str(verdict_row.get("reason") or "")[:300]))
            if passed:
                outcome.update(status="verified", verdict=handoff.get("verdict"))
                break
            bounces += 1
            if bounces > bounce_limit:
                from chip import system_pack

                rows.append(
                    _row(
                        cid,
                        "page",
                        synthetic,
                        event="page_dry_run",
                        escalate_to=system_pack.pack_identity_string("operator_name", default="Operator"),
                        would_fire=True,
                        sent=False,
                        bounces=bounces,
                    )
                )
                outcome.update(status="paged_dry_run", verdict=None)
                break
            feedback = str(verdict_row.get("reason") or "")
    except TierCapBreach as exc:
        rows.append(_row(cid, "ladder", synthetic, event="tier_cap_breach", tier_capped=str(exc)))
        outcome.update(status="tier_cap", verdict=None)
    except Exception as exc:  # noqa: BLE001 — tier faults are receipts, not crashes
        rows.append(_row(cid, "ladder", synthetic, event="error", detail=f"{exc.__class__.__name__}: {exc}"[:300]))
        outcome.update(status="error", verdict=None)

    outcome.update(bounces=bounces, spend_by_tier=spend_by_tier)
    if case.get("fingerprint_class"):
        outcome["fingerprint"] = str(case["fingerprint_class"])
    if outcome.get("status") == "verified" and outcome.get("verdict") in ("propose-in-room", "propose-auto"):
        from chip import go_write

        lane = str((context.get("frame") or {}).get("lane") or "chipchatdev")
        from chip import pack_values

        ticket = pack_values.related_ticket_from_mapping(case) or str(case.get("id") or cid)
        fp = go_write.record_ladder_proposal(
            case_id=cid,
            description=str(case.get("description") or ""),
            verdict=str(outcome.get("verdict")),
            lane=lane,
            ticket=ticket,
            container=str(case.get("go_container") or ""),
        )
        outcome["proposal_fp"] = fp
        outcome["propose_card"] = go_write.format_ladder_propose_card(
            case_id=cid,
            verdict=str(outcome.get("verdict")),
            fp=fp,
            lane=lane,
            ticket=ticket,
            exp_hint="2099-01-01T00:00:00Z",
        )
    rows.append(_row(cid, "ladder", synthetic, event="summary", **{k: v for k, v in outcome.items() if k not in {"synthetic", "case_id"}}))
    try:
        _write(case_path, data_root, rows, context)
    except OSError:
        pass
    outcome["rows"] = rows
    return outcome
