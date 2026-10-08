"""Monthly-USD display cents-only guard (operator format law, 2026-09-29)."""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

spend = importlib.import_module("chip.spend")


def test_warn_emit_is_cents_only(capsys):
    spend.emit_monthly_warn(5.123456)
    err = capsys.readouterr().err
    assert "monthly spend $5.12" in err
    assert "$5.1234" not in err


def test_standup_fact_is_cents_only(monkeypatch, tmp_path):
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "d"))
    # emit_monthly_warn writes its own standup fact at the cleaned format
    spend.emit_monthly_warn(5.123456)
    txt = Path(spend.standup_facts_path()).read_text()
    assert "monthly spend WARN $5.12 " in txt


def test_cutoff_message_is_cents_only():
    with pytest.raises(spend.SpendCutoffError) as exc:
        spend.enforce_cutoff(10.123456)
    assert "monthly $10.12" in str(exc.value)
    assert "10.123" not in str(exc.value)
