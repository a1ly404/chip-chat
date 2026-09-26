"""Discord heartbeat spend gate and interval (no live Discord)."""

from __future__ import annotations

from chip_relay.watchdog import (
    HEARTBEAT_INTERVAL_S,
    HEARTBEAT_MIN_SPEND_DELTA_USD,
    HeartbeatGate,
    Watchdog,
    should_post_heartbeat,
)


def test_heartbeat_interval_is_thirty_minutes() -> None:
    assert HEARTBEAT_INTERVAL_S == 30 * 60
    assert HEARTBEAT_MIN_SPEND_DELTA_USD == 0.01


def test_no_post_when_spend_delta_below_threshold() -> None:
    now = 1_000_000.0
    assert not should_post_heartbeat(
        now=now,
        last_post_at=None,
        baseline_monthly_usd=1.0,
        current_monthly_usd=1.009,
        last_error="",
        watchdog_consecutive_failures=0,
        watchdog_would_page=False,
    )


def test_post_when_spend_delta_at_threshold() -> None:
    now = 1_000_000.0
    assert should_post_heartbeat(
        now=now,
        last_post_at=None,
        baseline_monthly_usd=1.0,
        current_monthly_usd=1.01,
        last_error="",
        watchdog_consecutive_failures=0,
        watchdog_would_page=False,
    )


def test_interval_blocks_spend_qualified_post() -> None:
    now = 2_000_000.0
    last = now - HEARTBEAT_INTERVAL_S + 60
    assert not should_post_heartbeat(
        now=now,
        last_post_at=last,
        baseline_monthly_usd=1.0,
        current_monthly_usd=1.05,
        last_error="",
        watchdog_consecutive_failures=0,
        watchdog_would_page=False,
    )
    assert should_post_heartbeat(
        now=now,
        last_post_at=now - HEARTBEAT_INTERVAL_S,
        baseline_monthly_usd=1.0,
        current_monthly_usd=1.05,
        last_error="",
        watchdog_consecutive_failures=0,
        watchdog_would_page=False,
    )


def test_last_error_bypasses_spend_gate_not_interval() -> None:
    now = 3_000_000.0
    assert should_post_heartbeat(
        now=now,
        last_post_at=None,
        baseline_monthly_usd=2.0,
        current_monthly_usd=2.0,
        last_error="empty chip reply",
        watchdog_consecutive_failures=0,
        watchdog_would_page=False,
    )
    last = now - 60
    assert not should_post_heartbeat(
        now=now,
        last_post_at=last,
        baseline_monthly_usd=2.0,
        current_monthly_usd=2.0,
        last_error="empty chip reply",
        watchdog_consecutive_failures=0,
        watchdog_would_page=False,
    )


def test_failed_heartbeat_bypasses_interval_for_outage_visibility() -> None:
    now = 4_000_000.0
    last = now - 60
    assert should_post_heartbeat(
        now=now,
        last_post_at=last,
        baseline_monthly_usd=0.5,
        current_monthly_usd=0.5,
        last_error="",
        watchdog_consecutive_failures=1,
        watchdog_would_page=True,
    )


def test_heartbeat_gate_resets_baseline_on_post() -> None:
    gate = HeartbeatGate(baseline_monthly_usd=1.0)
    gate.note_post(1.25, 100.0)
    assert gate.baseline_monthly_usd == 1.25
    assert gate.last_post_at == 100.0


def test_watchdog_would_page_before_second_failure() -> None:
    dog = Watchdog()
    assert dog.would_page_on_next_failure() is False
    assert dog.observe(False) == "fail"
    assert dog.would_page_on_next_failure() is True
