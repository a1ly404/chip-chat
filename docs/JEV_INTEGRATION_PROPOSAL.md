# JEV_INTEGRATION_PROPOSAL — v3.1 (Ally review gate; NOT implemented)

> Status: v3.1 — the morning-cut review found v3 self-contradictory: the thrift
> section said Jev runs on unknown fingerprints / wake-gray ONLY and that row #1
> dies at the counter, while the band section used row #1's 0.75 to LOWER the
> done-gate in-room floor, and the build plan still turned done-gate first.
> **A mis-inserted row got promoted into a threshold.** v3.1 corrects all three
> surfaces at once (bands, activation order, build plan) so the spec no longer
> argues with itself.

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

### v3.1 band law — PER-SITE cuts, NO branch-at-live (jitter gated)

- **First-live-window law: LOG the score, DO NOT branch on it.** Bands stay in
  one constants block as UNLABELED hypotheses until same-state re-score jitter
  and the known-bad/benign histogram exist: if identical state moves ~±0.10,
  the 0.15-wide in-room band AND the 0.05 success delta are inside the noise,
  and ">0.95" reads as a rank label, not a probability. Opening with live bands
  confounds the ledger — every row becomes "what an unmeasured cut did," and
  bad-threshold vs bad-model is indistinguishable afterwards. Measuring the
  jitter IS the first window's purpose.
