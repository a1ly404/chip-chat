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
