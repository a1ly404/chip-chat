# Post-A/B runbook (after live ops session)

**Audience:** pm lane + Lyla. **ocgo** owns firing and reading the live arms. pm drafts docs only; Lyla is hands-off until she rules mothball vs rerun.

**verified-as-of:** `2026-09-29T17:29:07Z`

Related (pre-fire / grading): [`RAZ-335-ops-ab-post-runbook.md`](RAZ-335-ops-ab-post-runbook.md). This file is the **after** checklist once ocgo's live A/B has run or aborted.

## What happened (executed — RAZ-335)

Facts below are the **actual** live ops A/B outcome (not only the predicted flow). Receipt cite: **Linear RAZ-335 / comment id `63b479a0`** (id as given by Lyla; this doc does not claim a re-read of Linear).

| Fact | Value |
| --- | --- |
| GO token | `f4k9-wq2x-t7bz` (only token cited; do not invent others) |
| Result | **Tie 4/4** both arms |
| Ally ruling | **Mothball** confirmed. **No re-grade.** |
| Session cap | **Disarmed.** Nothing else fires on RAZ-335. |
| Spend | **`$0.0062`** actual vs **`$0.25`** session cap |
| Receipt | RAZ-335 / `63b479a0` |

### Archive note (ledger)

- **Do not truncate** the live spend ledger under `CHIP_CHAT_DATA_DIR`. Archive is a **copy** into `docs/archive/` (UTC timestamp in the filename), never a rotate-in-place wipe of the live file.
- Record source path + `verified-as-of` next to the archive copy (PR body or archive sidecar).

### airvpn_port_grab.py — disposition (not voided)

- Script / tier-1 row **`airvpn-port-regab`** is **not** cancelled and **not** voided.
- Ownership **transferred to `cc` (chip-chat lane)**. Not buffrdev. Do **not** drop the row because the script file may still be absent on disk — absence ≠ cancellation.
- Cross-ref: `config/chip_tools.json` id `airvpn-port-regab`, argv `python3 scripts/airvpn_port_grab.py` (audience `global-only`, RAZ-335 tier-1). Readers must not conclude the tier-1 row is dead.

## Archive spend ledger (procedure)

- **Copy** the live session spend / arm jsonl into `docs/archive/` with a UTC timestamp in the filename (example: `docs/archive/ops-ab-spend-20260929T163631Z.jsonl`).
- **Do not truncate** the live ledger under `CHIP_CHAT_DATA_DIR`. Archive is a copy, not a rotate-in-place.
- Record the source path and `verified-as-of` next to the archive copy (PR body or archive sidecar note).

## Transcript ownership

| Role | Owns |
| --- | --- |
| **ocgo** | Reads `ops_raza335` solo / trio jsonl under the data dir (`threads/ops_raza335/solo.jsonl`, `trio.jsonl` or equivalent harness paths). Posts the score/verdict receipt when scoring is done. |
| **pm** | Does **not** mothball alone. May draft docs / draft PRs only. |
| **Lyla** | Decides **mothball vs rerun**. No ambient re-fire without her GO. |

## Mothball (chosen)

Ally confirmed mothball for this session. Consequences:

- **Do not** disable monthly spend gates (`$5` WARN / `$10` refuse) or lane denies.
- **Stop** calling `ops_session_a_b` live. No `--live` without a fresh named GO.
- Session cap for this RAZ-335 ops A/B is **disarmed**; **nothing else fires** on RAZ-335 from this A/B track.
- Protocol engine **stays dark** without GO (`CHIP_LANE_GO_TICKET` / ops GO receipt law unchanged).
- Collab code may remain deployed; ambient invocation stays dead.
- **No re-grade** of the 4/4 tie.

## Rerun (not chosen)

If Lyla later chooses rerun (separate from this mothball):

- Requires a **new** GO token (new receipt / new env gate as the harness already requires). Do not reuse `f4k9-wq2x-t7bz` as if it were still live.
- Same scenario as the prior arms unless Lyla **names a different** ground-truth scenario.
- Solo arm first, then trio, same session tracker rules as the pre-fire runbook.

## Abort (session spend)

- Session refuse threshold is **`$0.25`** (`SpendRefused` from the ops session tracker).
- This run stayed under cap (`$0.0062` vs `$0.25`); abort path did not fire.
- If an arm would pass that cap, **`SpendRefused` stops the arm**. Write the failure receipt; do not continue the other arm on the same tracker as if the cap reset.
- **Do not retry live in the same process** after `SpendRefused`. Exit, archive what you have, wait for Lyla.

## Out of scope here

- Inventing Discord webhooks or tokens.
- pm firing the ops session.
- Merging other PRs; Lyla authorized merge of **#125 only** after folding these results.
