"""Heartbeat bookkeeping. Two consecutive failures page once, then stop."""

from __future__ import annotations

from dataclasses import dataclass


HEARTBEAT_INTERVAL_S = 30 * 60
HEARTBEAT_MIN_SPEND_DELTA_USD = 0.01


@dataclass
class Watchdog:
    consecutive_failures: int = 0
    stopped: bool = False
    pages: int = 0

    def observe(self, ok: bool) -> str:
        if self.stopped:
            return "stopped"
        if ok:
            self.consecutive_failures = 0
            return "ok"
        self.consecutive_failures += 1
        if self.consecutive_failures >= 2:
            self.stopped = True
            self.pages += 1
            return "page_and_stop"
        return "fail"

    def would_page_on_next_failure(self) -> bool:
        return self.consecutive_failures >= 1 and not self.stopped


@dataclass
class HeartbeatGate:
    """In-process spend baseline for throttled Discord heartbeats."""

    baseline_monthly_usd: float
    last_post_at: float | None = None

    def note_post(self, monthly_usd: float, now: float) -> None:
        self.baseline_monthly_usd = monthly_usd
        self.last_post_at = now


def should_post_heartbeat(
    *,
    now: float,
    last_post_at: float | None,
    baseline_monthly_usd: float,
    current_monthly_usd: float,
    last_error: str,
    watchdog_consecutive_failures: int,
    watchdog_would_page: bool,
) -> bool:
    """Return True when a heartbeat line may be sent to Discord."""
    interval_ok = last_post_at is None or (now - last_post_at) >= HEARTBEAT_INTERVAL_S
    hard_bypass_interval = watchdog_consecutive_failures >= 1
    if not interval_ok and not hard_bypass_interval:
        return False

    if last_error.strip() or watchdog_consecutive_failures >= 1 or watchdog_would_page:
        return True

    delta = current_monthly_usd - baseline_monthly_usd
    return delta >= HEARTBEAT_MIN_SPEND_DELTA_USD


def heartbeat_line(
    *,
    last_message_age_s: int,
    success_rate: float,
    last_error: str,
    monthly_usd: float,
) -> str:
    return (
        f"heartbeat age_s={last_message_age_s} success_rate={success_rate:.2f} "
        f"last_error={last_error or '-'} monthly_usd={monthly_usd:.6f}"
    )
