"""Simple-question lane: route plain factual questions to lumo-tamer, not the operator.

Law (kept behind the existing GO + flag rules):
- Default OFF. Live only when CHIP_LUMO_SIMPLE_ROUTE=1 AND a lumo-tamer URL is set.
- Runs AFTER ladder-outcome and scoped-GO handling in dispatch.handle_message, so a
  ``GO:`` line can never reach this lane; GO-shaped and deploy/absorb text is refused.
- CHIP_STAND_DOWN wins: no lumo call while standing down.
- Action/mutation verbs (restart, delete, deploy, ...) are never "simple".
- Fail-closed to the normal path: any lumo error/timeout/empty reply returns None and
  the message continues to the regular persona dispatch (operator still sees it).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

FLAG_ENV = "CHIP_LUMO_SIMPLE_ROUTE"
URL_ENV = "CHIP_LUMO_TAMER_URL"  # e.g. http://lumo-tamer:3003/v1/chat/completions
KEY_ENV = "LUMO_TAMER_API_KEY"
MODEL_ENV = "CHIP_LUMO_TAMER_MODEL"
DEFAULT_MODEL = "lumo"
TIMEOUT_S = 60.0
MAX_QUESTION_CHARS = 240

_QUESTION_START = re.compile(
    r"^(what|whats|what's|when|where|who|which|why|how|is|are|does|do|did|can|could|"
    r"should|will|would|define|explain)\b",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"\b(restart|stop|start|kill|delete|remove|rm|wipe|purge|deploy|absorb|merge|push|"
    r"rebuild|flip|enable|disable|rotate|revoke|grant|approve|install|uninstall|run|"
    r"execute|apply|rollback|roll back|down|prune|reboot|shutdown|fix|change|set)\b",
    re.IGNORECASE,
)


def _env(environ: dict[str, str] | None) -> dict[str, str]:
    return os.environ if environ is None else environ


def enabled(environ: dict[str, str] | None = None) -> bool:
    env = _env(environ)
    flag = (env.get(FLAG_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}
    return flag and bool((env.get(URL_ENV) or "").strip())


def is_simple_question(text: str) -> bool:
    raw = (text or "").strip()
    if not raw or len(raw) > MAX_QUESTION_CHARS or "\n" in raw:
        return False
    if raw.upper().startswith("GO:") or raw.startswith("--") or raw.startswith("@"):
        return False
    from chip.go_scope import needs_scoped_go

    if needs_scoped_go(raw):
        return False
    if not raw.endswith("?"):
        return False
    if _QUESTION_START.match(raw) is None:
        return False
    return _ACTION.search(raw) is None


def should_route(text: str, environ: dict[str, str] | None = None) -> bool:
    env = _env(environ)
    if not enabled(env):
        return False
    from chip_relay.dispatch import stand_down

    if stand_down(dict(env)):
        return False
    return is_simple_question(text)


def ask_lumo(question: str, environ: dict[str, str] | None = None, *, opener=None) -> str | None:
    """POST an OpenAI-compatible chat request to lumo-tamer. None on any failure."""
    env = _env(environ)
    url = (env.get(URL_ENV) or "").strip()
    if not url:
        return None
    headers = {"Content-Type": "application/json", "User-Agent": "chip-relay"}
    key = (env.get(KEY_ENV) or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    body = {
        "model": (env.get(MODEL_ENV) or DEFAULT_MODEL).strip(),
        "messages": [
            {"role": "system", "content": "Answer briefly and factually. If unsure, say so."},
            {"role": "user", "content": question},
        ],
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return None
    try:
        reply = str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError):
        return None
    return reply or None
