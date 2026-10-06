"""Coverage scoreboard tests."""

from __future__ import annotations

from chip_relay import case_intake_coverage as cov
from chip_relay.case_intake_coverage import GRADUATION_CONSECUTIVE_CLEAN, load, record_outcome, scoreboard_table


def test_graduation_streak_breaks_on_open_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cov, "data_dir", lambda: tmp_path)
    (tmp_path / "case_intake").mkdir(parents=True, exist_ok=True)
    record_outcome("parsed_clean")
    record_outcome("failed_closed", failure_kind="closed")
    record_outcome("parsed_clean")
    state = load()
    assert int(state["consecutive_clean"]) == 2
    record_outcome("failed_open", failure_kind="open")
    state = load()
    assert int(state["consecutive_clean"]) == 0


def test_scoreboard_table_has_graduation_bar() -> None:
    assert str(GRADUATION_CONSECUTIVE_CLEAN) in scoreboard_table(load())
