"""LINEAR_API_KEY gate: absent / valid / invalid. Invalid fails closed with a receipt.

A present-but-rejected key must never produce a half-alive learnings source: the
caller gets ``ok=False`` and a row lands in ``linear_key_gate.jsonl``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from chip.store import append_jsonl

LINEAR_API_URL = "https://api.linear.app/graphql"
Probe = Callable[[str], dict[str, Any]]
_CACHE: dict[str, dict[str, Any]] = {}


def _default_probe(key: str) -> dict[str, Any]:
    req = urllib.request.Request(
        LINEAR_API_URL,
        data=json.dumps({"query": "{ viewer { id } }"}).encode(),
        headers={"Content-Type": "application/json", "Authorization": key, "User-Agent": "chip-chat-linear-gate"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def check(environ: dict[str, str], *, data_root: Path, probe: Probe | None = None, use_cache: bool = True) -> dict[str, Any]:
    key = (environ.get("LINEAR_API_KEY") or "").strip()
    if not key:
        return {"state": "absent", "ok": False, "detail": "LINEAR_API_KEY unset"}
    if use_cache and key in _CACHE:
        return _CACHE[key]
    try:
        body = (probe or _default_probe)(key)
        ok = isinstance(body, dict) and not body.get("errors") and bool((body.get("data") or {}).get("viewer"))
        detail = "viewer ok" if ok else str(body.get("errors") if isinstance(body, dict) else body)[:200]
    except urllib.error.HTTPError as exc:
        ok, detail = False, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        out = {"state": "unreachable", "ok": False, "detail": f"{exc.__class__.__name__}"[:200]}
        append_jsonl(data_root / "linear_key_gate.jsonl", {"ts": time.time(), **out})
        return out
    out = {"state": "valid" if ok else "invalid", "ok": ok, "detail": detail}
    if not ok:
        append_jsonl(data_root / "linear_key_gate.jsonl", {"ts": time.time(), **out, "fail_closed": True})
    _CACHE[key] = out
    return out
