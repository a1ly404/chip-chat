# JEV_INTEGRATION_PROPOSAL — v4 (Ally morning review; NOT implemented)

> Status: v4. Iteration history: v1 = mute one-shot veto scorer (rejected: ends
> conversations). v2 = band-elected validator (rejected: still a judge, plus a
> scored proposal was silent). v3 = gray-only orderer with numeric cuts
> (rejected: per-site cuts were fit against a counter-decidable row and gave a
> ~0.60 floor + an in-room-band inside unmeasured jitter). v4 = **Jev never
> scores a veto; the deterministic registry is the only authority; Jev is the
> question router for the registry's gray residue** — with shadow bands for the
> day cuts become measurable.
>
> Ground verified: same `OPENROUTER_CC_API_KEY`; pinned `typesafe/jev-1.13`;
> `usage.cost` self-reporting; no new vendor. Mock fixtures offline; CI $0.00.

## 0. The only golden-able invariant (true now, no histogram required)

**A mute score never authorizes a wake or a Done.** Fallback-to-rules is already
how the gates are built, so the candidate line ALREADY TRUE in the spec is
narrower than the v3 sentence ("confidence under a cut ⇒ fallback"), which
would have written a model's self-report into standing law. If a golden line is
ever proposed, it is the narrow version — and only after the volume problem in
§6 clears.

## 1. Per-site action sets (not one global pair)

| site | legal auto? | room line? | numeric cut |
| --- | --- | --- | --- |
| wake-eligibility | may auto-wake (top band, shadowed) | yes (room band) | own, hypothesis |
| done-gate suspicion | **never** — no auto band (auto-raise = veto, auto-clear = pass; both END the conversation) | only AFTER the counter rung declines (score logged beside) | none during the log window |
| runbook choice | never auto | revision receipt starts the hop | own, unlocked |

Shape law, already standing without histograms: **no auto on suspicion; no
shared cut between a score and a choice; no branch until the §2 protocol ran.**
Do not invent a tighter or looser auto floor while the false-wake rate is an
empty cell — an empty rate means there is no quantity to place. The >0.95 auto
band is a defensible **policy guess** (do not spend a speaking model until the
score is in the extreme tail) — not a measurement, and not evidence that 0.95
means 95%.

Cost honesty (review): band 2 is the **middle price, not a free action** — an
in-room hit spends a persona turn, and the N=3 stall rule can page Ally.

## 2. Shadow bands + the measurement protocol

**Branch-at-live confounds the ledger**: a bad threshold and a bad model
produce identical rows forever. Log-then-branch has a hole too: if the auto
action never fires, a false wake is never observed. **Shadow bands close both:**
compute the band the hypothesis WOULD have chosen, store it on the ledger row,
and still take the conservative default (structural done-gate line only;
wake-gray stays quiet). A later independent label "should not have woken" is a
false wake with **zero speaking-model spend** — the calibration set.

**Minimum protocol before any live branch:**
1. **N=2** repeats on one state. Exact match ⇒ the model is deterministic on
   identical payloads: identical repeats are wasted, and "jitter" never shows
   up that way → go to null mutations.
2. If matches: rescore **null mutations** (key reorder, timestamp tick, same
   counters, ids) — **N=5 per state on ≥10 distinct fingerprints**. Spread on
   null mutations is the noise floor.
3. If N=2 mismatches: **N=5** identical-payload repeats on the same 10 receipts
   (true stochastic jitter, measured directly).
4. **No live branch while the noise floor ≥ half the narrowest intended band
   gap** (today 0.15 ⇒ ±0.10 kills every band).
5. **Wake-auto shadow law (Disposition 0, fixed):** v3's "shadowed until ≥1
   shadow-auto row has a label" REQUIRED a false wake to unlock — inverted.
   Amended law: **one reject must LENGTHEN the shadow; zero shadow-auto rows
   forever is the acceptable failure.** No minimum count turns auto on; the
   AUTO switch waits for independent blind reject-or-accept labels on the gray
   residue, and stays off while its denominator is thin. Done-gate never
   leaves the shadow: it has no auto to enable.

## 3. Where a mute scorer sits (v4: side ledger + missing-field router)

- **During the whole log window: the score lives in the side ledger** (never a
  room verdict line), so a persona never faces a number to contest.
- When a room line is finally permitted, it emits a **missing-field blank,
  not a confidence**:
  ```
  [jev:done-gate] missing=receipt_id state_sha=<12> — one tighter state may fill that id
  ```
  Schema = **choice of missing field** (frozen registry vocabulary) ∪ `unknown`.
- Why: a score cannot be contested; a named field can — the persona supplies
  it or says it is the wrong field, and a counter checks. One named id, one
  re-call, stop. No third call, no debate with a model.
- Net role: **question router for the deterministic rung** — still mute, still
  no prose, never a room speaker. "Calibrated priest" is out.

## 4. Revision-hop success predicate (registry-flip law)

Ordered, all four:
1. Call 1's receipt names **exactly one missing field id**, and that id is in
   the **frozen counter registry** (recorded pre-call; §5).
2. Call 2 adds that key and **no other new keys**.
3. The value is well-formed for that counter.
4. **Re-running the registry FLIPS the verdict** (gray/fail → pass, or pass →
   fail). If no flip, the revision FAILED — even if the hash changed and Jev
   moved 0.20 on the same state.
- Sleep (or dropout) fallback for score-delta: 0.05 stays a necessary signal,
  but never sufficient; **dropped entirely if null-mutation spread ≈ 0.05+**
  (a noise-sized delta cannot be a success test).
