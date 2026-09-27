"""Daily Done-discipline watchdog (cron-READY, cron-NOT-registered — Ally GO first).

Consumes the same Linear issues export shape as `chip done-gate-sweep`
(`{"id","status","statusType","description"}[]`), runs the sweep predicate, and:
- dry-run (default): prints the SITREP table to stdout. NEVER posts.
- `--post`: additionally posts to the webhook in CHIP_WEBHOOK_MONITORING (if
  that env is set). No env -> prints "post skipped" and exits like dry-run.

The export itself is produced by the Linear MCP side (agent Lane); this script
is intentionally offline so cron can run it without Linear credentials.

Fixture law (test pollution): fixture tickets use distinct synthetic IDs
(RAZ-94xx); never reuse real Linear IDs from live runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from chip.done_gate import sweep

MONITORING_WEBHOOK_ENV = "CHIP_WEBHOOK_MONITORING"


def build_table(findings: list[dict]) -> str:
    lines = [
        "done-gate watchdog SITREP — Done-discipline findings",
        "ticket | status | checked | stamps | severity | reason",
    ]
    if not findings:
        lines.append("(clean — no findings)")
    for f in findings:
        lines.append(
            f"{f['id']} | {f['status']} | {f['checked']} | {f['stamps']} "
            f"| {f['severity']} | {f['reason']}"
        )
    return "\n".join(lines)


def post_to_monitoring(text: str, *, environ: dict[str, str] | None = None) -> tuple[bool, str]:
    """Post the SITREP table to #monitoring-logs. Skipped without env (dry-run)."""
    env = environ if environ is not None else os.environ
    url = (env.get(MONITORING_WEBHOOK_ENV, "") or "").strip()
    if not url:
        return False, "post skipped: no monitoring webhook env"
    body = json.dumps({"content": text[:1900], "username": "chip-relay"}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "chip-relay"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        ok = 200 <= resp.status < 300
    return ok, ("posted" if ok else f"post failed http {resp.status}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chip-monitor-watchdog")
    parser.add_argument("--issues-file", required=True)
    parser.add_argument("--post", action="store_true",
                        help="post the table to CHIP_WEBHOOK_MONITORING if set (dry-run default)")
    args = parser.parse_args(argv)
    p = Path(args.issues_file).expanduser()
    if not p.is_file():
        print(f"ERROR: issues file not found: {p}", file=sys.stderr)
        return 1
    issues = json.loads(p.read_text(encoding="utf-8"))
    findings = sweep(issues if isinstance(issues, list) else [])
    table = build_table(findings)
    print(table)
    rc = 0 if not findings else 2
    if args.post:
        ok, note = post_to_monitoring(table)
        print(note)
        rc = rc or (0 if ok else 3)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
