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

**1. Wake-eligibility — ORDER, never veto (R8 preserved).**
- Machine-checkable TRUE set wakes (unchanged); FALSE set stays quiet (unchanged).
- The GRAY set (rules inconclusive) goes to Jev for ORDERING only: `score` "How
  strongly does this receipt represent a machine-checkable anomaly?" with the
  score bands below. **Jev has no third verdict called "no."**
- Bands: `>0.95 propose-auto · 0.80–0.95 propose-in-room (a persona picks it up
  and may tighten) · <0.80 needs-repro`. **Numeric cuts are NOT locked until the
  disagreement-set numbers exist (see §eval).** Thresholds live in one constants
  block, Ally-tunable, never inside gates.

**2. Runbook classifier — needs-repro is a START of a hop, not a stop.**
- Run 1 (deterministic-path fitness): if `choice.confidence < 0.80` → the chosen
  fallback posts a structured revision receipt — which option lost, confidence,
  exactly what a tighter state would change.
- **One revision hop:** one persona answers with a tighter state (script-built
  fields only), one re-call, then STOP (both calls logged in the room receipt).
- The repro is produced IN THE ROOM; the classifier cannot stall a fix forever.

**3. Signature classification — Jev earns its keep only on UNKNOWN fingerprints.**
- Known MONITOR_CASES classes never hit Jev (dedup + verify-twice already run on
  whatever it labels; labelers in front of trusted rules are paid noise).
- New/unknown fingerprint → `choice` over signature classes ∪ `unknown` → the
  room receipt proposes the label; a persona may contest by supplying counters.

**4. Done-gate suspicion — kept, surfaced in the room.**
- Structural regex stays authority; Jev raises suspicion on receipt↔box mismatch.
- The suspicion line posts to the room transcript (not a side log) so a persona
  or Ally can contest it with a better-bound receipt; contesting re-binds
  structurally, never by Jev's say-so.

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

## Thrift-rung amendment (review: "Jev after gray, never as a paid delay")

- Ladder v2: script → GHA → **deterministic rule** → **Jev (gray only)** →
  speaking persona → expensive wake.
- Jev runs when the deterministic check returns **gray**; a speaking persona runs
  only if Jev's receipt asks for a revision or a human wake. If a regex or
  counter can answer, Jev is a paid delay — the rung text asserts that.

## Eval law (review: "measuring agreement measures nothing")

- **The disagreement set is the only evidence.** Baseline-correct cases prove
  nothing. Before any numeric threshold is treated as real, publish:
  1. disagreement-set size (deterministic baseline said gray/needs-repro)
  2. false-wake count (Jev band >0.95 but room rejected)
  3. false-needs-repro count (Jev <0.80 but room's tightened state classified ≥0.80)
- The labeled replay set comes from REAL receipts (done-gate QA style), excluded
  from training eyes; published as a ledger table on RAZ-16.
- Three-band form adopted as the working hypothesis; single-cut rejected.

## Golden-17 candidate: NOT adopted
- Candidate line ("calibrated-decision gates: confidence < threshold ⇒
  deterministic fallback") stays a candidate. Adopting it from this proposal
  before seeing **one real false wake** = the silent backfill the phase note
  forbids (golden 13 corollary applies to its own proposer).

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