- **Top band >0.95 propose-auto survives as a HYPOTHESIS only** — no lowering
  before a false-wake count (asymmetry: false auto-wake spends a speaking
  model; false needs-repro spends ONE cheap revision receipt). One row cannot
  move 0.95 (row #1 alone is not the denominator).
- **Done-gate 0.60 floor WITHDRAWN.** It was fit against row #1, which the
  ladder excludes as counter-decidable — fitting a floor to a case Jev is
  forbidden to see, with no benign histogram (0.60 may sit mid-range of
  ordinary gray traffic, and every in-room line below the true floor is a room
  turn; combined with the quiet-stall N=3 pager, a low floor is Ally-spend on
  thin fingerprints — the expensive wake in disguise). Rubber-stamp path named
  for the record: suspicion posts as a normal thread row the done-gate and
  claims scanner already trust → cheap iteration manufactures the appearance
  of a bound receipt → a Done-lie launders THROUGH the sensor.
- **Done-gate auto-band: ABSENT.** Auto-raise is a veto; auto-clear is a pass;
  both end the conversation. This site's only legal output is the logged score
  (plus a room line ONLY after the counter rung has declined the receipt —
  counter-decidable rows never reach Jev).
- **Runbook choice confidence ≠ wake-anomaly score:** separate quantities, own
  cuts; sharing one number locks a coincidence.
- **Missing data before any lock:** known-bad vs benign histogram; repeat-call
  jitter (0.15-wide bands only as trustworthy as that jitter).

**1. Wake-eligibility — ORDER at gray, never veto (R8).** First-live-window:
score logged, NO band action (v3.1 band law). TRUE wakes; FALSE quiet; stays.

**2. Runbook classifier — separate quantity (own thresholds until its own data).**
needs-repro ⇒ ONE revision hop; hop budget below.

**3. Signature classifier — UNKNOWN fingerprints only (unchanged).**

**4. Done-gate suspicion — LOGGER ONLY for the first window (v3.1: no in-room
floor, no auto band; posts nothing until the counter rung has already declined
the receipt; the ~0.60 floor is withdrawn and must not return without the
benign histogram).**

## v3 revision-hop law (2 calls hard ceiling) — SUCCESS TEST v3.1

- **v3.1 strengthening: the delta test is insufficient.** Any new field changes
  the state hash; a marginally relevant field plus a ≥0.05 jump scores as
  "success" without doing real work. Replaced by a stronger predicate that
  costs **no extra Jev call**:
  1. the revision receipt (call 1's fallback) must NAMED the missing field id;
  2. call 2's state must add THAT id — and **no other new keys**;
  3. the new value must change a counter the deterministic rung already
     understands (receipt count, bound id, enum);
  4. **the rule rung must CHANGE ITS OWN RESULT.** If the rule still says the
     same thing, the score move is rejected even though the hash changed and
     the delta cleared 0.05 — witness-leading via relevance-gaming.
- Delta ≥0.05 remains a NECESSARY signal but is not sufficient; and when the
  jitter study shows ~±0.10 on identical state, **the 0.05 delta test is
  dropped entirely** (a noise-sized delta cannot be a success test).
- Same-state-hash + higher score remains rejected outright (witness-led, v3).
- **Stop-fail:** call 2 below the site's deterministic-path verdict ⇒ case
  stays needs-repro; hop count alone cannot mean success; delta alone cannot
  drive the loop (0.79→0.86 wobble would thrash).
- **Quiet-stall instrumentation:** revision posted, no tighter state within
  N=3 room turns → ledger row beside both call rows; escalated exactly once
  (tell-Ally-once). Thrash is bounded by the cap; quiet-stall is what v3.1
  instruments.

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
- **Laundering guard (v3.1, mandatory)**: presence/type of ANY subsequent row
  (jev receipts, persona chatter, contest lines) never counts as receipt
  binding for the done-gate or claims scanner — binding happens ONLY by the
  structural rule (a receipt line bound to a box passes the regex fields it
  requires). Chatter volume cannot warm a box toward "bound." The row shape is
  shared; the SEMANTICS of binding are unchanged by `kind:"jev"` rows existing.

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

## Golden-17 candidate: FIVE-condition gate (v3.1, evidence-triggered)

Eligibility to DRAFT after the first false wake; **adoption requires ALL FIVE:**

1. **Ten DISTINCT fingerprints across more than one call site.** A family of
   ten similar rows, or counter-misses labeled gray because the counter was
   never written, do not satisfy the denominator. Rows the skip list should
   have caught are excluded.
2. **The false wake was on the gray path** — deterministic rules were
   inconclusive. If a named counter in the thrift list matches the receipt,
   the fix is a new RULE, and writing Golden-17 would canonize the wrong layer.
3. **The anti-incentive check was recorded BEFORE the Jev call** (not after the
   rejection): an independent determination that no named counter in the
   thrift list matches the receipt. "Counter should have caught it" judged by
   the proposer lets the proposer under-specify counters until the line is
   adoptable — laundering the gate itself.
4. **Jitter-immune: the false-wake state was RE-SCORED, and the second score
   stayed above the auto cut.** A 0.96 that re-scores at 0.86 on the same hash
   is jitter — a room rejection of noise satisfies none of the conditions.
5. **The rejection names the band and the state hash, and comes from a speaker
   who did not author the revision state** (values flowing from a rejected
   band's own author = self-validation = the R11-forbidden path).

Plus, per-site scope split (final): **suspicion rejections do NOT count as
false wakes.** A rejected suspicion-auto from the cheapest site manufactures
evidence for a law aimed at confidence gates in general. False wake means one
thing: a **wake-eligibility auto that a human or inject speaker rejected**.
Candidate text is already fail-closed wording; promoting it to golden law
before the five hold = a false-wake-rate claim without a false-wake rate.

## Single pipe enforced
- `jev-1.13-free` / OpenCode Zen / any second surface: **rejected** (v2 per review: a second pipe breaks the single-key story). One pipe: OpenRouter only.
</think><tool_call>edit<arg_key>newString</arg_key><arg_value>- `jev-1.13-free` / OpenCode Zen / any second surface: **rejected** (v2 per review: a second pipe breaks the single-key story). One pipe: OpenRouter only.

## What v2 keeps from v1 verbatim (the accepted base)
- Same OpenRouter key, no new vendor (§1 table) · pinned slug (§1) · mock
  fixtures offline, CI $0.00 (§6) · secret-key rejection (§5, now last-line) ·
  Jev never inside `authorize()`/`monitor.classify()`/done-gate regex (§3) ·
  fail-closed fallback (§3) · `usage.cost` ledger metering (§4) · Jevons brake
  (§4: calls only when a receipt is already actionable, once per fingerprint).

## Rev-2 build plan (v3.1: safe-first-activation, skip-list enforced in the caller)

1. `chip/jev_router.py` — request builder (script-built fields; excerpt fencing;
   redaction sweep; **literal free-text DROPPED**), room-receipt formatter, band
   constants block (UNLABELED hypotheses — never branch), fail-closed fallback,
   mock fixture (CI $0.00; live only behind GO + env).
2. **First activation pair (both score-log-only):**
   - **wake-gray scoring**: logs scores on deterministic-gray receipts; takes NO
     band action; the conservative default action each site already has
     (done-gate: structural finding only; wake-gray: stays quiet) runs as-is.
   - **done-gate suspicion LOGGER**: runs ONLY on receipts the counter rung has
     ALREADY declined as gray (counter-decidable rows skip Jev — enforced IN
     THE CALLER, not in the thrift text). Room lines: deferred until the benign
     histogram + jitter land (row #1's mis-insertion is the standing example of
     why no-floor-cheap-room-lines must not exist pre-measurement).
   - **Runbook + signature classifier: NOT activated.** Runbook can start a
     remediation hop (higher blast radius than a transcript line); signature
     is degraded until RAZ-330. Revisit after Phase 3 signal sources land.
3. Threshold calibration = disagreement-set collection on real receipts BEFORE
   any threshold constants are promoted (ledger on RAZ-16).

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
