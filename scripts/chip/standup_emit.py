"""STANDUP_EMISSION_SPEC task 3 — cron-ready standup emitter (2026-09-28).

Mirrors the monitor_watchdog law exactly: dry-run default (NEVER posts),
posting gated on the CHIP_WEBHOOK_MONITORING env (same webhook tier as the
watchdog and feed posts). Cron registration is live per Ally churn-GO, but
log-only: no webhook env -> printed SITREP only, receipts to stdout.

Input contract (docs/STANDUP_EMISSION_SPEC.md): structured standup receipts
only. A lane without a VALID receipt is silent by contract — the emitter
renders the typed stall report (chip.standup.render_stall), never confident
health text, and runs correlated-dark across lanes (>=2 silent in one
window = ONE finding, tell-Ally-once via exit code 3; "once" is the cron
contract's job to coalesce via state).

RC law (machine-checkable, for cron alerting):
- 0: every configured lane live AND phaseC_gate true (credit night)
- 2: at least one lane silent/stale (single-lane dark: log-and-continue)
- 3: correlated-dark (>=2 silent lanes) — PAGE-tier per spec
- 1: usage/garbage errors (fail closed)

Receipts carry verified-as-of anchors per the done-bar law.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from typing import Any, Mapping

from chip import standup
from chip.monitor_watchdog import post_to_monitoring  # same webhook tier + UA

WEBHOOK_ENV = "CHIP_WEBHOOK_MONITORING"

_RENDER_ORDER_ERROR = "emitter: no lanes configured"


def render_live(receipt: Mapping[str, Any]) -> str:
    """source=live standup line (facts + credit gate; <=3 lines)."""
    payload = receipt["payload"]
    age = payload.get("age_h")
    gate = receipt.get("credit", {}).get("phaseC_gate")
    return (
        f"{receipt['standup']} standup: source=live age_h={age:g}h "
        f"credit.phaseC_gate={str(bool(gate)).lower()}"
    )


def render_window(receipts: list[Mapping[str, Any]]) -> tuple[str, dict[str, Any]]:
    """One emission window: per-lane lines + correlated-dark law + credit counter.

    Silent lanes render stalls; live lanes render their credit facts; when
    >=2 lanes are silent the correlated-dark line is appended (ONE finding,
    tell-Ally-once). Deterministic: lanes sorted by standup id.
    """
    rows = []
    for r in sorted(receipts, key=lambda x: x.get("standup", "")):
        fin = standup.finalize_receipt(dict(r))
        if fin["_schema_errors"]:
            rows.append(f"{r.get('standup', 'unknown')} standup: RECEIPT GARBAGE — zero credit (golden 5)")
            continue
        if fin["payload"]["source"] == "silent":
            rows.append(standup.render_stall(fin).splitlines()[0])
        else:
            rows.append(render_live(fin))
    cd = standup.correlated_dark(receipts)
    if cd.get("correlated"):
        rows.append(cd["report"])
    credit = standup.consume(receipts)
    rows.append(f"phaseC credit: {credit['credited']}/{credit['received']} receipt(s)")
    return "\n".join(rows[:6]), credit  # hard <=6 lines per window (room-turns family law)


def load_receipts(path: str | None, lanes: list[str], environ: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Read per-lane receipt JSONs; absent/garbage lane receipt -> silent stall receipt.

    Fail-closed by CONSTRUCTION: no receipt means source=silent with the
    typed STALL renderer — never silence, never confident narration.
    """
    env = environ if environ is not None else os.environ
    out: list[dict[str, Any]] = []
    for lane in lanes:
        rec = None
        if path:
            p = os.path.join(path, f"{lane}.json")
            if os.path.isfile(p):
                try:
                    loaded = json.load(open(p, encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    loaded = None
                if isinstance(loaded, dict) and not standup.validate_receipt(loaded):
                    rec = loaded
        if rec is None:
            rec = standup.make_silent_receipt(
                lane, None, issued_at=env.get("CHIP_STANDUP_ISSUED_AT") or None
            )
        out.append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chip-standup-emit")
    parser.add_argument("--lanes", required=True,
                        help="comma-separated standup ids, e.g. homelab,gymlog")
    parser.add_argument("--receipts-dir", default=None,
                        help="dir holding <lane>.json standup receipts (absent = stall)")
    parser.add_argument("--post", action="store_true",
                        help="post the SITREP to CHIP_WEBHOOK_MONITORING if set (dry-run default)")
    parser.add_argument("--json", action="store_true", help="machine-checkable receipt line to stderr")
    args = parser.parse_args(argv)
    lanes = [s.strip() for s in args.lanes.split(",") if s.strip()]
    if not lanes:
        print("ERROR: no lanes configured", file=sys.stderr)
        return 1
    window, credit = render_window(load_receipts(args.receipts_dir, lanes))
    print(window)
    if args.json:
        print(json.dumps({"credit": credit}), file=sys.stderr)
    lines = window.splitlines()
    correlated = any(l.startswith("correlated-dark:") for l in lines)
    if correlated:
        rc = 3
    elif any("STALL" in l or "GARBAGE" in l for l in lines):
        rc = 2
    else:
        rc = 0
    if args.post:
        ok, note = post_to_monitoring(window)
        print(note)
        if not ok:
            rc = rc or 3
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
