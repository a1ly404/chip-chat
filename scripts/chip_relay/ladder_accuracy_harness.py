"""Run accuracy fixtures through production intake/ladder code with canned LLM only ($0)."""

from __future__ import annotations

import json
from typing import Any, Callable

from chip_relay import case_intake_ladder as ladder
from chip_relay.case_intake import CaseFile, parse_freeform
from chip_relay.case_intake_ladder import StageFn
from chip_relay.monitoring_alert_adapter import is_informational, try_adapter
from chip_relay.monitoring_skills import (
    load_policy,
    match_skill,
    parse_monitoring_message,
    run_skill_for_alert,
    should_ignore_alert,
)


def make_canned_stage_fn(script: dict[str, Any]) -> StageFn:
    """Replay recorded tier payloads; only the OpenRouter call is replaced."""
    attempts: list[dict[str, Any]] = list(script.get("attempts") or [])
    if not attempts and script.get("executor"):
        attempts = [{"executor": script["executor"], "verifier": script.get("verifier") or {"pass": True, "reason": "ok"}}]
    pm = script.get("pm") or {"goal": "triage", "acceptance": ["receipted verdict"], "lane": "chipchatdev"}
    exec_calls = 0
    base_pm = {"_model": "canned/pm", "_cost_usd": 0.0, "_tokens": 0}

    def _fn(tier: str, payload: str, cfg: dict[str, Any]) -> dict[str, Any]:
        nonlocal exec_calls
        base = {"_model": f"canned/{tier}", "_cost_usd": 0.0, "_tokens": 0}
        if tier == "pm":
            return {**base_pm, **pm}
        if tier == "executor":
            i = min(exec_calls, len(attempts) - 1)
            exec_calls += 1
            ex = dict(attempts[i].get("executor") or {})
            if ex.get("_glm_empty"):
                return {**base, "_parse_error": "finish=stop:empty assistant", "claims": []}
            return {**base, **ex}
        i = min(max(exec_calls - 1, 0), len(attempts) - 1)
        ver = dict(attempts[i].get("verifier") or {"pass": True, "reason": "ok"})
        return {**base, **ver}

    return _fn


def _canned_parser(payload: dict[str, Any]) -> Callable[[str, str], dict[str, Any]]:
    def _parser(_msg: str, _model: str) -> dict[str, Any]:
        out = dict(payload)
        out.setdefault("_tokens", 0)
        return out

    return _parser


def _claims_from_case(case: dict[str, Any], verdict: str) -> list[dict[str, Any]]:
    claims = []
    for ref in ("description", "repro_context"):
        text = str(case.get(ref) or "")
        if text:
            claims.append(
                {
                    "claim": ref,
                    "receipt": {"kind": "case_field", "ref": ref, "excerpt": text.strip()[:60]},
                }
            )
    return claims or [{"claim": "x", "receipt": {"kind": "case_field", "ref": "description", "excerpt": str(case.get("description") or "x")[:60]}}]


def default_ladder_script(case: dict[str, Any], verdict: str) -> dict[str, Any]:
    return {
        "attempts": [
            {
                "executor": {"verdict": verdict, "summary": "canned", "claims": _claims_from_case(case, verdict)},
                "verifier": {"pass": True, "reason": "ok"},
            }
        ]
    }


def run_fixture(
    fx: dict[str, Any],
    *,
    data_root: Any,
    repo_root: Any,
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one fixture; returns observed fields for the frozen scorer."""
    route = str(fx.get("route") or "ladder")
    limits = limits or {"primary_model": "canned", "fallback_model": "canned", "pending_clarify_ttl_s": 3600, "pending_clarify_max_cycles": 3}

    if route == "informational":
        raw = str(fx.get("raw_input") or "")
        return {"route": route, "informational": is_informational(raw)}

    if route == "adapter":
        raw = str(fx.get("raw_input") or "")
        wid = fx.get("webhook_id")
        from chip import pack_values

        channel = pack_values.default_intake_channel()
        res = try_adapter(raw, webhook_id=str(wid) if wid is not None else None, channel=channel, message_id="acc")
        obs: dict[str, Any] = {"route": route, "kind": res.kind, "note": res.note or ""}
        if res.case is not None:
            obs["case_id"] = res.case.id
            obs["fingerprint_class"] = res.case.fingerprint_class
        return obs

    if route == "parse":
        from chip_relay.case_intake import load_limits

        lim = load_limits()
        canned = fx.get("canned_parse") or {}
        result = parse_freeform(
            str(fx.get("raw_input") or ""),
            parser=_canned_parser(canned),
            limits=lim,
            monthly_usd=0.0,
            skip_spend_gate=True,
        )
        obs = {"route": route, "kind": result.kind}
        if result.pending:
            obs["missing"] = list(result.pending.missing)
        if result.case is not None:
            obs["case_id"] = result.case.id
        return obs

    if route == "monitoring_skill":
        from unittest.mock import patch

        raw = str(fx.get("raw_input") or "")
        alert = parse_monitoring_message(raw)
        if should_ignore_alert(alert):
            return {"route": route, "ignored": True}
        skill = match_skill(alert)
        policy = load_policy()
        t0 = 8_000_000.0
        with patch("chip_relay.monitoring_skills._docker_running", return_value=False), patch(
            "chip_relay.monitoring_skills._docker_restart", return_value=(True, "mock")
        ):
            run_skill_for_alert(alert, now=t0)
            run = run_skill_for_alert(alert, now=t0 + policy.soak_seconds + 1.0)
        fix = (skill.fix_action if skill else {}) or {}
        return {
            "route": route,
            "skill_id": run.skill_id or (skill.id if skill else ""),
            "fix_kind": str(fix.get("kind") or ""),
            "ignored": run.ignored,
            "followup": (skill.followup if skill else "")[:120],
        }

    if route == "ladder":
        case = dict(fx.get("case") or {})
        case.setdefault("id", fx["id"])
        case.setdefault("synthetic", True)
        script = fx.get("canned_ladder") or default_ladder_script(case, str(fx.get("canned_verdict") or "needs-repro"))
        stage = make_canned_stage_fn(script)
        out = ladder.run_ladder(
            case,
            stage_fn=stage,
            data_root=data_root,
            repo_root=repo_root,
            case_path=None,
            evidence=str(case.get("repro_context") or ""),
            synthetic=True,
        )
        return {
            "route": route,
            "status": out.get("status"),
            "verdict": out.get("verdict"),
            "bounces": int(out.get("bounces") or 0),
            "first_try": out.get("bounces") == 0 and out.get("status") == "verified",
        }

    raise ValueError(f"unknown route: {route}")
