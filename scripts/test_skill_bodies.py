"""Slice B body drafts: family contract must survive the template→body move.

Guard law: every docked body carries the base contract needles (same set the
template guard enforces) + its template-id pairing per docs/skill-bodies/
MANIFEST.md. Zero executor wiring. The movable set is exactly the manifest
table; an extra/wandering file fails CI.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "chip_relay") if (Path(__file__).parent / "chip_relay").is_dir() else str(Path(__file__).parent))
from test_skill_template import _templates, check_template_contract  # noqa: E402

BODIES_DIR = Path(__file__).resolve().parents[1] / "docs" / "skill-bodies"

EXPECTED_PAIRS = {
    "internal-loop-degrade": "internal-loop-degrade.template.md",
    "transport-blip": "transport-blip.template.md",
    "gold-signal-down": "gold-signal-down.template.md",
    "skill-runner-fault": "skill-runner-fault.template.md",
    "suppression-saturation": "suppression-saturation.template.md",
    "airvpn-port-unhealthy": "airvpn-port-unhealthy.template.md",
}


def _bodies():
    return sorted(BODIES_DIR.glob("*.body.md"))


def test_manifest_pairs_complete():
    names = {p.name.removesuffix(".body.md") for p in _bodies()}
    assert names == set(EXPECTED_PAIRS), f"drift: {names ^ set(EXPECTED_PAIRS)}"


def test_base_contract_survives_the_move():
    import pytest

    for p in _bodies():
        text = p.read_text(encoding="utf-8")
        pid = p.name.removesuffix(".body.md")
        tpl = next(t for t in _templates() if t.name == EXPECTED_PAIRS[pid])
        base = ["class: propose",
                'skill_version: "<pinned razzorstack git SHA', "SERVICE_ALLOWLIST",
                "verified-as-of", "claim done"]
        for needle in base:
            assert needle in text, f"{p.name}: missing {needle!r}"
        for bad in ("run_or_raise(", "tool.run(", "CHIP_TOOL_AUTO=1", "--execute"):
            assert bad not in text, f"{p.name}: wiring leaked {bad!r}"
        # body inherits its template's lane + destination line
        assert f"skill_id: {pid}" in text
        tmpl_lane = re.search(r"lane: (\S+)", tpl.read_text(encoding="utf-8")).group(1)
        assert f"lane: {tmpl_lane}" in text


def test_manifest_rows_match_files():
    man = (BODIES_DIR / "MANIFEST.md").read_text(encoding="utf-8")
    for pid in EXPECTED_PAIRS:
        assert f"{pid}.body.md" in man
        assert f"`.cursor/skills/{pid}.md`" in man
