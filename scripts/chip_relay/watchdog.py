"""Heartbeat bookkeeping. Two consecutive failures page once, then stop."""

from __future__ import annotations

from dataclasses import dataclass


HEARTBEAT_INTERVAL_S = 15 * 60


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
