"""Session-scoped OpenRouter spend tracker for the WI-335 ops A/B session.

Hard ceiling: $0.25 (session), warn at $0.20. Separates the ops-session cap
from the global monthly ledger managed by chip.spend (WARN $5 / refuse $10);
this module must never replace the global gates, only guard this one session.

Fail-closed: any tracker misuse, missing cost field, or garbage spend row
refuses rather than guessing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SESSION_ALERT_USD = 0.20
SESSION_REFUSE_USD = 0.25


class SpendRefused(Exception):
    """Raised when the next costed action would breach the session ceiling."""


@dataclass
class SpendRow:
    ts: str
    label: str
    arm: str
    session_usd: float
    cumulative_usd: float


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class SpendTracker:
    def __init__(self, ledger_path: Path, arm: str = "", cap: float = SESSION_REFUSE_USD,
                 alert: float = SESSION_ALERT_USD) -> None:
        self.ledger_path = Path(ledger_path)
        self.arm = arm
        self.cap = float(cap)
        self.alert = float(alert)
        self.cumulative_usd = 0.0
        self.alerted = False
        if self.ledger_path.exists():
            for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    self.cumulative_usd += float(row["session_usd"])
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                    raise SpendRefused(
                        f"fail-closed ledger row not parseable: {line[:80]!r}"
                    ) from exc

    def record_call(self, session_usd: float, label: str,
                    on_refuse: Callable[[], None] | None = None) -> SpendRow:
        try:
            cost = float(session_usd)
        except (TypeError, ValueError) as exc:
            raise SpendRefused(f"garbage session_usd {session_usd!r}") from exc
        if cost < 0:
            raise SpendRefused("negative spend row")
        if self.cumulative_usd + cost > self.cap:
            on_refuse = on_refuse or (lambda: None)
            on_refuse()
            raise SpendRefused(
                f"session cap {self.cap} would be exceeded: {self.cumulative_usd}+{cost}"
            )
        self.cumulative_usd += cost
        row = SpendRow(ts=_utcnow(), label=label, arm=self.arm,
                       session_usd=round(cost, 9),
                       cumulative_usd=round(self.cumulative_usd, 6))
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row.__dict__) + "\n")
        if self.cumulative_usd >= self.alert and not self.alerted:
            self.alerted = True
            print(f"SPEND ALERT: ops session at ${self.cumulative_usd:.4f} (>= ${self.alert})",
                  file=__import__("sys").stderr)
        return row

    def state(self) -> dict[str, float | bool]:
        return {
            "cumulative_usd": round(self.cumulative_usd, 6),
            "alert_usd": self.alert,
            "refuse_usd": self.cap,
            "alerted": self.alerted,
        }
