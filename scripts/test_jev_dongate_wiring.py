"""Jev live wiring into the done-gate sweep (Ally GO 2026-10-05).

Law: shadow consult only — jev NEVER changes severity or flips a gate.
- Without a live GO (env absent), sweep() output is byte-identical to the
  offline law: no `jev` key, no HTTP, no ledger noise.
- With CHIP_JEV_LIVE=1 + key + mock, findings get a shadow annotation
  (bands recorded as hypothetical).
- The registry-first ban still holds BEFORE any HTTP attempt (row #1 class).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from chip import config, done_gate, jev_router

_ISSUE_REOPEN = {
    "id": "RAZ-GRAYPARTIAL",
    "status": "Done",
    "statusType": "completed",
    "description": (
        "## AC\n- [x] do the thing 1\nverified-as-of: 2026-10-05 10:00\n"
        "- [x] do the thing 2\n- [x] do the thing 3\n"
    ),
}


def test_offline_sweep_unchanged_no_jev_key() -> None:
    findings = done_gate.sweep([_ISSUE_REOPEN])
    assert findings and findings[0]["severity"] == "reopen-required"
    assert "jev" not in findings[0]


def test_no_annotation_without_live_go(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    env = {"CHIP_JEV_LIVE": "1"}  # live flag but NO key — must not consult
    called = {}

    def boom(*a, **k):
        called["touched"] = True
        raise AssertionError("HTTP attempted without OPENROUTER_CC_API_KEY")

    with patch("urllib.request.urlopen", boom):
        findings = done_gate.sweep([_ISSUE_REOPEN], environ=env)
    assert "jev" not in findings[0]
    assert "touched" not in called


def test_live_consult_annotates_shadow(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    env = {"CHIP_JEV_LIVE": "1", "OPENROUTER_CC_API_KEY": "k-test"}
    called = {}

    def fake_ask(state, questions, *, call_site, environ=None, mock=False, registry_path=None):
        called["state"] = dict(state)
        called["call_site"] = call_site
        return {
            "id": "gen-dec-mock",
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"reopen_or_baseline": {"type": "noul", "noul": 0.51}},
            "usage": {"cost": 0.00002},
        }

    with patch.object(jev_router, "ask", side_effect=fake_ask):
        findings = done_gate.sweep([_ISSUE_REOPEN], environ=env)
    ann = findings[0]["jev"]
    assert ann["band_hypothetical"] == "needs-repro"  # 0.51 < room_min 0.80
    assert ann["action"].startswith("conservative")
    assert findings[0]["severity"] == "reopen-required"  # severity UNTOUCHED
    assert called["call_site"] == "done-gate-sweep"
    # registry-banned shape fields still present in the state (ban logic lives in ask())


def test_registry_ban_blocks_http(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    env = {"CHIP_JEV_LIVE": "1", "OPENROUTER_CC_API_KEY": "k-test"}
    provider = {}

    def boom(*a, **k):
        provider["touched"] = True

    # fully deterministic row-1 shape: zero receipts bound
    issue = {
        "id": "RAZ-ROW1",
        "status": "Done",
        "statusType": "completed",
        "description": "## AC\n- [x] a\n- [x] b\n",
    }
    with patch("urllib.request.urlopen", boom):
        findings = done_gate.sweep([issue], environ=env)
    assert "jev" not in findings[0]  # blocked-registry-hit has no answers => no annotation
    assert "touched" not in provider  # never reached the HTTP layer
