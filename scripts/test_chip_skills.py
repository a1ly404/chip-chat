"""Parity P1.3: reusable skills catalog (pack read-only + user skills)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from chip import skills as sk

GOOD = """---
name: Disk cleanup
description: When a disk usage alert fires, find large files before deleting anything.
triggers: [disk full, disk usage]
version: 2
---
# Steps
1. Check usage.
"""


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CHIP_SKILLS", raising=False)
    return tmp_path / "data"


@pytest.fixture
def pack(tmp_path) -> Path:
    d = tmp_path / "pack-skills"
    d.mkdir()
    (d / "checkout-latency.md").write_text(
        "# Checkout latency\n\nVerify checkout health and restart only when allowlisted.\n", encoding="utf-8"
    )
    (d / "disk-cleanup.md").write_text(GOOD, encoding="utf-8")
    return d


def test_flag_default_off() -> None:
    assert sk.enabled({}) is False
    assert sk.enabled({"CHIP_SKILLS": "1"}) is True


def test_parse_front_matter() -> None:
    s = sk.parse(GOOD, skill_id="disk-cleanup", source="user")
    assert s.name == "Disk cleanup"
    assert s.triggers == ("disk full", "disk usage")
    assert s.version == "2"
    assert s.body.startswith("# Steps")


@pytest.mark.parametrize(
    "text,reason",
    [
        ("# no header\nbody\n", "missing front-matter"),
        ("---\nname: x\n", "unterminated"),
        ("---\nname: x\n---\nbody\n", "missing description"),
        ("---\ndescription: d\n---\nbody\n", "missing name"),
        ("---\nname: x\ndescription: d\n---\n\n", "empty body"),
        ("---\nname x\n---\nbody\n", "bad front-matter"),
    ],
)
def test_parse_invalid(text: str, reason: str) -> None:
    with pytest.raises(sk.SkillError, match=reason):
        sk.parse(text, skill_id="x", source="user")


def test_bad_id_rejected() -> None:
    with pytest.raises(sk.SkillError, match="invalid skill id"):
        sk.parse(GOOD, skill_id="../etc", source="user")


def test_legacy_pack_body_without_front_matter(pack: Path) -> None:
    cat = sk.load_catalog(pack=pack)
    s = cat.get("checkout-latency")
    assert s is not None and s.source == "pack"
    assert s.name == "Checkout latency"
    assert s.description.startswith("Verify checkout health")


def test_invalid_user_skill_skipped_with_reason(pack: Path, _data_dir: Path) -> None:
    udir = _data_dir / "skills"
    udir.mkdir(parents=True)
    (udir / "broken.md").write_text("no header\n", encoding="utf-8")
    cat = sk.load_catalog(pack=pack)
    assert cat.get("broken") is None
    assert any(r["id"] == "broken" and "front-matter" in r["reason"] for r in cat.skipped)
    assert {s.id for s in cat.list()} == {"checkout-latency", "disk-cleanup"}


def test_user_cannot_shadow_pack_without_override(pack: Path) -> None:
    cat = sk.load_catalog(pack=pack)
    with pytest.raises(sk.SkillError, match="read-only pack skill"):
        sk.add_skill("disk-cleanup", name="Mine", description="d", body="b", catalog=cat)
    # A hand-written shadow file is ignored with a reason.
    udir = sk.user_dir()
    udir.mkdir(parents=True)
    (udir / "disk-cleanup.md").write_text(GOOD.replace("Disk cleanup", "Sneaky"), encoding="utf-8")
    cat = sk.load_catalog(pack=pack)
    assert cat.get("disk-cleanup").source == "pack"
    assert any("shadows pack" in r["reason"] for r in cat.skipped)


def test_override_shadows_pack(pack: Path) -> None:
    cat = sk.load_catalog(pack=pack)
    sk.add_skill("disk-cleanup", name="Mine", description="d", body="b", override=True, catalog=cat)
    cat = sk.load_catalog(pack=pack)
    assert cat.get("disk-cleanup").source == "user"
    assert cat.get("disk-cleanup").name == "Mine"


def test_add_list_merge_and_replace(pack: Path) -> None:
    sk.add_skill("weekly-report", name="Weekly report", description="Summarise the week", body="Do it", triggers=["weekly"])
    cat = sk.load_catalog(pack=pack)
    assert [s.id for s in cat.list()] == ["checkout-latency", "disk-cleanup", "weekly-report"]
    with pytest.raises(sk.SkillError, match="already exists"):
        sk.add_skill("weekly-report", name="W", description="d", body="b", catalog=cat)
    sk.add_skill("weekly-report", name="W2", description="d", body="b", replace=True, catalog=cat)
    assert sk.load_catalog(pack=pack).get("weekly-report").name == "W2"
    assert sk.delete_skill("weekly-report") is True
    assert sk.delete_skill("weekly-report") is False


def test_add_refuses_secret_and_header_injection() -> None:
    with pytest.raises(sk.SkillError, match="secret"):
        sk.add_skill("s", name="n", description="d", body="pass" + "word" + "=" + "x" * 8, catalog=sk.Catalog())
    with pytest.raises(sk.SkillError, match="delimiters|single line"):
        sk.add_skill("s", name="n\n---\nname: evil", description="d", body="b", catalog=sk.Catalog())


def test_search_and_select(pack: Path) -> None:
    cat = sk.load_catalog(pack=pack)
    assert [s.id for s in sk.search(cat, "checkout health")][0] == "checkout-latency"
    assert sk.search(cat, "the and of") == []
    assert [s.id for s in sk.select(cat, "Alert: DISK FULL on node-3")] == ["disk-cleanup"]
    # Trigger must match on word boundaries.
    assert "disk-cleanup" not in [s.id for s in sk.select(cat, "diskfullness unrelated")]
    # Falls back to word search when no trigger matches.
    assert [s.id for s in sk.select(cat, "checkout latency is high")][0] == "checkout-latency"


def test_injection_respects_budget(pack: Path) -> None:
    cat = sk.load_catalog(pack=pack)
    chosen = cat.list()
    full = sk.injection_block(chosen, budget_chars=10_000)
    assert "## Skill: Checkout latency" in full and "## Skill: Disk cleanup" in full
    for budget in (0, 10, 60, 120, 200, len(full) - 1, len(full)):
        block = sk.injection_block(chosen, budget_chars=budget)
        assert len(block) <= budget
    assert sk.injection_block(chosen, budget_chars=len(full)) == full
    first = sk.injection_block(chosen[:1], budget_chars=10_000)
    cut = sk.injection_block(chosen[:1], budget_chars=len(first) - 10)
    assert cut.endswith("[truncated]\n") and len(cut) <= len(first) - 10


def test_default_pack_surface_resolves() -> None:
    # The repo's example pack ships a skill body; catalog must load it without error.
    cat = sk.load_catalog()
    assert all(r["source"] != "pack" for r in cat.skipped), cat.skipped


def _cli(args: list[str], data: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CHIP_CHAT_DATA_DIR"] = str(data)
    root = Path(__file__).resolve().parent
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "chip", "skill", *args], capture_output=True, text=True, env=env, timeout=60)


def test_cli_transcript(_data_dir: Path, tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("# Steps\n1. Look.\n", encoding="utf-8")
    r = _cli(["add", "triage-ci", "--name", "Triage CI", "--description", "When CI fails on main",
              "--body-file", str(body), "--trigger", "ci failed", "--mock"], _data_dir)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["id"] == "triage-ci"
    r = _cli(["list", "--mock"], _data_dir)
    assert r.returncode == 0, r.stderr
    assert "triage-ci" in [s["id"] for s in json.loads(r.stdout)["skills"]]
    r = _cli(["show", "triage-ci", "--mock"], _data_dir)
    assert json.loads(r.stdout)["body"].startswith("# Steps")
    r = _cli(["search", "ci fails", "--mock"], _data_dir)
    assert json.loads(r.stdout)[0]["id"] == "triage-ci"
    r = _cli(["show", "nope", "--mock"], _data_dir)
    assert r.returncode == 1
    r = _cli(["add", "../x", "--name", "n", "--description", "d", "--body", "b", "--mock"], _data_dir)
    assert r.returncode == 2
    r = _cli(["delete", "triage-ci", "--mock"], _data_dir)
    assert r.returncode == 0, r.stderr
