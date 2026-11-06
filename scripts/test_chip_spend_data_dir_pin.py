"""Engine parity plan slice B: fail-closed spend data-dir pin (Refs RAZ-16).

Offline: no OpenRouter calls, no webhooks. The live guard raises before any
network or ledger write; mock runs bypass the live guard entirely.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chip import spend, spend_data_dir
from chip.spend import SpendCutoffError
from chip.spend_data_dir import PIN_ENV, SpendDataDirNotPinned


def test_pin_default_off():
    assert spend_data_dir.pin_enabled({}) is False
    assert spend_data_dir.pin_enabled({PIN_ENV: ""}) is False
    assert spend_data_dir.pin_enabled({PIN_ENV: "0"}) is False
    assert spend_data_dir.pin_enabled({PIN_ENV: "false"}) is False
    assert spend_data_dir.pin_enabled({PIN_ENV: "1"}) is True
    assert spend_data_dir.pin_enabled({PIN_ENV: "yes"}) is True


def test_pin_off_keeps_legacy_paths():
    """Without the pin flag, ledger resolution still returns a Path (no hard break)."""
    assert spend.ledger_json_path().name == "spend.json"


def test_pin_on_without_env_refuses(monkeypatch, tmp_path):
    monkeypatch.setenv(PIN_ENV, "1")
    monkeypatch.delenv("CHIP_CHAT_DATA_DIR", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    with pytest.raises(SpendDataDirNotPinned) as exc:
        spend_data_dir.pinned_data_dir()
    assert "refused" in str(exc.value)
    assert (tmp_path / "spend.json").is_file() is False


def test_pin_on_requires_absolute(monkeypatch):
    monkeypatch.setenv(PIN_ENV, "1")
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", "relative/dir")
    with pytest.raises(SpendDataDirNotPinned):
        spend_data_dir.pinned_data_dir()


def test_pin_on_resolved_ledger_inside_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(PIN_ENV, "1")
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    pinned = spend_data_dir.ensure_pinned_data_dir()
    assert pinned == tmp_path
    assert spend.ledger_json_path().parent == tmp_path
    assert spend.ledger_jsonl_path().parent == tmp_path


def test_guard_fails_closed_before_network_or_ledger(monkeypatch, tmp_path):
    """The guard must refuse before any auth call; no ledger file appears."""
    monkeypatch.setenv(PIN_ENV, "1")
    monkeypatch.delenv("CHIP_CHAT_DATA_DIR", raising=False)
    monkeypatch.delenv("HOME", raising=False)

    def _never(*args, **kwargs):  # a call would mean the guard let work run
        raise AssertionError("refresh_from_auth reached before the pin check")

    monkeypatch.setattr(spend, "refresh_from_auth", _never)
    monkeypatch.setattr(spend, "save_ledger", _never)
    with pytest.raises(SpendDataDirNotPinned):
        spend.guard_before_live_completion("room", "sk-or-v1-unit-test-key")
    assert (tmp_path / "spend.json").is_file() is False


def test_monthly_cutoff_shared_across_worktrees(monkeypatch, tmp_path):
    """Two worktree cwds + one pinned dir = one cutoff decision, one history."""
    shared = tmp_path / "pinned"
    shared.mkdir()
    monkeypatch.setenv(PIN_ENV, "1")
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(shared))

    cases = [
        (tmp_path / "worktreeA", 3.0),  # below the $5 cutoff: passes, writes
        (tmp_path / "worktreeB", 6.5),  # at/above the cutoff: refused
    ]
    for work, monthly in cases:
        work.mkdir()
        monkeypatch.chdir(work)
        assert spend_data_dir.ensure_pinned_data_dir() == shared
        assert spend.ledger_json_path() == shared / "spend.json"
        ledger = spend.default_ledger()
        ledger["monthly_usd"] = monthly
        if monthly >= 5.0:
            with pytest.raises(SpendCutoffError):
                spend.enforce_cutoff(monthly)
        else:
            spend.enforce_cutoff(monthly)
            spend.save_ledger(ledger)  # a sub-cutoff live call records into SHARED state
    # Worktree B (refused) never wrote; the shared ledger still holds A's write,
    # and A's earlier sub-cutoff entry is what a later worktree sees.
    final = json.loads((shared / "spend.json").read_text(encoding="utf-8"))
    assert final["monthly_usd"] == 3.0
