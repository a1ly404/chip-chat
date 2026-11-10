"""Durable memory with provenance, user/room scopes and recall search (parity P1.2).

Explicit memory, separate from the advisory MemPalace extraction in ``chip.mempalace``:

* ``remember(scope, text, by=..., source=..., now=...)`` stores one fact with provenance
  (who wrote it, from which source, when).
* ``forget(record_id)`` removes it from recall; an id-only tombstone is appended to the audit log.
* ``recall(query, scopes=...)`` ranks active facts by keyword overlap (recency breaks ties)
  within a top-k and character budget.

Scopes: ``user`` (one global profile shared across rooms) and ``room:<name>``. Each scope is
one JSON file under ``CHIP_CHAT_DATA_DIR/memory/``; writes are atomic (tmp + replace).

Safety: secret-shaped text (API keys, tokens, private keys, ``password=`` pairs) is refused on
write; text is length-capped; each scope keeps at most ``max_records`` active facts and evicts
the oldest. Re-remembering identical text returns the existing record; remembering with the same
``key`` supersedes the previous fact (newest wins; old one kept as ``superseded`` for audit).

Library and CLI only: nothing here injects memory into turns. Turn injection will be gated by
``CHIP_MEMORY=1`` (default off); ``enabled()`` exposes that flag for callers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip.config import data_dir
from chip.law import safe_room_name

SCHEMA_VERSION = 1
FLAG = "CHIP_MEMORY"
USER_SCOPE = "user"
KINDS = frozenset({"fact", "preference", "decision"})
MAX_TEXT_CHARS = 1000
DEFAULT_MAX_RECORDS = 500
DEFAULT_TOP_K = 5
DEFAULT_BUDGET_CHARS = 2000
_KEY = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$", re.IGNORECASE)
_ID = re.compile(r"^m_[0-9a-f]{12}$")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be by for from has have i in is it its of on or that the this to was we were with you".split()
)

# Shapes of credentials we never persist. Deliberately broad: a false refusal is cheap,
# a stored secret is not.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(
        r"\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\s*[:=]\s*\S{4,}",
        re.IGNORECASE,
    ),
    re.compile(r"https?://[^\s/:@]+:[^\s/@]+@"),
    re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{20,}", re.IGNORECASE),
)
# Long opaque tokens mixing upper, lower and digits (hex digests / git SHAs stay storable).
_OPAQUE = re.compile(r"[A-Za-z0-9+/_=-]{32,}")


class MemoryRefused(ValueError):
    """Raised when text may not be stored (secret-shaped, empty or too long)."""


def enabled(env: dict[str, str] | None = None) -> bool:
    src = os.environ if env is None else env
    return str(src.get(FLAG, "0")).strip().lower() in {"1", "true", "yes", "on"}


def looks_secret(text: str) -> bool:
    text = text or ""
    if any(p.search(text) for p in _SECRET_PATTERNS):
        return True
    for tok in _OPAQUE.findall(text):
        if re.search(r"[A-Z]", tok) and re.search(r"[a-z]", tok) and re.search(r"[0-9]", tok):
            return True
    return False


def _iso(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_scope(scope: str) -> str:
    s = (scope or "").strip()
    if s == USER_SCOPE:
        return s
    if s.startswith("room:"):
        name = safe_room_name(s[5:])
        if name and name.strip("_"):
            return f"room:{name}"
    raise ValueError(f"invalid scope: {scope!r} (use 'user' or 'room:<name>')")


def memory_dir() -> Path:
    path = data_dir() / "memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _scope_path(scope: str) -> Path:
    scope = normalize_scope(scope)
    stem = "user" if scope == USER_SCOPE else "room__" + scope[5:]
    return memory_dir() / f"{stem}.json"


def audit_path() -> Path:
    return memory_dir() / "audit.jsonl"


def _load_scope(scope: str) -> dict[str, Any]:
    path = _scope_path(scope)
    if not path.is_file():
        return {"schema": SCHEMA_VERSION, "scope": normalize_scope(scope), "records": []}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if int(doc.get("schema", 0)) != SCHEMA_VERSION:
        raise ValueError(f"memory schema mismatch in {path.name}")
    return doc


def _save_scope(doc: dict[str, Any]) -> None:
    path = _scope_path(doc["scope"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _audit(row: dict[str, Any]) -> None:
    with audit_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _norm_text(text: str) -> str:
    return " ".join((text or "").split())


def _new_id(scope: str, text: str, ts: str, n: int) -> str:
    digest = hashlib.sha256(f"{scope}\x00{text}\x00{ts}\x00{n}".encode()).hexdigest()
    return "m_" + digest[:12]


def _all_scopes() -> list[str]:
    out = []
    for p in sorted(memory_dir().glob("*.json")):
        if p.stem == "user":
            out.append(USER_SCOPE)
        elif p.stem.startswith("room__"):
            out.append("room:" + p.stem[6:])
    return out


def remember(
    scope: str,
    text: str,
    *,
    by: str,
    source: str,
    now: datetime,
    kind: str = "fact",
    key: str | None = None,
    max_records: int = DEFAULT_MAX_RECORDS,
) -> dict[str, Any]:
    """Store one fact. Returns the active record (existing one on exact duplicate)."""
    scope = normalize_scope(scope)
    clean = _norm_text(text)
    if not clean:
        raise MemoryRefused("memory text is empty")
    if len(clean) > MAX_TEXT_CHARS:
        raise MemoryRefused(f"memory text exceeds {MAX_TEXT_CHARS} chars")
    if looks_secret(clean):
        raise MemoryRefused("memory text looks like a credential; refusing to store it")
    if kind not in KINDS:
        raise ValueError(f"invalid kind: {kind!r} (use one of {sorted(KINDS)})")
    if key is not None and not _KEY.match(key):
        raise ValueError(f"invalid key: {key!r}")
    by = (by or "").strip()
    source = (source or "").strip()
    if not by or not source:
        raise ValueError("provenance requires both 'by' and 'source'")
    if max_records < 1:
        raise ValueError("max_records must be >= 1")

    ts = _iso(now)
    doc = _load_scope(scope)
    records: list[dict[str, Any]] = doc["records"]
    active = [r for r in records if r["status"] == "active"]
    lowered = clean.casefold()
    for r in active:
        if r["text"].casefold() == lowered and r.get("key") == key:
            return r

    rid = _new_id(scope, clean, ts, len(records))
    rec = {
        "id": rid,
        "scope": scope,
        "kind": kind,
        "key": key,
        "text": clean,
        "status": "active",
        "provenance": {"by": by, "source": source, "at": ts},
        "superseded_by": None,
    }
    if key is not None:
        for r in active:
            if r.get("key") == key:
                r["status"] = "superseded"
                r["superseded_by"] = rid
                _audit({"event": "supersede", "id": r["id"], "by_id": rid, "scope": scope, "at": ts})
    records.append(rec)
    _audit({"event": "remember", "id": rid, "scope": scope, "by": by, "source": source, "at": ts})

    active = [r for r in records if r["status"] == "active"]
    overflow = len(active) - max_records
    if overflow > 0:
        evict = {r["id"] for r in sorted(active, key=lambda r: r["provenance"]["at"])[:overflow]}
        for r in records:
            if r["id"] in evict:
                _audit({"event": "evict", "id": r["id"], "scope": scope, "at": ts})
        records[:] = [r for r in records if r["id"] not in evict]
    # Superseded rows are audit only; keep them bounded too.
    stale = [r for r in records if r["status"] != "active"]
    if len(stale) > max_records:
        drop = {r["id"] for r in stale[: len(stale) - max_records]}
        records[:] = [r for r in records if r["id"] not in drop]
    _save_scope(doc)
    return rec


def forget(record_id: str, *, by: str, now: datetime) -> bool:
    """Remove a record (any status) by id. The audit row keeps the id, never the text."""
    if not _ID.match(record_id or ""):
        raise ValueError(f"invalid memory id: {record_id!r}")
    for scope in _all_scopes():
        doc = _load_scope(scope)
        before = len(doc["records"])
        doc["records"] = [r for r in doc["records"] if r["id"] != record_id]
        if len(doc["records"]) != before:
            _save_scope(doc)
            _audit({"event": "forget", "id": record_id, "scope": scope, "by": (by or "unknown"), "at": _iso(now)})
            return True
    return False


def list_records(scope: str | None = None, *, include_superseded: bool = False) -> list[dict[str, Any]]:
    scopes = [normalize_scope(scope)] if scope else _all_scopes()
    out: list[dict[str, Any]] = []
    for s in scopes:
        for r in _load_scope(s)["records"]:
            if include_superseded or r["status"] == "active":
                out.append(r)
    return out


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.casefold()) if w not in _STOP and len(w) > 1}


def recall(
    query: str,
    *,
    scopes: list[str] | None = None,
    top_k: int = DEFAULT_TOP_K,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> list[dict[str, Any]]:
    """Active facts ranked by keyword overlap with ``query`` (newest first on ties).

    Only records sharing at least one keyword are returned. ``budget_chars`` caps the summed
    text length; a record that does not fit is skipped, never truncated.
    """
    q = _tokens(query)
    if not q or top_k <= 0 or budget_chars <= 0:
        return []
    pool = []
    for s in scopes if scopes is not None else _all_scopes():
        pool.extend(list_records(s))
    scored = []
    for r in pool:
        hits = len(q & _tokens(r["text"]))
        if hits:
            scored.append((hits, r["provenance"]["at"], r))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    out: list[dict[str, Any]] = []
    used = 0
    for _, _, r in scored:
        if len(out) >= top_k:
            break
        if used + len(r["text"]) > budget_chars:
            continue
        out.append(r)
        used += len(r["text"])
    return out


def format_recall_block(records: list[dict[str, Any]]) -> str:
    """Advisory prompt block; facts are context, never instructions."""
    if not records:
        return ""
    lines = ["## Remembered facts (advisory, may be stale)"]
    for r in records:
        p = r["provenance"]
        lines.append(f"- [{r['scope']}] {r['text']} (by {p['by']} via {p['source']}, {p['at']})")
    return "\n".join(lines)
