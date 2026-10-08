"""Novel-shape escalation ladder: gather → diagnose → pager (slice 7, WI-357)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from chip import pack_values, system_pack
from chip.store import append_jsonl
from chip_relay.case_intake import CaseFile

REPO = Path(__file__).resolve().parents[2]
_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

StageFn = Callable[[str, str], dict[str, Any]]


def escalation_enabled(environ: dict[str, str]) -> bool:
    for key in ("CASE_INTAKE_ESCALATE", "CASE_INTAKE_ESCALATION_LADDER"):
        raw = environ.get(key, "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
    return False


def _default_config_path() -> Path:
    return system_pack.surface("case_intake_escalation", system_pack.load_pack())


def load_escalation_config(path: Path | None = None) -> dict[str, Any]:
    raw = (path or _default_config_path()).read_text(encoding="utf-8")
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
    return out


def parse_stage_payload(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fence = _JSON_FENCE.search(stripped)
    if fence:
        stripped = fence.group(1).strip()
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("stage output not object")
    return parsed


def _estimate_usd(tokens: int, per_1k: float) -> float:
    return (max(0, tokens) / 1000.0) * per_1k


@dataclass
class EscalationResult:
    ran: bool
    outcome: str
    confidence: float
    should_page: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    diagnose: dict[str, Any] = field(default_factory=dict)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    ladder_log: list[str] = field(default_factory=list)


def append_case_escalation(case_path: Path, block: dict[str, Any]) -> None:
    if not case_path.is_file():
        case_path.parent.mkdir(parents=True, exist_ok=True)
        case_path.write_text(json.dumps({"escalation": [block]}, indent=2) + "\n", encoding="utf-8")
        return
    try:
        doc = json.loads(case_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    rows = list(doc.get("escalation") or [])
    rows.append(block)
    doc["escalation"] = rows
    case_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def log_escalation_jsonl(data_root: Path, row: dict[str, Any]) -> None:
    append_jsonl(data_root / "escalation.jsonl", row)


def run_ladder(
    case: CaseFile,
    *,
    case_path: Path,
    data_root: Path,
    config: dict[str, Any],
    gather: StageFn,
    diagnose: StageFn,
    now: float | None = None,
    spent_usd: float = 0.0,
) -> EscalationResult:
    """State machine: novel → evidence → diagnose → park|pager."""
    ts = now if now is not None else time.time()
    cap = float(config.get("per_case_usd_cap") or 0.25)
    threshold = float(config.get("diagnose_confidence_min") or 0.72)
    receipts: list[dict[str, Any]] = []
    ladder_log: list[str] = ["novel"]

    if spent_usd >= cap:
        row = {
            "ts": ts,
            "case_id": case.id,
            "stage": "cap_hit",
            "spent_usd": spent_usd,
            "cap_usd": cap,
        }
        log_escalation_jsonl(data_root, row)
        return EscalationResult(
            ran=True,
            outcome="cap_hit",
            confidence=0.0,
            should_page=True,
            receipts=[row],
            ladder_log=ladder_log + ["cap_hit", "pager"],
        )

    case_blob = json.dumps(case.as_dict())
    gather_model = str(config["gather_model"])
    gather_payload = gather(case_blob, gather_model)
    gather_tokens = int(gather_payload.get("_tokens") or 0)
    gather_usd = _estimate_usd(gather_tokens, float(config.get("usd_per_1k_tokens_gather") or 0.0))
    spent_usd += gather_usd
    evidence = {
        "summary": str(gather_payload.get("summary") or "").strip(),
        "log_pointers": list(gather_payload.get("log_pointers") or []),
        "repro_steps": list(gather_payload.get("repro_steps") or []),
    }
    gather_receipt = {
        "ts": ts,
        "stage": "gather",
        "model": gather_model,
        "tokens": gather_tokens,
        "usd_estimate": gather_usd,
        "case_id": case.id,
    }
    receipts.append(gather_receipt)
    log_escalation_jsonl(data_root, gather_receipt)
    ladder_log.append("evidence")

    if spent_usd >= cap:
        return EscalationResult(
            ran=True,
            outcome="cap_hit_after_gather",
            confidence=0.0,
            should_page=True,
            evidence=evidence,
            receipts=receipts,
            ladder_log=ladder_log + ["pager"],
        )

    diagnose_model = str(config["diagnose_model"])
    diagnose_input = json.dumps({"case": case.as_dict(), "evidence": evidence})
    diagnose_payload = diagnose(diagnose_input, diagnose_model)
    diagnose_tokens = int(diagnose_payload.get("_tokens") or 0)
    diagnose_usd = _estimate_usd(diagnose_tokens, float(config.get("usd_per_1k_tokens_diagnose") or 0.0))
    confidence = float(diagnose_payload.get("confidence") or 0.0)
    diagnose_block = {
        "hypothesis": str(diagnose_payload.get("hypothesis") or "").strip(),
        "confidence": confidence,
        "diagnose_only": True,
        "recommended_next": str(diagnose_payload.get("recommended_next") or "park").strip(),
    }
    diagnose_receipt = {
        "ts": ts,
        "stage": "diagnose",
        "model": diagnose_model,
        "tokens": diagnose_tokens,
        "usd_estimate": diagnose_usd,
        "confidence": confidence,
        "case_id": case.id,
    }
    receipts.append(diagnose_receipt)
    log_escalation_jsonl(data_root, diagnose_receipt)
    ladder_log.append("diagnose")

    block = {
        "ts": ts,
        "evidence": evidence,
        "diagnose": diagnose_block,
        "receipts": receipts,
        "ladder_log": ladder_log,
    }
    append_case_escalation(case_path, block)

    if confidence >= threshold:
        ladder_log.append("park")
        return EscalationResult(
            ran=True,
            outcome="resolved",
            confidence=confidence,
            should_page=False,
            evidence=evidence,
            diagnose=diagnose_block,
            receipts=receipts,
            ladder_log=ladder_log,
        )

    ladder_log.append("pager")
    return EscalationResult(
        ran=True,
        outcome="pager",
        confidence=confidence,
        should_page=True,
        evidence=evidence,
        diagnose=diagnose_block,
        receipts=receipts,
        ladder_log=ladder_log,
    )


def handle_novel_shape(
    case: CaseFile,
    *,
    tax: str,
    tax_classes: set[str],
    deduped: bool,
    environ: dict[str, str],
    limits: dict[str, Any],
    pager: Any,
    post_pager: Any,
    case_path: Path,
    data_root: Path,
    state: dict[str, Any],
    save_state: Any,
    now: float,
    logs: list[str],
) -> None:
    """Pager or escalation ladder for novel taxonomy shapes."""
    page_class = case.fingerprint_class or "novel-intake"
    novel_shape = tax == "novel" or (case.fingerprint_class and case.fingerprint_class not in tax_classes)
    if not novel_shape or deduped:
        return

    link = f"file://{case_path}"

    if escalation_enabled(environ):
        from chip_relay.case_intake_coverage import record_escalation
        from chip_relay.case_intake_ocgo_grind import make_ocgo_gather_fn, ocgo_grind_enabled

        cfg = load_escalation_config()
        base_gather = make_openrouter_stage_fn("gather", cfg)
        gather_fn = base_gather
        if ocgo_grind_enabled(environ):
            gather_fn = make_ocgo_gather_fn(
                case=case,
                channel=str(limits.get("intake_channels") or pack_values.default_intake_channel()).split(",")[0].strip(),
                case_path=case_path,
                inner=base_gather,
                now=now,
            )
        esc = run_ladder(
            case,
            case_path=case_path,
            data_root=data_root,
            config=cfg,
            gather=gather_fn,
            diagnose=make_openrouter_stage_fn("diagnose", cfg),
            now=now,
        )
        if esc.evidence:
            record_escalation("escalation_evidence")
        if esc.diagnose:
            record_escalation("escalation_diagnose")
        if esc.should_page:
            record_escalation("escalation_paged")
            summary = f"{case.description[:120]} (escalation ladder conf={esc.confidence:.2f})"
            ping = pager.ping(page_class, summary, link, now=now, limits=limits)
            if ping and post_pager({**ping, "summary": summary, "link": link}):
                state.setdefault("pager_last", {})[page_class] = now
                save_state(state)
                logs.append("pager sent after escalation ladder")
        else:
            logs.append(f"escalation ladder resolved confidence={esc.confidence:.2f}")
        return

    ping = pager.ping(page_class, case.description[:120], link, now=now, limits=limits)
    if ping and post_pager({**ping, "summary": case.description[:120], "link": link}):
        state.setdefault("pager_last", {})[page_class] = now
        save_state(state)
        logs.append("pager sent (novel shape)")


def make_openrouter_stage_fn(stage: str, config: dict[str, Any]) -> StageFn:
    """Live gather/diagnose via OpenRouter (not used in CI)."""

    def _fn(payload: str, model: str) -> dict[str, Any]:
        from chip import config as chip_config
        from chip import openrouter

        key = openrouter.require_api_key()
        if stage == "gather":
            system = str(config.get("gather_system") or "")
            max_tokens = int(config.get("gather_max_tokens") or 512)
        else:
            system = str(config.get("diagnose_system") or "")
            max_tokens = int(config.get("diagnose_max_tokens") or 768)
        completion = openrouter.chat_completion(
            key,
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": payload},
            ],
            max_tokens=max_tokens,
        )
        text = openrouter.extract_assistant_text(completion)
        parsed = parse_stage_payload(text)
        usage = completion.get("usage") if isinstance(completion.get("usage"), dict) else {}
        parsed["_tokens"] = int(usage.get("total_tokens") or 0)
        return parsed

    return _fn
