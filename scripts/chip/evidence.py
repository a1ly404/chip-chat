"""Spec B — evidence-gated done for task envelopes."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip import config
from chip import system_pack

EVIDENCE_TYPES = frozenset(
    {"url_regex", "json_path", "literal_in_tool_output", "discord_ts"}
)

URL_REGEX_DEFAULT = re.compile(
    r"https://github\.com/[^/]+/[^/]+/actions/runs/\d+",
    re.IGNORECASE,
)

NEVER_AUTO_DONE = "never_auto_done.txt"


def never_auto_done_path() -> Path:
    return config.data_dir() / NEVER_AUTO_DONE


def repo_never_auto_done_path() -> Path:
    return system_pack.surface("never_auto_done", system_pack.load_pack())


def load_never_auto_done_keys() -> frozenset[str]:
    keys: set[str] = set()
    for path in (repo_never_auto_done_path(), never_auto_done_path()):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                keys.add(line)
    return frozenset(keys)


def validate_acceptance_immutable(envelope: dict[str, Any], new_acceptance: list[dict[str, Any]]) -> None:
    existing = envelope.get("acceptance")
    if existing is None:
        return
    if not isinstance(existing, list):
        raise ValueError("corrupt acceptance[]")
    if json.dumps(existing, sort_keys=True) != json.dumps(new_acceptance, sort_keys=True):
        raise ValueError("REFUSE:acceptance_immutable")


def normalize_acceptance(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("acceptance item must be object")
        eid = str(item.get("id") or "")
        text = str(item.get("text") or "")
        etype = str(item.get("evidence_type") or "")
        if not eid or not text or etype not in EVIDENCE_TYPES:
            raise ValueError("invalid acceptance item")
        out.append({"id": eid, "text": text, "evidence_type": etype})
    return out


def capture_evidence_from_tool_output(
    envelope: dict[str, Any],
    *,
    tool_id: str,
    stdout: str,
    stderr: str,
    acceptance_id: str | None = None,
    kind: str | None = None,
    payload: str | None = None,
) -> dict[str, Any] | None:
    """Only tool outputs may create evidence rows — never model prose."""
    text = (stdout or "") + "\n" + (stderr or "")
    text = text.strip()
    if not text and payload is None:
        return None
    row = {
        "acceptance_id": acceptance_id or "",
        "kind": kind or "tool_output",
        "payload": payload if payload is not None else text,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_tool": tool_id,
    }
    evidence = envelope.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
    evidence.append(row)
    envelope["evidence"] = evidence
    return row


def _validator_url_regex(payload: str, *, regex: re.Pattern[str] | None = None) -> bool:
    pat = regex or URL_REGEX_DEFAULT
    return bool(pat.search(payload or ""))


def _validator_literal_in_tool_output(payload: str, *, expected: str) -> bool:
    return expected in (payload or "")


def _validator_json_path(payload: str, *, path: str, expected: str) -> bool:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return False
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return str(cur) == expected


def _validator_discord_ts(payload: str, *, created_at: str) -> bool:
    if re.match(r"^\d{4}-\d{2}-\d{2}T", payload or ""):
        return True
    try:
        ms = int(payload)
    except (TypeError, ValueError):
        return False
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        created_ms = int(created.timestamp() * 1000)
    except ValueError:
        return ms > 0
    return ms > created_ms


def evidence_valid_for_acceptance(
    acceptance: dict[str, Any],
    evidence_row: dict[str, Any],
    *,
    envelope: dict[str, Any],
) -> bool:
    if evidence_row.get("acceptance_id") != acceptance.get("id"):
        return False
    payload = str(evidence_row.get("payload") or "")
    etype = str(acceptance.get("evidence_type") or "")
    if etype == "url_regex":
        return _validator_url_regex(payload)
    if etype == "literal_in_tool_output":
        return _validator_literal_in_tool_output(payload, expected=str(acceptance.get("text") or ""))
    if etype == "json_path":
        meta = acceptance.get("json_path") or {}
        if isinstance(meta, dict):
            return _validator_json_path(
                payload,
                path=str(meta.get("path") or ""),
                expected=str(meta.get("expected") or ""),
            )
        return False
    if etype == "discord_ts":
        return _validator_discord_ts(payload, created_at=str(envelope.get("created_at") or ""))
    return False


def try_done(envelope: dict[str, Any]) -> tuple[bool, str]:
    """
    Only path to done. Returns (True, 'Done') or (False, reason token / missing ids).
    """
    issue_key = str(envelope.get("issue_key") or envelope.get("id") or "")
    if issue_key in load_never_auto_done_keys():
        return False, "REFUSE:never_auto_done"

    acceptance = envelope.get("acceptance")
    if not isinstance(acceptance, list):
        acceptance = []
    evidence = envelope.get("evidence")
    if not isinstance(evidence, list):
        evidence = []

    if not acceptance:
        return False, "REFUSE:missing_acceptance"

    missing: list[str] = []
    for item in acceptance:
        aid = str(item.get("id") or "")
        ok = any(evidence_valid_for_acceptance(item, row, envelope=envelope) for row in evidence)
        if not ok:
            missing.append(aid)
    if missing:
        return False, ",".join(missing)
    return True, "Done"


def apply_try_done(envelope: dict[str, Any]) -> dict[str, Any]:
    ok, reason = try_done(envelope)
    if ok:
        envelope["status"] = "done"
    else:
        if reason.startswith("REFUSE:"):
            envelope["status"] = envelope.get("status") or "open"
        else:
            envelope["status"] = "open"
        envelope["done_blocker"] = reason
    return envelope
