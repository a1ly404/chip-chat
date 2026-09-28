"""Slice B skeleton guard (template-only contract for internal-loop-degrade)."""

from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "docs" / "skill-templates" / "internal-loop-degrade.template.md"


def test_template_exists_and_is_skeleton() -> None:
    assert TEMPLATE.is_file()


def _templates() -> list[Path]:
    return sorted(
        (Path(__file__).resolve().parents[1] / "docs" / "skill-templates").glob("*.template.md")
    )


def test_two_templates_present() -> None:
    names = [p.name for p in _templates()]
    assert "internal-loop-degrade.template.md" in names
    assert "transport-blip.template.md" in names  # generalization proof
    # family completed 2026-09-28 (queue item 4): remaining taxonomy drops 5+6
    assert "skill-runner-fault.template.md" in names
    assert "suppression-saturation.template.md" in names


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def check_template_contract(p: Path) -> None:
    text = _read(p)
    for needle in (
        "class: propose",          # registry law: never auto until proven
        'skill_version: "<pinned razzorstack git SHA',  # contract pinning
        "SERVICE_ALLOWLIST",       # guard-referenced rollback check
        "verified-as-of",          # done bar
        "claim done",              # claim done ≠ Done
    ):
        assert needle in text, f"{p.name}: contract field missing: {needle!r}"
    for bad in ("run_or_raise(", "tool.run(", "CHIP_TOOL_AUTO=1", "--execute"):
        assert bad not in text, f"{p.name}: executor wiring {bad!r} leaked into template"


def test_loop_degrade_template_contract() -> None:
    for p in _templates():
        if p.name == "internal-loop-degrade.template.md":
            check_template_contract(p)
            assert "lane: homelab" in _read(p)


def test_transport_blip_contract_and_silence_follows() -> None:
    p = next(x for x in _templates() if x.name == "transport-blip.template.md")
    check_template_contract(p)
    text = _read(p)
    assert "skill_id: transport-blip" in text
    # special law: transients stay silent on first sighting (verify-twice)
    assert "verify-twice" in text
    assert "transient-confirmed" in text and "persistent" in text
    assert "re-run-health-probe" in text  # registry auto class rollback tier


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


def test_gold_signal_down_contract() -> None:
    p = next(x for x in _templates() if x.name == "gold-signal-down.template.md")
    check_template_contract(p)
    text = _read(p)
    assert "skill_id: gold-signal-down" in text
    assert "discord-bot-loop-restart" in text  # registry-bound rollback id
    assert '"loops_ok": false' in text or '"loops_ok" : false' in text
    assert "standup_schedule.loops_ok" in text


def test_family_wide_base_contract() -> None:
    # every template in the dir satisfies the base contract — no exempt members
    for p in _templates():
        check_template_contract(p)


def test_skill_runner_fault_contract() -> None:
    p = next(x for x in _templates() if x.name == "skill-runner-fault.template.md")
    check_template_contract(p)
    text = _read(p)
    assert "skill_id: skill-runner-fault" in text
    assert "skill-runner-restart" in text  # registry-bound rollback tier
    assert "runner-attached" in text  # evidence receipts never agent-fabricated
    assert "timed_out|contract_drift|hang|recovered" in text  # fault classes enumerated
    assert '"source": "runner-receipt"' in text
    assert "lane: chip" in text


def test_suppression_saturation_contract() -> None:
    p = next(x for x in _templates() if x.name == "suppression-saturation.template.md")
    check_template_contract(p)
    text = _read(p)
    assert "skill_id: suppression-saturation" in text
    assert "clear-stale-suppress" in text
    assert "DEADLETTER_AFTER=10" in text
    assert "human_reopen" in text and '"required": true' in text  # never automated clear
    assert "SKIP_KEYS" in text
    assert "Warn-only" in text  # posts once per 4h tier
    assert "lane: chip" in text
