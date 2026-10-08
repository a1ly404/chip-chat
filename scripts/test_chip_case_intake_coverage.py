"""Coverage scoreboard tests."""


import json

from chip_relay import case_intake_coverage as cov
from chip_relay.case_intake_coverage import (
    GRADUATION_CONSECUTIVE_CLEAN,
    OPEN_FAIL_KILL_THRESHOLD,
    class_killed,
    load,
    record_outcome,
    scoreboard_table,
    sync_killed_classes_from_fixtures,
)


def test_graduation_streak_breaks_on_open_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cov, "data_dir", lambda: tmp_path)
    (tmp_path / "case_intake").mkdir(parents=True, exist_ok=True)
    record_outcome("llm_parsed_clean")
    record_outcome("failed_closed", failure_kind="closed")
    record_outcome("llm_parsed_clean")
    state = load()
    assert int(state["consecutive_llm_clean"]) == 2
    record_outcome("failed_open", failure_kind="open")
    state = load()
    assert int(state["consecutive_llm_clean"]) == 0


def test_adapter_does_not_increment_llm_graduation(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cov, "data_dir", lambda: tmp_path)
    (tmp_path / "case_intake").mkdir(parents=True, exist_ok=True)
    record_outcome("adapter_handled")
    state = load()
    assert int(state["adapter_handled"]) == 1
    assert int(state["consecutive_llm_clean"]) == 0


def test_consecutive_llm_clean_persists_on_disk(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cov, "data_dir", lambda: tmp_path)
    (tmp_path / "case_intake").mkdir(parents=True, exist_ok=True)
    record_outcome("llm_parsed_clean")
    state2 = load()
    assert int(state2["consecutive_llm_clean"]) == 1


def test_scoreboard_table_has_graduation_bar() -> None:
    assert str(GRADUATION_CONSECUTIVE_CLEAN) in scoreboard_table(load())


def test_failed_open_resets_at_195(monkeypatch, tmp_path) -> None:
    """Full reset semantics (slice 3): one failed_open zeroes streak."""
    monkeypatch.setattr(cov, "data_dir", lambda: tmp_path)
    (tmp_path / "case_intake").mkdir(parents=True, exist_ok=True)
    for _ in range(195):
        record_outcome("llm_parsed_clean")
    assert int(load()["consecutive_llm_clean"]) == 195
    record_outcome("failed_open", failure_kind="open")
    assert int(load()["consecutive_llm_clean"]) == 0


def test_class_kill_at_three_open_failures(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cov, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(cov, "REPO_ROOT", tmp_path)
    (tmp_path / "case_intake").mkdir(parents=True, exist_ok=True)
    case_class = "container-status-change"
    for _ in range(OPEN_FAIL_KILL_THRESHOLD):
        record_outcome("failed_open", failure_kind="open", case_class=case_class)
    assert case_class in load()["killed_classes"]
    assert class_killed(case_class)


def test_fixture_unblocks_killed_class(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cov, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(cov, "REPO_ROOT", tmp_path)
    fx_dir = tmp_path / "config" / "fixtures" / "case_intake_live"
    fx_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "case_intake").mkdir(parents=True, exist_ok=True)

    case_class = "container-status-change"
    for _ in range(3):
        record_outcome("failed_open", failure_kind="open", case_class=case_class)
    assert class_killed(case_class)

    fixture_id = "container-status-change-synthetic-worker-down--f0000001"
    rel = f"config/fixtures/case_intake_live/{fixture_id}.json"
    body = {
        "id": fixture_id,
        "raw_input": "Container checkout-worker went down — source=SyntheticStatusBot",
        "outcome": "pending_clarify",
        "expected": {
            "route": "adapter",
            "outcome": "adapter_handled",
            "fingerprint_class": case_class,
            "webhook_id": "10000000000000001234",
        },
    }
    (tmp_path / rel).write_text(json.dumps(body) + "\n", encoding="utf-8")
    (fx_dir / "index.json").write_text(
        json.dumps(
            [
                {
                    "id": fixture_id,
                    "path": rel,
                    "outcome": "adapter_handled",
                    "fingerprint_class": case_class,
                    "version": 1,
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    sync_killed_classes_from_fixtures()
    assert not class_killed(case_class)
    assert case_class not in load()["killed_classes"]
