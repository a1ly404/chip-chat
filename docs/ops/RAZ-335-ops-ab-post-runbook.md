# Post-A/B runbook — RAZ-335 ops session validation (SPEC-ONLY; verified-as-of 2026-09-29T15:3xZ)

This doc is rendered OFF the receipted ruling chain (churn window, Lyla hands-off). It has NO executor wiring by design: guard-test-clean, no wiring, no chip_tools placeholders — spec only until the A/B fires.

## 0. Preconditions (what must be true BEFORE the A/B fires)

| gate | check |
|---|---|
| protocol slices merged | #108-#114 into main (pm/Cursor) |
| named GO | Lyla posts `GO RAZ-335-ops-ab <token>` (any channel she uses; the runner accepts only `--go-receipt <file>` first-line form) |
| env double gate | `CHIP_OPS_AB_GO=1` in the shell that fires the runner |
| collab module present | `chip.collab` importable on the merged main (runner fail-closed; refuses with `chip.collab unavailable…`) |
| spend | session tracker must show ≥ headroom for the arm you fire; monthly gates $5/$10 must be unfired |

## 1. Firing order

1. `python3 -m chip.ops_session_a_b --arm solo --live --go-receipt <path> --json` — baseline FIRST (per ruling).
2. `python3 -m chip.ops_session_a_b --arm trio --live --go-receipt <path> --json` — same session, same tracker (cumulative across arms).
3. Optional validation pass WITHOUT spend: `--arm both` + `--allow-mock-mutation` (no live calls; board mutation in mock sandbox only). This is the ONLY placebo run permitted pre-GO.

## 2. Transcripts — where they land and who owns them

- `~/appdata/chip-chat/threads/ops_raza335/solo.jsonl` and `trio.jsonl` — per-rule timestamped rows (prompt/reply/critique/decision/board_op).
- `spend.jsonl` — same file, arm-tagged rows. Never truncate (defend with `protect transcript per ruling = exam data`; no unlink anywhere in this module).
- The board files (`boards/ab_solo.json`, `ab_trio.json` under data dir) are the PRIMARY evidence; the transcripts mirror them.
- The transcripts stay on disk regardless of outcome (including error exits) — a clean refusal is itself a receipt row.

## 3. Grading

- `--score <path>` imports no model and costs nothing: rubric = ground-truth-class discovery (2) + falsifiable probe named (1) + evidence citation (1); max 4 per arm.
- The grader compares `decision`/`converge` rows only (not short prompt rows).
- "Ground-truth class" = external reachability from outside the tunnel vs tunnel-internal lease probe; see `docs/ops/RAZ-335-t1-port-health-202609291445.md`.

## 4. Verdict posting

After scoring, post to `#chip-dev` per ruling, ONE injected receipt:
- `A/B verdict: trio_wins|trio_loses|tie` + per-arm rubric scores + transcripts' paths
- spend ledger summary (session cumulative; every call's ts/model/tokens/USD)
- the exact GO token (not secrets) + verified-as-of ts
- copy the same body to the RAZ-335 thread (durable copy). DO NOT post to #chip-dev only.

## 5. Mothball procedure (if trio loses or ties)

Per ruling: "fails the verdict → mothball the collab stack lane-wide."
1. Bottom line: verdict posted with reasoning (see rubric) — NOT silent.
2. Collab engine STAYS DEPLOYED but the ambient/ambient trio invocation surfaces stay dead (already dark by law); the room board protocol remains for RAZ-335-only manual use, if at all.
3. The harness itself stays as a smoke tool (it is the fixed capability the next re-test resumes from).
4. Re-run condition: any of (a) scrubbed protocol holes fix landed in a later round; (b) a NEW ground-truth scenario worth grading (a different probe-source case); (c) Lyla re-opens with a new GO token. No re-fire without a fresh named GO.

## 6. Failure/abort semantics (per the old ruling, carried)

| event | action |
|---|---|
| `SpendRefused` mid-arm | stop the arm immediately; write failure receipt (spend state + last ts); the OTHER arm stays spend-blocked too (same cap tracker) |
| `chip.collab` import error pre-slice | rc=1 refusal, logged; the session is aborted, no spend |
| ré-merge with different GO token | refuse all retrisions (token mismatch counts as a refusal event) |
| probe-source aspect regresses on merge | post the regression delta in #chip-dev with receipts before re-firing |

## 7. LUMOCHECK-DOWN substitution (all scheduling/timers are receipted evidence)

Firing-time evidence (live probe bodies, gluetun API output, log lines from container + receipts) live in `docs/ops/RAZ-335-t1-port-health-202609291445.md`. Substitute evidence rules hold until LUMOCHECK returns: machine-readable values inline, verified-as-of timestamps, no fabricated field names.
