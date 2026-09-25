"""On-demand one-turn smoke. Refuses to spend when monthly_usd is at the WARN gate.

Live Discord is not invoked unless CHIP_RELAY_SELFTEST=1 and the Infisical env
vars are present. This module's budget check is what CI and local runs share.
"""

from __future__ import annotations

import os
import sys

from chip.spend import WARN_MONTHLY_USD


def budget_ok(monthly_usd: float) -> bool:
    return monthly_usd < WARN_MONTHLY_USD


def main(argv: list[str] | None = None) -> int:
    del argv
    if os.environ.get("CHIP_RELAY_SELFTEST", "").strip().lower() not in ("1", "true", "yes"):
        print("ERROR: set CHIP_RELAY_SELFTEST=1 to run the one-turn smoke", file=sys.stderr)
        return 1
    from chip import spend

    ledger = spend.load_ledger()
    monthly = float(ledger.get("monthly_usd") or 0.0)
    if not budget_ok(monthly):
        print(
            f"ERROR: monthly_usd={monthly} >= WARN {WARN_MONTHLY_USD}; selftest refused",
            file=sys.stderr,
        )
        return 2
    required = (
        "DISCORD_CHIP_RELAY_TOKEN",
        "DISCORD_CHIP_RELAY_WEBHOOK_DEV",
        "OPENROUTER_CC_API_KEY",
    )
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        print("ERROR: selftest missing " + ", ".join(missing), file=sys.stderr)
        return 3
    print(
        "ERROR: live Discord smoke is not wired in this skeleton; "
        f"budget ok monthly_usd={monthly}",
        file=sys.stderr,
    )
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
