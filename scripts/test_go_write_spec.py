"""Spec stub guards for GO-token write path (no production wiring)."""

import pytest

from chip import go_write, runbooks
from chip.go_scope import propose_go_marker
from datetime import datetime, timezone


def test_go_write_flag_is_off() -> None:
    assert go_write.GO_WRITE_WIRED is False


def test_go_write_entrypoints_refuse() -> None:
    with pytest.raises(go_write.GoWriteNotWiredError):
        go_write.format_propose_receipt(
            runbook_id="compose-recreate",
            fingerprint="wg0-missing",
            lane="razzorstack",
            ticket="homelab-wg0",
            evidence="tool:wg-show.missing-wg0",
            exp_hint="2099-01-01T00:00:00Z",
        )
    with pytest.raises(go_write.GoWriteNotWiredError):
        go_write.try_apply_scoped_go("GO: x fp=x lane=chip exp=2099-01-01T00:00:00Z", persona="homelabdev", room="dev")


def test_blocked_runbooks_stay_denied_regardless_of_go() -> None:
    for rid in ("remux", "plex-restart", "credential-flip", "media-delete"):
        assert runbooks.runbook_class(rid) == "blocked"
        assert runbooks.may_autorun(rid) is False


def test_scoped_go_parser_ready_for_impl() -> None:
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    missing, why = propose_go_marker("GO: ticket only", now=now)
    assert missing is None and "fp=" in why
    ok, _ = propose_go_marker(
        "GO: homelab fp=wg0-missing lane=razzorstack exp=2099-01-01T00:00:00Z runbook=compose-recreate",
        now=now,
    )
    assert ok == {"fp": "wg0-missing", "lane": "razzorstack", "exp": "2099-01-01T00:00:00Z"}
