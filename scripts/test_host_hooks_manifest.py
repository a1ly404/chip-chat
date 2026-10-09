"""RAZ-360: host-hook manifest schema + sample scheduler crontab shape."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "plugins" / "example" / "host-hooks.yaml"
DOC = REPO / "docs" / "host-hooks.md"
CRONTAB = REPO / "docs" / "examples" / "scheduler.crontab"
REQUIRED_HOOKS = {
    "host-watchdog",
    "host-detect-engine",
    "host-repair-docker",
    "host-restart-stack",
    "host-scheduler-receipt-check",
}
OSES = {"linux", "macos", "windows"}
CRON_FIELD = re.compile(r"^[\d*/,\-]+$")


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_schema() -> None:
    m = _manifest()
    assert m["version"] == 1
    assert isinstance(m["receipts_dir"], str)
    hooks = m["hooks"]
    assert set(hooks) == REQUIRED_HOOKS
    for name, h in hooks.items():
        assert isinstance(h["cadence"], str) and h["cadence"], name
        assert isinstance(h["may_restart"], bool), name
        assert set(h["os"]) == OSES, name
        assert all(isinstance(v, str) and v for v in h["os"].values()), name


def test_doc_names_every_hook_and_readme_links_it() -> None:
    doc = DOC.read_text(encoding="utf-8")
    for name in REQUIRED_HOOKS:
        assert f"`{name}`" in doc, name
    assert "docs/host-hooks.md" in (REPO / "README.md").read_text(encoding="utf-8")


def test_sample_crontab_parses() -> None:
    jobs = []
    for line in CRONTAB.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        assert len(parts) == 7, line
        assert all(CRON_FIELD.match(f) for f in parts[:5]), line
        assert parts[5] == "bin/chip-job", line
        jobs.append(parts[6])
    assert jobs == ["morning-standup", "done-gate-watchdog", "case-intake-beat"]


def test_example_is_generic() -> None:
    text = MANIFEST.read_text(encoding="utf-8") + CRONTAB.read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z]:\\Users|\d+\.\d+\.\d+\.\d+|https?://", text)