- **Noise-floor law (Disposition 3):** if the protocol determines the shifts
  are anywhere near the band gaps, the threshold-comparator role is a paid
  no-op — **shut Jev off** (`CHIP_JEV_LIVE=0` again; the side ledger continues
  recording facts for a future model only, never read for behavior).
- Real progress that no current counter can see = **a new counter in the
  registry** — certified by Ally, never by a score. That is a build diff, not a
  Jev success (see §5 jurisdiction rules).
- Loose-counter hazard answer: the registry used in step 4 is the entry
  **logged before call 1**; adding a counter between the calls invalidates the
  hop (it was not the frozen registry).
- **Missing-SET law (Disposition 2b):** the registry (not Jev) declares the
  missing SET before the call; call 2 may add every key in that set and none
  outside it — one re-call still. Empty missing-set → no revision hop starts
  at all. A post-flip second fact = a NEW room turn + a new pre-call log line,
  never a smuggled second key inside the same hop.

## 5. Counter registry — the only authority for "does a counter exist"

- **The registry is checked-in (code-owner review); the proposer never rules
  at review time.** The caller runs every entry BEFORE any Jev HTTP call.
- The ledger row records `counter_id=<hit|none>` **before** the call; `none`
  is the **only legal predecessor of a Jev request**.
- **CI fixture corpus:** row #1's shape is the first fixture — `Done ∧
  boxes>0 ∧ receipts=0` must FAIL the build if it reaches the Jev client.
- The corpus grows from leaks: each later counter-decidable receipt judged
  live becomes a fixture in the same PR that adds its counter, so the list
  never re-shrinks to whatever the proposer enumerated on adoption day.
- Jurisdiction is visible: it widens only via a registry diff (weakens/deletes)
  or a receipt matching no entry — both are review artifacts, never vibes.

## 6. Golden-17 path (re-scoped)

- The five-condition gate stays the path to a THRESHOLD law, but at current
  volume it may never close — which is the reason the candidate stays **spec
  text** rather than law.
- The v3 sentence is the WRONG promotion: "confidence under a cut falls back"
  writes the model's self-report into standing law. The narrow invariant in §0
  is already true and needs no fingerprints, no jitter study, no volume.
- If a golden line is later wanted, it is §0's sentence, proposed in a window,
  acked explicitly. The numeric cut never leaves the site spec while shadow
  labels are accumulating.

## 7. Verification checklist (v4 + the four added rows — run in the build PRs)

The four rows from Lyla's checklist migrate to the +three+ build PRs per
Disposition 4 (this iteration is the amendment to the v3 sequence):

| row | verifying PR |
| --- | --- |
| registry pre-call `counter_id=none` logging | Registry PR (chip/counter_registry.py + chip/jev_router.py's `precheck_state` ledger-writes; verified by mock fixtures) |
| row-#1 fixture failing Jev-client reach | Registry PR's `may_call_jev` guard (returns False for the row-#1 shape; asserted by test in the same PR) |
| shadow row shape with `band_hypothetical` | Client PR (jev_router writes conf + `band_hypothetical` from a mock scorer in CI; live behind GO) |
| missing-field blank shape | Room-line emitter PR (template exactness test; defers until §5 protocol + blind labels) |

(All four statuses are PASS-by-acceptance-criteria inside each build PR; the
spec stays unchanged for the contract's text.)

## 8. Build order (Disposition 4, supersedes all prior ones)

1. **Registry PR: chip/counter_registry.json + chip/counter_registry.py +
   may_call_jev(state), with row-#1's fixture pointed AT THAT FUNCTION (no
   client import); registry stays green on main; `Done ∧ boxes>0 ∧
   receipts=0` fails CI if may_call_jev is true.**
2. **Client PR: the network entry calls may_call_jev and refuses when false
   (spy test); shadow fields conf/band_hypothetical written HERE from a mock
   scorer in CI; live calls only behind Lyla GO; no room line, no branch.**
3. **Room-line emitter PR — only after the noise protocol AND the blind-label
   rule exist; until then nothing lawful to post.**
4. (Distance clause: Phase B/C cadence + strict flip remain Ally-gate.)

- Same OpenRouter key (single `OPENROUTER_CC_API_KEY`; no zen/free-tier surface).
- Slug pinned (zero `jev-latest` hits anywhere, forever).
- Jev never inside the gates (authorize/monitor/done_gate grep = zero forever).
- Registry = checked-in file; every Jev request is preceded by a logged
  `counter_id=none` row (spec-level test: mock the registry, assert refuse).
- Fixture corpus present with row #1's shape failing the Jev client reach.
- Shadow rows carry `band_hypothetical=<choice>`, `action=taken=conservative`.
- Room lines (post-log-window) = missing-field blanks only, never confidence.
- Spend: `usage.cost` rows into the ledger; CI/mock $0.00.

## 8. Build order (unchanged gate: Ally GO + Grok consult)

1. `chip/counter_registry.json` + fixture corpus (row #1 first).
2. `chip/jev_router.py` — registry-first caller, shadow-band state writer,
   request builder (script-built fields only; excerpt fencing; redaction sweep),
   room-receipt formatter (missing-field blanks), fail-closed fallback, mock
   fixture (CI $0.00; live behind GO + env).
3. Site 1 shadow scoring (wake-eligibility) — log-only, no branch.
4. Done-gate site: counter rung → gray residue → LOGGER ONLY (no room line
   until §5 protocol passes; no auto band ever).
5. Phase B/C cadence unchanged (cards on RAZ-16; strict flip at Phase B degree).
