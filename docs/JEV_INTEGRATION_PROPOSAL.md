# JEV_INTEGRATION_PROPOSAL — v2 (Ally review gate; NOT implemented)

> Status: PROPOSAL v2 — rewritten after the Harden review (same PR #75, iterating).
> v1 was a mute one-shot scorer; the review correctly identified that Chip Chat is
> a ROOM — agents argue, narrow the question, and try again. v2 makes Jev a
> queue-orderer and conversational anchor, never a verdict.
>
> Unchanged verified ground (from v1, accepted in review): same
> `OPENROUTER_CC_API_KEY`; pinned slug `typesafe/jev-1.13` (alias never); mock
> fixtures offline; secret-KEY rejection; Jev never lives inside `authorize()`,
> `monitor.classify()`, or done-gate regex; fail-closed to no-wake/needs-repro/
> structural regex; `usage.cost` into the existing ledger; Jevons brake (calls
> only on already-actionable receipts, once per fingerprint).

## CORE CHANGE: Jev starts conversations; it never ends them

v1 used confidence as a VETO. v2: Jev only works the band the deterministic
rules already left open — and every live call emits a **room receipt** the next
persona can answer (the missing product requirement from the review).

## The room receipt (mandatory on every live call)

```
[jev:<call_site>] question=<id> band=<auto|room|needs-repro> conf=<0.xx>
state_sha=<sha256[0:12]> — no wake yet; pm may tighten the state and re-call once
```
- Posted to the room transcript (footer of the propose card / wake card), NOT a
  side log — the contest happens where the agents live.
- Next persona may reply with a TIGHTER state (one hop) → one re-call → stop.
  Both calls logged; the delta between them IS the machine-learning signal.

## Insertion point v2 forms

### v3 band law (Ally-morning review) — PER-CALL-SITE cuts, asymmetric-cost
- **Top band >0.95 propose-auto stays; no lowering before a false-wake count.**
  Asymmetry: a false auto-wake spends a speaking model on a receipt the room
  will reject; a false needs-repro spends ONE cheap revision receipt. One row
  cannot move 0.95 (row #1 alone is not the denominator).
- **Suspicion-only sites (done-gate): in-room posting extends down to ~0.60.**
  The room line is nearly free — burying a Done-lie under a revision hop is the
  v1 veto in band form. Structural regex stays authority; Jev raises the line.
  (Row #1's 0.75 = missed-defect class under the old 0.80 cut; the fix is the
  lower in-room floor, which also exposes the deterministic-lesson below.)
- **Band-2 bottom is provisional:** 0.80–0.85 = in-room ONLY when the receipt
  NAMES a specific missing script field (a persona can supply it); otherwise
  needs-repro. Tightening prose while counters stay equal re-prints ~0.75 —
  "the bottom of band 2 is band 3 with an extra persona turn attached."
- **Runbook choice confidence ≠ wake-anomaly score:** separate quantities, own
  cuts; sharing one number locks a coincidence.
- **Missing data this spec must gather before any lock:** score histogram of
  known-bad vs known-benign gray receipts; repeat-call jitter on identical
  state (if the same state moves ~0.10, a 0.15-wide band is noise — then
  ">0.95" reads as "top of the observed range", not a probability).

**1. Wake-eligibility — ORDER at gray, never veto (R8).**
- TRUE (machine-checkable) wakes; FALSE stays quiet; GRAY → Jev orders:
  bands as above with the in-room floor 0.80 on THIS site (cheap revision).
- Jev has no third verdict "no."

**2. Runbook classifier — separate quantity (own thresholds until its own data).**
- needs-repro ⇒ ONE revision hop; hop budget below.

**3. Signature classifier — UNKNOWN fingerprints only (unchanged).**

**4. Done-gate suspicion — room-surfaced; in-room floor ~0.60 (v3 lowered per
the asymmetry math + row-#1 lesson).**

## v3 revision-hop law (2 calls hard ceiling)

- **Success test:** score delta ≥0.05 on call 2 is stop-success ONLY when the
  tighter state added script-built fields ABSENT on call 1 (receipt id, counter,
  enum code). Same state hash + new score = **false convergence (persona led the
  witness)** — verdict stays whatever the field rule says.
- **Stop-fail:** call 2 stays under the site floor ⇒ case stays needs-repro;
  hop count alone cannot mean success; delta alone cannot drive the loop
  (0.79→0.86 wobble would thrash).
- **Quiet-stall instrumentation (the failure to fix):** if a revision receipt
  posts but no tighter state arrives within N=3 room turns → ledger row
  "revision posted, no tighter state in N turns" logged beside both call rows;
  escalated exactly once (tell-Ally-once ladder). Thrash is already bounded by
  the two-call cap; quiet-stall v3 is what we instrumented instead.

## v3 thrift-rung skips (where Jev has NO signal)

- **Deterministic verdict exists ⇒ stop at the rule rung.** "Done claimed ∧
  boxes_checked>0 ∧ receipts_bound=0" IS a counter — row #1 should have ended
  at the deterministic rung; sending it to Jev = paid delay on a counter
  question (Jev has no signal to add when you can name the counter).
- **Next action independent of the label ⇒ Jev skipped.** Unbound-Done fix =
  re-bind a receipt (regex already flagged it); rule → speaking persona direct.
- Jev earns calls on **unknown fingerprints** and **wake-gray** only (output
  chooses ordering / signature class). Persona remains the skip target from the
  rule rung when the rule's result is already what the room contests.
- **Row-#1 carcinised candor (ledger follows separately):** the review's
  counter-lesson classifies row #1 as a mis-insertion; its corrective rule
  candidate lives at the deterministic rung (done-gate counter), NOT in the
  Jev path. The room floor for suspicion sites remains a Jev-law widening, not
  a Jev-created rule.

## Untrusted-`state` hardening (review hole #1 — deeper than key names)

- The request builder sends **script-built fields only**: fingerprint, counters,
  enum anomaly codes, timestamps, ids, **plus the Phase-1 ledger columns**
  (`task_id`, `task_type`, `turn_of_task`, `usd_cost`/`cost_usd` dual-key,
  `memory_op_counts`) — carried straight from the thread rows so Jev receipts
  join the Phase-5 unit-economics data without a second formatter (ledger-fix:
  this spec predates #86's columns; the build inherits them, not vice versa).
  **Free text is DROPPED by default.** When a bounded excerpt is genuinely
  needed (signature classifier), it is fenced
  (`"excerpt_escaped": "<≤200 chars, control chars stripped>"`) and carries the
  untrusted-data marker from harvest law — the Decisions API reading instruction-
  shaped text cannot act on it any more than `monitor.classify` can.
- Secret-KEY rejection stays (v1 check), but it is the LAST line, not the defense.

## Room-receipt compatibility (Phase-2 contract merge — collision 2 answer)

- `[jev:<call_site>]` lines land as **thread rows in the Phase-1/2 shape**
  (task-less rows keep the old shape when no task binding exists; task-bound
  rows carry the ledger columns) with `content` prefixed `[jev:<call_site>] ...`
  and a row-level `kind: "jev"` marker — NOT parallel free-form text. The
  transcript remains the single row schema (claims scanning + done gate +
  dedup read it); MemPalace rows stay in their own store
  (`tasks/<room>/<agent>.jsonl`) and are untouched by Jev.
- Consequence: no competing format; Jev receipts are first-class thread rows.

## Cursor-WIP dependency (collision 3 answer — per-site runnability)

| call site | runnable on current main (0fbe941)? | dependency |
| --- | --- | --- |
| 4 done-gate suspicion | YES — `chip.done_gate` regex + room row contract exist on main | none |
| 2 runbook classifier | YES — registry + MONITOR_CASES exist on main | none (revision hop needs room personas live, which is Phase 2 land) |
| 1 wake-eligibility graying | YES in principle — the feed's actionable pipeline exists on main | richer gray framing may arrive with RAZ-330; not a blocker |
| 3 signature classifier | DEGRADED until Cursor's RAZ-330 collectors + alert surface land | text-source ingestion (#78) exists; unknown-fingerprints mostly EMERGE from that stream |

- All four sites are runnable OFFLINE via mock fixtures regardless (CI $0.00);
  offline-first means no part of this proposal silently assumes unlanded surfaces.

## Thrift-rung ORDER v3 (supersedes the v2 bullet above)

- Ladder: script → GHA → **deterministic rule** → **Jev (gray only)** →
  speaking persona → expensive wake — **with two classes that SKIP Jev entirely:**
  1. **Deterministic verdict exists** ("Done ∧ boxes_checked>0 ∧ receipts_bound=0"
     is a counter — row #1 should have ended at this rung; sending it was a paid
     delay on a counter question; if you can NAME the counter, Jev adds nothing).
  2. **Next action independent of the label** (unbound-Done fix = re-bind a
     receipt the regex already flagged): rule → speaking persona directly.
- Jev earns calls at **unknown fingerprints** and **wake-gray** only (its output
  chooses ordering / signature class). Persona stays the skip target from the
  rule rung when the rule's result is already what the room contests.

## Eval law (review: "measuring agreement measures nothing")

- **The disagreement set is the only evidence.** Baseline-correct cases prove
  nothing. Before any numeric threshold is treated as real, publish:
  1. disagreement-set size (deterministic baseline said gray/needs-repro)
  2. false-wake count (Jev band >0.95 but room rejected)
  3. false-needs-repro count (Jev BELOW the site's in-room floor but the room's
     tightened state classified at/above it — floors are per-site per v3)
- Plus the two v3 datasets: known-bad/benign score histogram, and repeat-call
  jitter on identical state (band width is only as trustworthy as that jitter).
- The labeled replay set comes from REAL receipts (done-gate QA style), excluded
  from training eyes; published as a ledger table on RAZ-16.
- Three-band form adopted as the working hypothesis; single-cut rejected.

## Golden-17 candidate: evidence-triggered (v3 gate, not time/first-wake)

- **Eligibility to DRAFT** after the first false wake — but adoption requires ALL
  of: (1) disagreement-set ledger has order-of-ten gray rows (a denominator —
  one row cannot set law); (2) the false wake was on the **gray path**
  (deterministic rules were inconclusive — if a counter should have caught it,
  the fix is a new RULE, and writing Golden-17 would canonize the wrong layer);
  (3) the room rejection is recorded as a ledger row (band >0.95, room
  rejected), not a vibe.
- Candidate text is already the fail-closed spec wording; promoting to golden
  law = claiming confidence thresholds are trustworthy enough to bind — which
  wants a false-wake rate that does not exist yet.

## Single pipe enforced
- `jev-1.13-free` / OpenCode Zen / any second surface: **rejected** (v2 per review: a second pipe breaks the single-key story). One pipe: OpenRouter only.
</think><tool_call>edit<arg_key>newString</arg_key><arg_value>- `jev-1.13-free` / OpenCode Zen / any second surface: **rejected** (v2 per review: a second pipe breaks the single-key story). One pipe: OpenRouter only.

## What v2 keeps from v1 verbatim (the accepted base)
- Same OpenRouter key, no new vendor (§1 table) · pinned slug (§1) · mock
  fixtures offline, CI $0.00 (§6) · secret-key rejection (§5, now last-line) ·
  Jev never inside `authorize()`/`monitor.classify()`/done-gate regex (§3) ·
  fail-closed fallback (§3) · `usage.cost` ledger metering (§4) · Jevons brake
  (§4: calls only when a receipt is already actionable, once per fingerprint).

## Rev-2 build plan (still gated: your GO + Grok consult standing)
1. `chip/jev_router.py` — request builder (script-built fields; excerpt fencing;
   redaction sweep), room-receipt formatter, band constants block, fail-closed
   fallback, mock fixture (CI $0.00; live only behind GO + env).
2. Threshold calibration = disagreement-set collection on real receipts BEFORE any
   threshold constants are promoted (ledger on RAZ-16).
3. Insertion order: done-gate suspicion (flag→room) → signature classifier
   (unknown-fingerprints only) → wake-eligibility graying → runbook revision hop.

## Ally verification checklist (v2 — how each claim is checked without me)

1. **Single-key**: still exactly one `OPENROUTER_CC_API_KEY` (no TypeSafe/Zen cred);
   OpenRouter docs declare "any valid OpenRouter API key" is sufficient for Jev.
2. **Pin**: `grep -r "jev-latest" scripts/` → zero hits forever.
3. **Gates untouched**: `grep -rn jev scripts/chip/authorize.py scripts/chip/monitor.py
   scripts/chip/done_gate.py` → zero (Jev called BY callers, never inside gates).
4. **Room-receipt law**: every live call site shows a transcript receipt line
   (see the room-receipt section) — verified by PR diff inspection.
5. **Spend**: `usage.cost` rows land in the existing ledger; CI/mock $0.00.
6. **Eval guarantees**: placeholder thresholds live in ONE constants block with
   "NOT LOCKED — disagreement-set numbers required first" proven by test.

## Open questions retained (all answered in v2)
1. Thresholds → three-band working hypothesis adopted; numbers unlocked until the
   disagreement-set data exists.
2. 17th golden line → NOT adopted until one real false wake is observed
   (golden-13 corollary binds its own proposer).
3. `jev-1.13-free` / second surface → rejected (single-key story).
4. Thrift ladder rung inserted as gray-only (deterministic → Jev → persona → wake).

## Suite confirmation at rebased HEAD (Lyla's 04:00 ask)
- Rebased onto origin/main `0fbe941` (Phase-2 circuit + #86 ledger columns).
- Full suite from THIS branch tree: **384 passed, 1 skipped** (matches the ~384
  expectation; CI file list re-pinned through #86/#87 chains).
- Collision answers summary: (1) ledger rebased-in — state serializer predated
  #86's columns, spec now carries them; (2) format merged into the Phase-2 row
  contract (`kind: "jev"`, `content` prefix), MemPalace store untouched;
  verdict = NO structural competition; (3) sites 4/2/1 runnable on main, site 3
  degraded until RAZ-330 lands; all four runnable OFFLINE via mock fixtures.
