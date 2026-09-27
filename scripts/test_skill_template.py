"""Slice B skeleton guard (template-only contract for internal-loop-degrade)."""

from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "docs" / "skill-templates" / "internal-loop-degrade.template.md"


def test_template_exists_and_is_skeleton() -> None:
    assert TEMPLATE.is_file()


def test_template_declares_pinned_contract_and_lane() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    # contract line MONITOR_CASES.md prescribed (Slice B pinning law)
    for needle in ("skill_id: internal-loop-degrade", 'skill_version: "<pinned razzorstack git SHA'):
        assert needle in text
    assert "lane: homelab" in text
    assert "class: propose" in text


def test_receipt_stub_keys_shape() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    for needle in (
        '"exit": 0',
        '"probe": {',
        '"verify_twice":',
        '"agrees": true',
        '"rollback":',
        "SERVICE_ALLOWLIST",
    ):
        assert needle in text


def test_template_contains_no_executor_wiring() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    # wiring patterns that must NOT appear in a template-only skeleton
    for bad in ("run_or_raise(", "tool.run(", "CHIP_TOOL_AUTO=1", "--execute"):
        assert bad not in text, f"executor wiring {bad!r} leaked into template"


def test_gate_and_done_bar_referenced() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "reopen_required" in text
    assert "claim done" in text and "Done" in text
    assert "verified-as-of" in text
