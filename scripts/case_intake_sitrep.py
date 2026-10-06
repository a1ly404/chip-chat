#!/usr/bin/env python3
"""Overnight churn sitrep — coverage scoreboard + fixtures (RAZ-350)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from chip.config import REPO_ROOT, data_dir
from chip_relay.case_intake_coverage import GRADUATION_CONSECUTIVE_CLEAN, load, scoreboard_table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Case intake coverage sitrep")
    parser.add_argument("--write", type=Path, help="Write markdown to path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    state = load()
    fixtures = state.get("fixtures_captured") or []
    index_path = REPO_ROOT / "config" / "fixtures" / "case_intake_live" / "index.json"
    pr_anchors: list[str] = []
    if index_path.is_file():
        try:
            for row in json.loads(index_path.read_text(encoding="utf-8")):
                pr_anchors.append(f"- `{row.get('id')}` → `{row.get('path')}` ({row.get('outcome')})")
        except json.JSONDecodeError:
            pass

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    md = [
        f"# Case intake sitrep ({ts} UTC)",
        "",
        "## Coverage scoreboard",
        "",
        scoreboard_table(state),
        "",
        "## Failure taxonomy (this window)",
        "",
        "- **(a) failed closed** — parked / dead-letter / one-strike / pending; receipt logged",
        "- **(b) failed open** — misroute / hallucination / no receipt; hunt to zero",
        "",
        f"failed_closed={state.get('failed_closed', 0)} failed_open={state.get('failed_open', 0)} misroutes={state.get('misroutes', 0)}",
        "",
        "## Graduation",
        "",
        f"consecutive_clean={state.get('consecutive_clean', 0)} / {GRADUATION_CONSECUTIVE_CLEAN}",
        "",
        "## New fixtures (same-day captures)",
        "",
    ]
    if pr_anchors:
        md.extend(pr_anchors)
    else:
        md.append("_none yet_")
    md.extend(["", f"data_dir={data_dir()}", ""])

    text = "\n".join(md)
    if args.json:
        print(json.dumps({"sitrep": text, "state": state}, indent=2))
    else:
        print(text)
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
