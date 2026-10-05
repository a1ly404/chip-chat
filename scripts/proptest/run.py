"""Swarm entry point for the property-test harness.

Run from ``scripts/`` (or with ``scripts`` on PYTHONPATH):

    python3 -m proptest.run                      # 500 examples per property
    python3 -m proptest.run --examples 5000      # deep sweep
    python3 -m proptest.run --examples 50 --seed 42

Prints one machine-readable JSON summary to stdout so an agent swarm can
parse the verdict without reading human logs. On failure, Hypothesis prints
the SHRUNK minimal history to stderr; feed it back as a regression test
(see proptest/README.md for the workflow).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="proptest.run",
        description="Property-test the chip-relay skill gates against a reference model.",
    )
    parser.add_argument(
        "--examples",
        type=int,
        default=int(os.environ.get("CHIP_PROTEST_EXAMPLES", "500")),
        help="examples per property (default 500; CI pytest profile uses 50)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="reproduce a specific run (deterministic for the installed versions)",
    )
    parser.add_argument(
        "--properties",
        nargs="*",
        default=None,
        help="subset of property names to run (default: all)",
    )
    args = parser.parse_args(argv)

    os.environ.setdefault("CHIP_CHAT_MOCK", "0")
    from hypothesis import HealthCheck, given, seed, settings

    from proptest import generators, harness

    def build(strategy, check):
        def check_with_root(case):
            check(case, root)

        base = check_with_root
        if args.seed is not None:
            base = seed(args.seed)(base)
        return settings(
            max_examples=args.examples,
            deadline=None,
            database=None,  # no .hypothesis dir; swarms run fresh sweeps
            suppress_health_check=list(HealthCheck),
        )(given(strategy)(base))

    # name -> (strategy, check_fn). The check fns are the same ones the
    # pytest suite uses, so a swarm-found failure reproduces under pytest.
    all_properties = {
        "pipeline_matches_reference": (generators.scenarios, harness.check_scenario),
        "duplicate_storm_posts_once": (generators.storm_scenarios, harness.check_scenario),
        "mock_never_reaches_live_channel": (
            generators.scenarios,
            harness.check_mock_boundary,
        ),
        "malformed_law_fails_closed": (
            generators.garbage_law_scenarios,
            harness.check_scenario,
        ),
        "malformed_alerts_never_execute": (
            generators.malformed_alert_lists,
            harness.check_malformed_alerts,
        ),
    }
    selected = args.properties or list(all_properties)
    unknown = [p for p in selected if p not in all_properties]
    if unknown:
        parser.error(f"unknown properties: {unknown}; known: {list(all_properties)}")

    root = Path(tempfile.mkdtemp(prefix="chip-proptest-"))
    summary = {
        "tool": "chip-proptest",
        "examples_per_property": args.examples,
        "seed": args.seed,
        "properties": {},
        "verdict": "PASS",
    }
    try:
        for name in selected:
            strategy, check = all_properties[name]
            case = build(strategy(), check)
            t0 = time.monotonic()
            try:
                case()
                summary["properties"][name] = {
                    "verdict": "PASS",
                    "examples": args.examples,
                    "seconds": round(time.monotonic() - t0, 2),
                }
            except Exception as exc:  # noqa: BLE001 — report, don't crash the sweep
                summary["properties"][name] = {
                    "verdict": "FAIL",
                    "examples": args.examples,
                    "seconds": round(time.monotonic() - t0, 2),
                    "error": str(exc).splitlines()[-1][:200] if str(exc) else exc.__class__.__name__,
                }
                summary["verdict"] = "FAIL"
                summary["_exception"] = exc
        summary["seconds"] = round(
            sum(p.get("seconds", 0) for p in summary["properties"].values()), 2
        )
        print(json.dumps(summary, indent=1, default=repr))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if "_exception" in summary:
        # Re-raise AFTER the JSON summary so Hypothesis's shrunk falsifying
        # history reaches the swarm's stderr alongside the machine verdict.
        raise summary["_exception"]
    return 0 if summary["verdict"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
