"""Per-persona lane roots, global denies, and receipt-before-action writes.

No raw shell. A global deny beats a persona root. Missing pin refuses; there is
no fallback checkout.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip import config
from chip import store

LANES_FILE = config.REPO_ROOT / "config" / "chip_lanes.json"
GO_ENV = "CHIP_LANE_GO_TICKET"

LANE_PERSONAS = ("homelabdev", "opsdev", "buffrdev", "chipchatdev")


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_lanes() -> dict[str, Any]:
    if not LANES_FILE.is_file():
        return {"personas": {}, "hard_denies": [], "go_denies": []}
    raw = json.loads(LANES_FILE.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {"personas": {}, "hard_denies": [], "go_denies": []}


def persona_root(persona: str) -> Path | None:
    """Return the pinned root, or None when the env pin is unset or missing."""
    spec = (load_lanes().get("personas") or {}).get(persona.strip().lower())
    if not isinstance(spec, dict):
        return None
    env_name = str(spec.get("root_env") or "").strip()
    if not env_name:
        return None
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        return None
    return path


def _norm(text: str) -> str:
    return text.replace("\\", "/").lower()


def deny_reason(blob: str, *, ticket_id: str | None = None) -> str | None:
    """Global denies win over any persona allow. Hard denies never clear."""
    low = _norm(blob)
    lanes = load_lanes()
    for frag in lanes.get("hard_denies") or []:
        if str(frag).lower() in low:
            return f"global deny {frag}"
    go = os.environ.get(GO_ENV, "").strip()
    named = (ticket_id or "").strip()
    for frag in lanes.get("go_denies") or []:
        if str(frag).lower() in low:
            if not named or go != named:
                return f"global deny {frag} (needs GO naming the ticket)"
    return None


def refuse_blob(*parts: str, ticket_id: str | None = None) -> str | None:
    return deny_reason(" ".join(p for p in parts if p), ticket_id=ticket_id)


def receipt_path() -> Path:
    return config.data_dir() / "audit" / "tool_receipts.jsonl"


def retro_path() -> Path:
    return config.data_dir() / "audit" / "retros.jsonl"


def append_receipt(record: dict[str, Any]) -> None:
    store.append_jsonl(receipt_path(), record)


def file_retro(*, attempted: str, why: str, envelope: str) -> None:
    """Live runner only. Tests leave CHIP_LANE_RETRO_LIVE unset so CI stays quiet."""
    if os.environ.get("CHIP_LANE_RETRO_LIVE", "").strip().lower() not in ("1", "true", "yes"):
        return
    store.append_jsonl(
        retro_path(),
        {"ts": _ts(), "attempted": attempted, "why": why, "envelope": envelope},
    )


def _under_root(root: Path, relpath: str) -> Path:
    rel = Path(relpath)
    if rel.is_absolute():
        raise ValueError("path must be relative to the lane root")
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise ValueError("path escapes the lane root")
    return target


def read_text(
    *,
    persona: str,
    relpath: str,
    ticket_id: str | None = None,
) -> str:
    root = persona_root(persona)
    if root is None:
        raise FileNotFoundError(f"lane pin missing for {persona}")
    target = _under_root(root, relpath)
    reason = deny_reason(str(target), ticket_id=ticket_id)
    if reason:
        file_retro(attempted=f"read {target}", why=reason, envelope=f"persona={persona} path={relpath}")
        raise PermissionError(reason)
    append_receipt(
        {
            "ts": _ts(),
            "phase": "start",
            "op": "read",
            "persona": persona,
            "path": str(target),
            "ticket_id": ticket_id or "",
        }
    )
    data = target.read_text(encoding="utf-8")
    append_receipt(
        {
            "ts": _ts(),
            "phase": "complete",
            "op": "read",
            "persona": persona,
            "path": str(target),
            "sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
            "ticket_id": ticket_id or "",
        }
    )
    return data


def assert_claim(*, persona: str, room: str, scope: str, ticket_id: str) -> None:
    """Writes require a live claim owned by this persona. First claim wins."""
    from chip import authorize

    ok, reason = authorize.authorize(
        persona,
        scope,
        "execute",
        environ=os.environ,
        room=room,
        tool="lane-write",
        manifest=("lane-write", "git-status", "git-diff", "git-log", "pytest"),
    )
    if not ok:
        file_retro(
            attempted=f"write ticket={ticket_id}",
            why=reason,
            envelope=f"persona={persona} room={room} scope={scope} ticket={ticket_id}",
        )
        raise PermissionError(reason)


def write_text(
    *,
    persona: str,
    relpath: str,
    content: str,
    ticket_id: str,
    room: str,
    scope: str,
) -> Path:
    """Append a start receipt, then write. Refuses when the pin, claim, or a deny fails."""
    if not (ticket_id or "").strip():
        raise PermissionError("write requires a ticket id")
    assert_claim(persona=persona, room=room, scope=scope, ticket_id=ticket_id)
    root = persona_root(persona)
    if root is None:
        raise FileNotFoundError(f"lane pin missing for {persona}")
    target = _under_root(root, relpath)
    reason = deny_reason(str(target), ticket_id=ticket_id)
    if reason:
        file_retro(
            attempted=f"write {target}",
            why=reason,
            envelope=f"persona={persona} path={relpath} ticket={ticket_id}",
        )
        raise PermissionError(reason)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    append_receipt(
        {
            "ts": _ts(),
            "phase": "start",
            "op": "write",
            "persona": persona,
            "path": str(target),
            "sha256": digest,
            "ticket_id": ticket_id,
            "intent": "write",
        }
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    append_receipt(
        {
            "ts": _ts(),
            "phase": "complete",
            "op": "write",
            "persona": persona,
            "path": str(target),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "ticket_id": ticket_id,
        }
    )
    return target
