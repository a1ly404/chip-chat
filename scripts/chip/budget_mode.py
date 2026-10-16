"""Monthly OpenRouter budget modes: normal -> thrift -> stopped (Refs RAZ-16).

* ``normal``  monthly spend below the thrift threshold.
* ``thrift``  spend >= thrift threshold (default 80% of the cap) and < cap:
  live calls route to the cheapest configured model with a low max-tokens
  ceiling, and write/act paths (solver restarts, task execution, fix actions,
  delegate / orchestrator hops, scoped GO writes) are refused. Status and
  read-only Q&A still work.
* ``stopped`` spend >= cap (the HARD STOP): live model calls are refused
  (``SpendCutoffError``); mock mode still works.

The mode is derived from the spend ledger, so it lasts for the rest of the
calendar month. The ledger carries a ``month`` stamp (UTC ``YYYY-MM``, the
same boundary OpenRouter uses for ``usage_monthly``); the first ledger load
in a new month resets the monthly figures, which drops the mode back to
``normal`` automatically.

Env (all optional; see .env.example):
  CHIP_SPEND_CAP_USD        monthly hard stop in USD (default 5.00)
  CHIP_SPEND_THRIFT_PCT     thrift threshold as a fraction of the cap (default 0.80)
  CHIP_THRIFT_MODEL         model used in thrift mode (default qwen/qwen3.7-flash)
  CHIP_THRIFT_MAX_TOKENS    max completion tokens in thrift mode (default 256)
  CHIP_THRIFT_REPLY_CHARS   max reply length posted in thrift mode (default 600)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

NORMAL = "normal"
THRIFT = "thrift"
STOPPED = "stopped"

ENV_CAP = "CHIP_SPEND_CAP_USD"
ENV_THRIFT_PCT = "CHIP_SPEND_THRIFT_PCT"
ENV_THRIFT_MODEL = "CHIP_THRIFT_MODEL"
ENV_THRIFT_MAX_TOKENS = "CHIP_THRIFT_MAX_TOKENS"
ENV_THRIFT_REPLY_CHARS = "CHIP_THRIFT_REPLY_CHARS"

DEFAULT_THRIFT_PCT = 0.80
# Cheapest model already sanctioned for Chip Chat (dev-bot routing law).
DEFAULT_THRIFT_MODEL = "qwen/qwen3.7-flash"
DEFAULT_THRIFT_MAX_TOKENS = 256
DEFAULT_THRIFT_REPLY_CHARS = 600

NOTICE_PREFIX = "[chip-budget]"


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _float_env(env: Mapping[str, str], key: str) -> float | None:
    raw = (env.get(key) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _int_env(env: Mapping[str, str], key: str, default: int) -> int:
    raw = (env.get(key) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def cap_usd(environ: Mapping[str, str] | None = None) -> float:
    env_cap = _float_env(_env(environ), ENV_CAP)
    if env_cap is not None:
        return env_cap
    from chip import spend

    return float(spend.CUTOFF_MONTHLY_USD)


def thrift_pct(environ: Mapping[str, str] | None = None) -> float:
    pct = _float_env(_env(environ), ENV_THRIFT_PCT)
    if pct is None or pct > 1.0:
        return DEFAULT_THRIFT_PCT
    return pct


def thrift_usd(environ: Mapping[str, str] | None = None) -> float:
    return round(cap_usd(environ) * thrift_pct(environ), 6)


def thrift_model(environ: Mapping[str, str] | None = None) -> str:
    return (_env(environ).get(ENV_THRIFT_MODEL) or "").strip() or DEFAULT_THRIFT_MODEL


def thrift_max_tokens(environ: Mapping[str, str] | None = None) -> int:
    return _int_env(_env(environ), ENV_THRIFT_MAX_TOKENS, DEFAULT_THRIFT_MAX_TOKENS)


def thrift_reply_chars(environ: Mapping[str, str] | None = None) -> int:
    return _int_env(_env(environ), ENV_THRIFT_REPLY_CHARS, DEFAULT_THRIFT_REPLY_CHARS)


def mode_for(monthly_usd: float | None, environ: Mapping[str, str] | None = None) -> str:
    monthly = float(monthly_usd or 0.0)
    if monthly >= cap_usd(environ):
        return STOPPED
    if monthly >= thrift_usd(environ):
        return THRIFT
    return NORMAL


# --- month rollover -------------------------------------------------------


def month_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m")


def ledger_month(ledger: Mapping[str, Any]) -> str | None:
    month = str(ledger.get("month") or "").strip()
    if month:
        return month[:7]
    verified = str(ledger.get("verified_as_of") or "").strip()
    if len(verified) >= 7 and verified[4] == "-":
        return verified[:7]
    return None


def roll_month(ledger: dict[str, Any], now: datetime | None = None) -> bool:
    """Reset monthly figures when the ledger belongs to an earlier month.

    Returns True when a reset happened. A ledger with no month evidence is
    stamped with the current month and left untouched.
    """
    current = month_key(now)
    seen = ledger_month(ledger)
    if seen is None:
        ledger["month"] = current
        return False
    if seen >= current:
        ledger["month"] = seen  # same month (or clock skew): never reset backwards
        return False
    ledger["month"] = current
    ledger["previous_month"] = {"month": seen, "monthly_usd": ledger.get("monthly_usd")}
    ledger["monthly_usd"] = 0.0
    ledger["estimated_monthly_usd"] = 0.0
    ledger["monthly_source"] = "rollover"
    ledger["monthly_sync_session_usd"] = float(ledger.get("session_usd") or 0.0)
    ledger["budget_notices"] = {}
    return True


# --- status ---------------------------------------------------------------


def status(
    ledger: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    from chip import spend

    if ledger is None:
        ledger = spend.load_ledger()
    monthly = spend.ledger_monthly_usd(dict(ledger))
    mode = mode_for(monthly, environ)
    return {
        "mode": mode,
        "month": ledger_month(ledger),
        "monthly_usd": round(monthly, 4),
        "thrift_usd": thrift_usd(environ),
        "cap_usd": cap_usd(environ),
        "thrift_model": thrift_model(environ) if mode == THRIFT else None,
    }


def current_mode(environ: Mapping[str, str] | None = None) -> str:
    try:
        return str(status(environ=environ)["mode"])
    except Exception:  # noqa: BLE001 — unreadable ledger must not open act paths silently
        return NORMAL


def act_block_reason(action: str, environ: Mapping[str, str] | None = None) -> str | None:
    """Refusal text when write/act paths are off (thrift or stopped), else None."""
    try:
        snap = status(environ=environ)
    except Exception:  # noqa: BLE001
        return None
    if snap["mode"] == NORMAL:
        return None
    return (
        f"budget-{snap['mode']}: {action} disabled for the rest of the month "
        f"(monthly ${snap['monthly_usd']:.2f} / cap ${snap['cap_usd']:.2f}); "
        "read-only status and short Q&A only"
    )


def clip_reply(text: str, environ: Mapping[str, str] | None = None) -> str:
    """Thrift mode posts short replies only."""
    if current_mode(environ) == NORMAL:
        return text
    limit = thrift_reply_chars(environ)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


# --- one-shot notices -----------------------------------------------------


def notice_line(mode: str, snap: Mapping[str, Any]) -> str:
    if mode == STOPPED:
        return (
            f"{NOTICE_PREFIX} HARD STOP: monthly OpenRouter spend ${snap['monthly_usd']:.2f} "
            f">= cap ${snap['cap_usd']:.2f}. Live model calls refused until the new month "
            f"({snap['month']} rollover)."
        )
    return (
        f"{NOTICE_PREFIX} THRIFT MODE: monthly OpenRouter spend ${snap['monthly_usd']:.2f} "
        f">= ${snap['thrift_usd']:.2f} ({int(round(thrift_pct() * 100))}% of ${snap['cap_usd']:.2f} cap). "
        f"Cheap model {thrift_model()}, short replies, read-only (no restarts, tasks, or hops) "
        "until the new month."
    )


def maybe_post_notice(
    post: Callable[[str], bool],
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Post ONE thrift notice and ONE hard-stop notice per month. Returns the mode posted."""
    from chip import spend

    ledger = spend.load_ledger()
    snap = status(ledger, environ)
    mode = snap["mode"]
    if mode == NORMAL:
        return None
    notices = ledger.get("budget_notices")
    if not isinstance(notices, dict):
        notices = {}
    if notices.get(mode):
        return None
    if not post(notice_line(mode, snap)):
        return None
    notices[mode] = datetime.now(timezone.utc).isoformat()
    if mode == STOPPED:
        notices.setdefault(THRIFT, notices[mode])  # skip a stale thrift notice after the stop
    fresh = spend.load_ledger()
    fresh["budget_notices"] = notices
    spend.save_ledger(fresh)
    return mode
