# DONE_GATE_QA — gate run against live Linear tickets (2026-09-27 night)

Read-only scan (Linear MCP fetches; NO state writes by chip lane). Patterns counted
per issue description text; verdicts = chip.done_gate predicate (RAZ-182/217
machinery, PR #58 `3829026` + #60 CLI `0779e33`).

| ticket | status | boxes | verified-as-of stamps | gate verdict (if state claims Done) |
| --- | --- | --- | --- | --- |
| RAZ-177 | In Progress | 0 | 0 | n/a now — but flipping Done with no AC boxes = premature (the incident class is literally its history) |
| RAZ-181 | Duplicate | 6 | **0** | not a Done claim — but 6 checked boxes with zero receipts would be REFUSED if revived as Done |
| **RAZ-199** | **Done** (completedAt 2026-09-11) | 4 | **0** | **REFUSE → reopen required** — the exact premature-Done case RAZ-217 was built on, still standing with zero receipts |
| RAZ-206 | Canceled | 0 | 0 | n/a (canceled ≠ Done claim) |
| RAZ-207 | Canceled | 0 | 0 | n/a |

## Evidence anchors (machine-checkable)
- Count queries: `/- \[[ x]\]/gi` and `/verified-as-of/gi` over `get_issue().description` (Linear MCP), scan date 2026-09-27 night.
- Module predicate: `chip.done_gate.reopen_required(md)` — unit-proven in `scripts/test_chip_done_gate.py` (bare/relative stamps rejected: R1).
- Status facts: `get_issue().status/statusType/completedAt` as shown above.

## Live flag (tell-Ally-once)
- **RAZ-199 flap:** ticket Done with no machine-checkable receipts. Receipt comment posted on the ticket
  proposing reopen per RAZ-217 watchdog semantics — no state flipped by chip lane.

## Forward note for Phase B / tickets discipline
- Everything checked in a Done claim needs a `verified-as-of: <stamp> — <proof>` bound line;
  stamps allow only `YYYY-MM-DD HH:MM` shape (bare "verified"/relative time not accepted).
