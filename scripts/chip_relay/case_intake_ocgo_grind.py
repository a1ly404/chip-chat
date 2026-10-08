"""OCgo-managed evidence grind for case intake (Qwen lane)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from chip_relay.case_intake import CaseFile
from chip_relay.case_intake_escalation import StageFn
from chip_relay.case_intake_spend import estimate_usd, per_case_warn_flag, record_attempt

EXECUTOR_TAG = "ocodegenow"
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def ocgo_managed(environ: dict[str, str]) -> bool:
    return environ.get("OCGO_MANAGED", "0").strip().lower() in {"1", "true", "yes", "on"}


def ocgo_grind_enabled(environ: dict[str, str]) -> bool:
    if not ocgo_managed(environ):
        return False
    return environ.get("CASE_INTAKE_OCGO_GRIND", "0").strip().lower() in {"1", "true", "yes", "on"}


def validate_evidence_pack(payload: dict[str, Any]) -> None:
    """Fail closed on hostile or incomplete evidence."""
    for key in ("summary", "log_pointers", "repro_steps"):
        if key not in payload:
            raise ValueError(f"missing evidence key: {key}")
    summary = str(payload.get("summary") or "")
    if _CONTROL_CHARS.search(summary):
        raise ValueError("hostile control chars in summary")
    if len(summary) > 4000:
        raise ValueError("summary too long")
    if not isinstance(payload.get("log_pointers"), list) or not isinstance(payload.get("repro_steps"), list):
        raise ValueError("log_pointers/repro_steps must be lists")


def write_evidence_pack(case_path: Path, case: CaseFile, pack: dict[str, Any]) -> None:
    doc: dict[str, Any]
    if case_path.is_file():
        try:
            doc = json.loads(case_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            doc = {}
    else:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc.update(case.as_dict())
    doc["evidence_pack"] = pack
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def make_ocgo_gather_fn(
    *,
    case: CaseFile,
    channel: str,
    case_path: Path,
    inner: StageFn,
    now: float | None = None,
) -> StageFn:
    """Wrap gather stage: OCgo executor tag + case file evidence pack."""

    def _fn(payload: str, model: str) -> dict[str, Any]:
        parsed = inner(payload, model)
        validate_evidence_pack(parsed)
        tokens = int(parsed.get("_tokens") or 0)
        usd = estimate_usd(model, tokens)
        record_attempt(
            case_id=case.id,
            channel=channel,
            model=model,
            tokens=tokens,
            outcome="ocgo_gather",
            executor=EXECUTOR_TAG,
            per_case_warn=per_case_warn_flag(case.id, usd, now=now),
            now=now,
        )
        pack = {
            "summary": str(parsed.get("summary") or ""),
            "log_pointers": list(parsed.get("log_pointers") or []),
            "repro_steps": list(parsed.get("repro_steps") or []),
            "executor": EXECUTOR_TAG,
            "model": model,
        }
        write_evidence_pack(case_path, case, pack)
        parsed["evidence_pack"] = pack
        return parsed

    return _fn
