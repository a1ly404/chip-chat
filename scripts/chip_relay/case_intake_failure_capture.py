"""Capture live misses as golden fixtures + append regression index."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from chip.config import REPO_ROOT

FIXTURE_DIR = REPO_ROOT / "config" / "fixtures" / "case_intake_live"
INDEX_FILE = FIXTURE_DIR / "index.json"


def _slug(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "alert"
    digest = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"{base}-{digest}"


def capture(
    *,
    raw_input: str,
    outcome: str,
    failure_kind: str,
    detail: str,
    expected: dict[str, Any] | None = None,
    fingerprint_class: str = "",
) -> str:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    fixture_id = _slug(raw_input[:200])
    body = {
        "id": fixture_id,
        "captured_at": time.time(),
        "raw_input": raw_input,
        "outcome": outcome,
        "failure_kind": failure_kind,
        "detail": detail,
        "expected": expected or {},
    }
    if fingerprint_class:
        body["fingerprint_class"] = fingerprint_class
    path = FIXTURE_DIR / f"{fixture_id}.json"
    if not path.is_file():
        path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    index: list[dict[str, str]] = []
    if INDEX_FILE.is_file():
        try:
            index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            index = []
    if not any(row.get("id") == fixture_id for row in index):
        row = {"id": fixture_id, "path": str(path.relative_to(REPO_ROOT)), "outcome": outcome}
        if fingerprint_class:
            row["fingerprint_class"] = fingerprint_class
        index.append(row)
        INDEX_FILE.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return fixture_id
