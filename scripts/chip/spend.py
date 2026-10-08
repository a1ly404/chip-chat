"""OpenRouter spend ledger and $5 warn / $10 cutoff gates (Chip Chat key only)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from chip.config import data_dir
from chip import openrouter
from chip.store import append_jsonl

WARN_MONTHLY_USD = 5.0
CUTOFF_MONTHLY_USD = 10.0

# Approximate list USD per 1M tokens for token_estimate fallback (hard-stop only; not pricing SoT).
# GLM / DeepSeek flash family ballparks via OpenRouter:
#   glm-5.3-flash ~$0.10/M prompt, ~$0.40/M completion
#   deepseek-v4.1-flash ~$0.07/M prompt, ~$0.27/M completion
_GLM_PROMPT_PER_M = 0.10
_GLM_COMPLETION_PER_M = 0.40
_DEEPSEEK_PROMPT_PER_M = 0.07
_DEEPSEEK_COMPLETION_PER_M = 0.27
_DEFAULT_PROMPT_PER_M = 1.0
_DEFAULT_COMPLETION_PER_M = 2.0


class SpendCutoffError(openrouter.ChipConfigError):
    """Monthly spend at or above cutoff; live completions refused."""


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def ledger_json_path():
    return data_dir() / "spend.json"


def ledger_jsonl_path():
    return data_dir() / "spend.jsonl"


def standup_facts_path():
    return data_dir() / "standup_facts.jsonl"


def default_ledger() -> dict[str, Any]:
    return {
        "verified_as_of": None,
        "monthly_usd": 0.0,
        "monthly_source": "ledger",
        # session_usd snapshot when monthly_usd was last aligned to API (or estimate baseline).
        "monthly_sync_session_usd": 0.0,
        "session_usd": 0.0,
        "estimated_monthly_usd": 0.0,
        "last_prompt_tokens": 0,
        "last_completion_tokens": 0,
        "last_model": None,
        "last_command": None,
    }


def load_ledger() -> dict[str, Any]:
    path = ledger_json_path()
    if not path.is_file():
        return default_ledger()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default_ledger()
    base = default_ledger()
    if isinstance(raw, dict):
        base.update(raw)
    return base


def save_ledger(ledger: dict[str, Any]) -> None:
    path = ledger_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")


def _parse_usd(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def monthly_usd_from_auth(info: dict[str, Any]) -> float | None:
    """OpenRouter key usage when positive; zero/absent means no authoritative signal."""
    for key in ("usage_monthly", "usage", "usage_daily"):
        parsed = _parse_usd(info.get(key))
        if parsed is not None and parsed > 0:
            return parsed
    return None


def estimate_token_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    slug = (model or "").lower()
    if "glm" in slug or slug.startswith("z-ai/"):
        p_rate, c_rate = _GLM_PROMPT_PER_M, _GLM_COMPLETION_PER_M
    elif "deepseek" in slug:
        p_rate, c_rate = _DEEPSEEK_PROMPT_PER_M, _DEEPSEEK_COMPLETION_PER_M
    else:
        p_rate, c_rate = _DEFAULT_PROMPT_PER_M, _DEFAULT_COMPLETION_PER_M
    return (prompt_tokens / 1_000_000.0) * p_rate + (completion_tokens / 1_000_000.0) * c_rate


def cost_from_completion_payload(payload: dict[str, Any], model: str) -> tuple[float, str, int, int]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    for cost_key in ("total_cost", "cost", "generation_cost"):
        api_cost = _parse_usd(usage.get(cost_key))
        if api_cost is not None and api_cost > 0:
            return api_cost, "api", prompt_tokens, completion_tokens
    if prompt_tokens or completion_tokens:
        est = estimate_token_cost_usd(model, prompt_tokens, completion_tokens)
        return est, "token_estimate", prompt_tokens, completion_tokens
    return 0.0, "token_estimate", prompt_tokens, completion_tokens


def monthly_session_delta_usd(ledger: dict[str, Any]) -> float:
    """Spend since monthly_usd was last synced (OpenRouter key usage can lag behind completions)."""
    if "monthly_sync_session_usd" not in ledger:
        return 0.0
    session = float(ledger.get("session_usd") or 0.0)
    sync = float(ledger.get("monthly_sync_session_usd") or 0.0)
    return max(0.0, session - sync)


def ledger_monthly_baseline_usd(ledger: dict[str, Any]) -> float:
    stored = _parse_usd(ledger.get("monthly_usd"))
    if stored is not None and stored > 0:
        return stored
    return float(ledger.get("estimated_monthly_usd") or 0.0)


def effective_monthly_usd(ledger: dict[str, Any], api_monthly: float | None) -> float:
    """Gate/monthly figure: max(OpenRouter key usage, ledger baseline + session delta since sync)."""
    local = ledger_monthly_baseline_usd(ledger) + monthly_session_delta_usd(ledger)
    if api_monthly is not None and api_monthly > 0:
        return max(api_monthly, local)
    if local > 0:
        return local
    return float(ledger.get("estimated_monthly_usd") or 0.0)


def ledger_monthly_usd(ledger: dict[str, Any]) -> float:
    """Monthly spend from on-disk ledger (relay heartbeats, selftest, gates without a fresh auth call)."""
    return effective_monthly_usd(ledger, None)


def write_standup_fact(fact: str) -> None:
    append_jsonl(standup_facts_path(), {"ts": _ts(), "fact": fact})


def emit_monthly_warn(monthly_usd: float) -> None:
    msg = (
        f"WARN: Chip Chat OpenRouter monthly spend ${monthly_usd:.2f} "
        f">= ${WARN_MONTHLY_USD:.2f} (OPENROUTER_CC_API_KEY)"
    )
    print(msg, file=sys.stderr)
    write_standup_fact(f"chip-chat: monthly spend WARN ${monthly_usd:.2f} (threshold ${WARN_MONTHLY_USD:.2f})")


def enforce_cutoff(monthly_usd: float) -> None:
    if monthly_usd >= CUTOFF_MONTHLY_USD:
        raise SpendCutoffError(
            f"Chip Chat spend cutoff: monthly ${monthly_usd:.2f} >= ${CUTOFF_MONTHLY_USD:.2f}. "
            "Live OpenRouter calls refused (mock mode still allowed)."
        )


def _apply_api_monthly_to_ledger(ledger: dict[str, Any], api_monthly: float) -> None:
    """Refresh API baseline when usage moves; keep sync point so session deltas accrue until then."""
    prev = _parse_usd(ledger.get("monthly_usd")) or 0.0
    source = str(ledger.get("monthly_source") or "")
    session = float(ledger.get("session_usd") or 0.0)
    if source != "api" or api_monthly > prev:
        ledger["monthly_usd"] = api_monthly
        ledger["monthly_sync_session_usd"] = session
    ledger["monthly_source"] = "api"


def refresh_from_auth(key: str) -> tuple[dict[str, Any], float | None]:
    info = openrouter.auth_key_info(key)
    api_monthly = monthly_usd_from_auth(info)
    ledger = load_ledger()
    ledger["verified_as_of"] = _ts()
    if api_monthly is not None and api_monthly > 0:
        _apply_api_monthly_to_ledger(ledger, api_monthly)
    save_ledger(ledger)
    return ledger, api_monthly


def guard_before_live_completion(command: str, key: str) -> float:
    ledger, api_monthly = refresh_from_auth(key)
    monthly = effective_monthly_usd(ledger, api_monthly)
    enforce_cutoff(monthly)
    if monthly >= WARN_MONTHLY_USD:
        emit_monthly_warn(monthly)
    ledger["last_command"] = command
    save_ledger(ledger)
    return monthly


def record_after_live_completion(
    command: str,
    model: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
) -> dict[str, Any]:
    cost_usd, source, prompt_tokens, completion_tokens = cost_from_completion_payload(payload, model)
    ledger = load_ledger()
    ledger["session_usd"] = float(ledger.get("session_usd") or 0.0) + cost_usd
    ledger["last_prompt_tokens"] = prompt_tokens
    ledger["last_completion_tokens"] = completion_tokens
    ledger["last_model"] = model
    ledger["last_command"] = command
    ledger["verified_as_of"] = _ts()
    save_ledger(ledger)

    api_monthly: float | None = None
    if key:
        try:
            ledger, api_monthly = refresh_from_auth(key)
        except openrouter.ChipConfigError:
            api_monthly = None
            ledger = load_ledger()

    if api_monthly is not None and api_monthly > 0:
        _apply_api_monthly_to_ledger(ledger, api_monthly)
    elif cost_usd > 0:
        ledger["estimated_monthly_usd"] = float(ledger.get("estimated_monthly_usd") or 0.0) + cost_usd
        current = _parse_usd(ledger.get("monthly_usd"))
        if current is None or current <= 0:
            ledger["monthly_usd"] = ledger["estimated_monthly_usd"]
            ledger["monthly_source"] = "token_estimate"
            ledger["monthly_sync_session_usd"] = float(ledger.get("session_usd") or 0.0)

    save_ledger(ledger)

    event = {
        "ts": _ts(),
        "verified_as_of": ledger["verified_as_of"],
        "source": source,
        "monthly_usd": ledger.get("monthly_usd"),
        "session_usd": ledger.get("session_usd"),
        "call_cost_usd": cost_usd,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "model": model,
        "command": command,
    }
    append_jsonl(ledger_jsonl_path(), event)
    return ledger


def format_summary(ledger: dict[str, Any]) -> str:
    monthly = ledger_monthly_usd(ledger)
    return (
        f"tokens prompt={ledger.get('last_prompt_tokens')} completion={ledger.get('last_completion_tokens')} "
        f"monthly_usd={monthly} session_usd={ledger.get('session_usd')} "
        f"source={ledger.get('monthly_source')}"
    )


def print_spend_report(key: str | None = None) -> int:
    ledger = load_ledger()
    if key:
        try:
            ledger, api_monthly = refresh_from_auth(key)
            monthly = effective_monthly_usd(ledger, api_monthly)
        except openrouter.ChipConfigError as exc:
            print(f"auth/key: ERROR {exc}")
            monthly = effective_monthly_usd(ledger, None)
    else:
        monthly = ledger_monthly_usd(ledger)

    print(f"data_dir={data_dir()}")
    print(f"verified_as_of={ledger.get('verified_as_of')}")
    print(f"monthly_usd={ledger_monthly_usd(ledger)} source={ledger.get('monthly_source')}")
    print(f"estimated_monthly_usd={ledger.get('estimated_monthly_usd')}")
    print(f"session_usd={ledger.get('session_usd')}")
    print(f"last_model={ledger.get('last_model')} last_command={ledger.get('last_command')}")
    print(f"last_tokens prompt={ledger.get('last_prompt_tokens')} completion={ledger.get('last_completion_tokens')}")
    print(f"thresholds warn=${WARN_MONTHLY_USD:.2f} cutoff=${CUTOFF_MONTHLY_USD:.2f}")
    if monthly >= CUTOFF_MONTHLY_USD:
        print("status=CUTOFF")
    elif monthly >= WARN_MONTHLY_USD:
        print("status=WARN")
    else:
        print("status=OK")
    return 0
