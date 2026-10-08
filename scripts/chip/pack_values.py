"""Deployment-specific constants read from the selected system pack (engine façade).

Callers use these helpers instead of hard-coding deployment-specific or operator-specific values.
Generic defaults are empty or safe; private packs supply production values.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Mapping

from chip import system_pack

_BASE_RESERVED = frozenset({"user", "inject", "relay"})

_DEFAULT_THRIFT_GO_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\broot cause\b", "operator_go"),
    (r"\bfigure out why\b", "operator_go"),
    (r"\binvestigate until\b", "operator_go"),
)

_DEFAULT_MOCK_PERSONA_ORDER: tuple[str, ...] = ("pm", "dev", "relay", "selftest")


@lru_cache(maxsize=1)
def _pack() -> dict[str, Any]:
    try:
        return system_pack.load_pack()
    except (OSError, ValueError):
        return {}


def _values() -> dict[str, Any]:
    raw = _pack().get("values")
    return raw if isinstance(raw, dict) else {}


def _allowlist(key: str) -> frozenset[str]:
    declared = _pack().get("allowlists", {}).get(key)
    if not declared:
        return frozenset()
    return frozenset(str(x) for x in declared)


def operator_discord_id() -> str:
    return str(_pack().get("identity", {}).get("operator_discord_id") or "").strip()


def operator_name() -> str:
    return str(_pack().get("identity", {}).get("operator_name") or "").strip()


def operator_cli_alias() -> str:
    return str(_values().get("operator_cli_alias") or operator_name() or "").strip()


def pager_handle() -> str:
    return str(_values().get("pager_handle") or "@oncall").strip()


def escalate_to_label() -> str:
    """Receipt ``escalate_to`` field (operator display name from pack identity)."""
    return operator_name() or "operator"


def reserved_claim_ids() -> frozenset[str]:
    extra = _values().get("reserved_claim_ids")
    if not extra:
        return _BASE_RESERVED
    return _BASE_RESERVED | frozenset(str(x).lower() for x in extra)


def service_allowlist() -> frozenset[str]:
    return _allowlist("service")


def mock_specialists() -> frozenset[str]:
    return _allowlist("mock_specialists")


def mock_persona_detect_order() -> tuple[str, ...]:
    raw = _values().get("mock_persona_detect_order")
    if not raw:
        return _DEFAULT_MOCK_PERSONA_ORDER
    return tuple(str(x) for x in raw)


def authorize_blocked_patterns() -> tuple[str, ...]:
    raw = _values().get("authorize_blocked_patterns")
    if not raw:
        return ()
    return tuple(str(x) for x in raw)


def authorize_blocked_target_label() -> str:
    return str(_values().get("authorize_blocked_target_label") or "storage").strip()


def cli_room_task_id_help() -> str:
    custom = _values().get("cli_room_task_id_help")
    if custom:
        return str(custom).strip()
    prefix = ticket_anchor_prefix()
    return f"task anchor ({prefix}-nn) binding this shot's ledger row to an outcome"


def cli_taskflow_bind_receipts_help() -> str:
    return str(
        _values().get("cli_taskflow_bind_receipts_help")
        or "comma-separated PR/commit/ticket anchors"
    ).strip()


def taskflow_deliverable_receipt_clause() -> str:
    return str(
        _values().get("taskflow_deliverable_receipt_clause")
        or "return receipts (PR/commit/ticket anchors); done = verified-as-of stamps, never claim-done-≠-Done."
    ).strip()


def relay_startup_refusal_post_help() -> str:
    return str(
        _values().get("relay_startup_refusal_post_help")
        or "Post the canned dead-image refusal and store that same body"
    ).strip()


def solver_forbidden_substrings() -> tuple[str, ...]:
    raw = _values().get("solver_forbidden_substrings")
    if not raw:
        return ()
    return tuple(str(x) for x in raw)


def monitor_skip_keys() -> frozenset[str]:
    raw = _values().get("monitor_skip_keys")
    if not raw:
        return frozenset()
    return frozenset(str(x) for x in raw)


def default_intake_channel() -> str:
    return str(_values().get("default_intake_channel") or "monitoring").strip()


def jev_learnings_personas() -> tuple[str, ...]:
    raw = _values().get("jev_learnings_personas")
    if not raw:
        return ("pm",)
    return tuple(str(x) for x in raw)


def default_data_dir_relative() -> str:
    """Path under $HOME when CHIP_CHAT_DATA_DIR is unset (pack ``default_data_dir_relative``)."""
    return str(_values().get("default_data_dir_relative") or ".local/share/chip-chat").strip()


def case_intake_legacy_ticket_keys() -> tuple[str, ...]:
    """Deprecated payload keys merged into ``related_ticket`` (pack-configured)."""
    raw = _values().get("case_intake_legacy_ticket_keys")
    if not raw:
        return ()
    return tuple(str(x) for x in raw)


def related_ticket_from_mapping(payload: Mapping[str, Any]) -> str:
    primary = str(payload.get("related_ticket") or "").strip()
    if primary:
        return primary
    for key in case_intake_legacy_ticket_keys():
        alt = str(payload.get(key) or "").strip()
        if alt:
            return alt
    return ""


def ticket_anchor_prefix() -> str:
    return str(_values().get("ticket_anchor_prefix") or "TICKET").strip().upper()


def ticket_anchor_pattern() -> "re.Pattern[str]":
    import re

    prefix = re.escape(ticket_anchor_prefix())
    alts = {ticket_anchor_prefix(), "TICKET"}
    inner = "|".join(sorted(re.escape(x) for x in alts))
    return re.compile(rf"\b(?:{inner})-\d+\b", re.IGNORECASE)


def case_intake_ticket_field_name() -> str:
    return str(_values().get("case_intake_ticket_field_name") or "related_ticket").strip()


def feed_alert_wire_keys() -> dict[str, str]:
    raw = _values().get("feed_alert_wire_keys")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def wire_feed_alert_key(engine_key: str) -> str:
    return feed_alert_wire_keys().get(engine_key, engine_key)


def text_source_legacy_streak_state_keys() -> tuple[str, ...]:
    raw = _values().get("text_source_legacy_streak_state_keys")
    if not raw:
        return ()
    return tuple(str(x) for x in raw)


def dead_image_fingerprint() -> str:
    return str(_values().get("dead_image_fingerprint") or "dead-image").strip()


def dead_image_refusal_lines(variant: str) -> tuple[str, ...]:
    templates = _values().get("dead_image_refusal_templates")
    if isinstance(templates, dict) and variant in templates:
        raw = templates[variant]
        if isinstance(raw, list):
            return tuple(str(x) for x in raw)
    defaults = {
        "refusal": (
            "claim: dead-image runbook is propose-only",
            "status: blocked",
            "evidence: none",
            "next: operator review",
        ),
        "human_followup": (
            "claim: dead-image stays propose-only",
            "status: blocked",
            "evidence: none",
            "next: no fix from this channel",
        ),
    }
    return defaults.get(variant, defaults["refusal"])


def runbook_propose_next_hint(runbook_id: str) -> str:
    hints = _values().get("runbook_propose_hints")
    if isinstance(hints, dict) and runbook_id in hints:
        return str(hints[runbook_id])
    return "operator review"


def openrouter_http_referer() -> str:
    return str(
        _values().get("openrouter_http_referer") or "https://github.com/chip-chat/chip-chat"
    ).strip()


def thrift_go_class() -> str:
    return str(_values().get("thrift_go_class") or "operator_go").strip()


def thrift_keyword_rows() -> tuple[tuple[str, str], ...]:
    """Static Spec-D rows; operator-go patterns are pack-overridable."""
    base: list[tuple[str, str]] = [
        (r"\bSTAND DOWN\b", "script"),
        (r"\bIDLE\b", "script"),
        (r"\brun the deploy workflow\b", "gha"),
        (r"\bdeploy workflow\b", "gha"),
        (r"\bworkflow_dispatch\b", "gha"),
        (r"\bexpensive\b", "expensive_agent"),
        (r"\banalyze deeply\b", "expensive_agent"),
    ]
    go_class = thrift_go_class()
    raw = _values().get("thrift_go_patterns")
    patterns = raw if raw else [p for p, _ in _DEFAULT_THRIFT_GO_PATTERNS]
    for pat in patterns:
        base.append((str(pat), go_class))
    return tuple(base)


def thrift_open_ended_pattern() -> str:
    raw = _values().get("thrift_open_ended_pattern")
    if raw:
        return str(raw)
    return r"(root cause|figure out why|investigate until)"


def rooms_config_hint() -> str:
    pack = _pack()
    if not pack:
        return "system pack rooms surface"
    try:
        rel = pack.get("surfaces", {}).get("rooms", "rooms.json")
    except (AttributeError, TypeError):
        rel = "rooms.json"
    return str(rel)
