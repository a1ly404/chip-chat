"""Persist ladder propose fingerprints and single-use scoped GO tokens."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from chip.config import data_dir
from chip.store import append_jsonl


def _root() -> Path:
    p = data_dir() / "go_scoped"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_ladder_proposal(
    *,
    fp: str,
    lane: str,
    ticket: str,
    container: str = "",
    case_id: str = "",
) -> dict[str, Any]:
    row = {
        "ts": time.time(),
        "fp": fp,
        "lane": lane,
        "ticket": ticket,
        "container": container,
        "case_id": case_id,
        "source": "ladder",
    }
    append_jsonl(_root() / "proposals.jsonl", row)
    return row


def latest_proposal(fp: str) -> dict[str, Any] | None:
    path = _root() / "proposals.jsonl"
    if not path.is_file():
        return None
    hit = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if str(row.get("fp")) == fp:
            hit = row
    return hit


def go_token_hash(go_text: str) -> str:
    norm = " ".join((go_text or "").strip().split())
    return hashlib.sha256(norm.encode()).hexdigest()


def token_used(go_text: str) -> bool:
    h = go_token_hash(go_text)
    path = _root() / "used_tokens.jsonl"
    if not path.is_file():
        return False
    return any(h in line for line in path.read_text(encoding="utf-8").splitlines())


def mark_token_used(go_text: str, *, meta: dict[str, Any] | None = None) -> None:
    row = {"ts": time.time(), "hash": go_token_hash(go_text), **(meta or {})}
    append_jsonl(_root() / "used_tokens.jsonl", row)


def write_verify_row(*, fp: str, ticket: str, status: str, detail: str) -> None:
    append_jsonl(
        _root() / "verify.jsonl",
        {"ts": time.time(), "fp": fp, "ticket": ticket, "status": status, "detail": detail[:300]},
    )
