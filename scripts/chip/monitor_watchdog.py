"""Daily Done-discipline watchdog (cron-READY, cron-NOT-registered — Ally GO first).

Consumes the same Linear issues export shape as `chip done-gate-sweep`
(`{"id","status","statusType","description"}[]`), runs the sweep predicate, and:
- dry-run (default): prints the SITREP table to stdout. NEVER posts.
- `--post`: additionally posts to the webhook in CHIP_WEBHOOK_MONITORING (if
  that env is set). No env -> prints "post skipped" and exits like dry-run.
- `--state-file`: persists a per-run findings map; a later run with the same
  state file emits ONLY the delta vs the previous run (new / resolved / changed
  / unchanged). P2: one glance, zero noise. Determinism: findings are already
  severity-then-id ordered by sweep(); no timestamps appear in table output.

The export itself is produced by the Linear MCP side (agent lane); this script
is intentionally offline so cron can run it without Linear credentials.

Fixture law (test pollution): fixture tickets use distinct synthetic IDs
(RAZ-94xx); never reuse real Linear IDs from live runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

from chip.done_gate import sweep

MONITORING_WEBHOOK_ENV = "CHIP_WEBHOOK_MONITORING"


def findings_state(findings: list[dict]) -> dict[str, dict]:
    """Deterministic per-ticket state map for delta computation."""
    return {
        f["id"]: {"severity": f["severity"], "checked": f["checked"], "stamps": f["stamps"]}
        for f in findings
    }


def state_hash(state: dict[str, dict]) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:16]


def diff_state(prev: dict[str, dict], current: dict[str, dict]) -> dict[str, list[str]]:
    """Compare per-ticket states → new / resolved / changed / unchanged id lists."""
    new_ids = [i for i in current if i not in prev]
    resolved_ids = [i for i in prev if i not in current]
    changed_ids = [i for i in current if i in prev and current[i] != prev[i]]
    unchanged = [i for i in current if i in prev and current[i] == prev[i]]
    return {"new": new_ids, "resolved": resolved_ids, "changed": changed_ids, "unchanged": unchanged}


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


def build_delta_table(findings: list[dict], prev_state: dict[str, dict]) -> str:
    """One-glance delta SITREP: only what changed since the previous run."""
    current = findings_state(findings)
    d = diff_state(prev_state, current)
    by_id = {f["id"]: f for f in findings}
    lines = ["done-gate watchdog DELTA SITREP"]
    if not d["new"] and not d["resolved"] and not d["changed"]:
        lines.append(f"(unchanged — {len(d['unchanged'])} finding(s) persist)")
        return "\n".join(lines)
    for ident in d["new"]:
        f = by_id[ident]
        lines.append(f"NEW [{f['severity']}] {ident} — {f['reason']}")
    for ident in d["resolved"]:
        lines.append(f"RESOLVED {ident} (no longer in findings)")
    for ident in d["changed"]:
        f = by_id[ident]
        p = prev_state[ident]
        lines.append(
            f"CHANGED [{f['severity']}] {ident} — "
            f"checked {p['checked']}→{f['checked']}, stamps {p['stamps']}→{f['stamps']}) "
            f"| {f['reason']}"
        )
    if d["unchanged"]:
        lines.append(f"(unchanged: {len(d['unchanged'])} — {', '.join(sorted(d['unchanged']))})")
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


def _load_state(p: Path) -> dict[str, dict]:
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def _save_state(p: Path, state: dict[str, dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, sort_keys=True, indent=1), encoding="utf-8")


def run_table(issues: list) -> str:
    return build_table(sweep(issues if isinstance(issues, list) else []))


def delta_report(issues: list, state_path: Path) -> tuple[str, int]:
    """Sweep + delta vs stored state; writes the new state. Returns (report, rc)."""
    findings = sweep(issues if isinstance(issues, list) else [])
    prev_state = _load_state(state_path)
    current = findings_state(findings)
    if not prev_state:
        report = build_table(findings)
    else:
        report = build_delta_table(findings, prev_state)
    _save_state(state_path, current)
    return report, (0 if not findings else 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chip-monitor-watchdog")
    parser.add_argument("--issues-file", required=True)
    parser.add_argument("--state-file", default=None,
                        help="persist per-run findings map here; later runs emit ONLY the delta")
    parser.add_argument("--post", action="store_true",
                        help="post the table to CHIP_WEBHOOK_MONITORING if set (dry-run default)")
    args = parser.parse_args(argv)
    p = Path(args.issues_file).expanduser()
    if not p.is_file():
        print(f"ERROR: issues file not found: {p}", file=sys.stderr)
        return 1
    try:
        issues = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: bad issues JSON: {exc}", file=sys.stderr)
        return 1

    findings = sweep(issues if isinstance(issues, list) else [])
    table = build_table(findings)
    print(table)
    rc = 0 if not findings else 2

    if args.state_file:
        state_path = Path(args.state_file).expanduser()
        prev_state = _load_state(state_path)
        current = findings_state(findings)
        if prev_state:
            print()
            print(build_delta_table(findings, prev_state))
        _save_state(state_path, current)

    if args.post:
        ok, note = post_to_monitoring(table)
        print(note)
        rc = rc or (0 if ok else 3)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
