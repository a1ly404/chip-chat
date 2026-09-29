"""Collab protocol tests."""

from __future__ import annotations

import pytest

from chip import collab


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_sealed_submission_after_reveal_refused(data) -> None:
    collab.begin_brainstorm("dev", "goal", ["a", "b"])
    sealed = collab.submit_hypothesis("dev", "b", "idea")
    assert sealed["entries"][-1]["sealed"] is True
    collab.close_brainstorm("dev")
    with pytest.raises(PermissionError, match="reveal"):
        collab.submit_hypothesis("dev", "a", "late")
