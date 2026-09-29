# Post-A/B runbook (after live ops session)

**Audience:** pm lane + Lyla. **ocgo** owns firing and reading the live arms. pm drafts docs only; Lyla is hands-off until she rules mothball vs rerun.

**verified-as-of:** `2026-09-29T16:36:31Z`

Related (pre-fire / grading): [`RAZ-335-ops-ab-post-runbook.md`](RAZ-335-ops-ab-post-runbook.md). This file is the **after** checklist once ocgo's live A/B has run or aborted.

## Archive spend ledger

- **Copy** the live session spend / arm jsonl into `docs/archive/` with a UTC timestamp in the filename (example: `docs/archive/ops-ab-spend-20260929T163631Z.jsonl`).
- **Do not truncate** the live ledger under `CHIP_CHAT_DATA_DIR`. Archive is a copy, not a rotate-in-place.
- Record the source path and `verified-as-of` next to the archive copy (PR body or archive sidecar note).

## Transcript ownership

| Role | Owns |
| --- | --- |
| **ocgo** | Reads `ops_raza335` solo / trio jsonl under the data dir (`threads/ops_raza335/solo.jsonl`, `trio.jsonl` or equivalent harness paths). Posts the score/verdict receipt when scoring is done. |
| **pm** | Does **not** mothball alone. May draft docs / draft PRs only. |
| **Lyla** | Decides **mothball vs rerun**. No ambient re-fire without her GO. |

## Mothball

If Lyla chooses mothball:

- **Do not** disable monthly spend gates (`$5` WARN / `$10` refuse) or lane denies.
- **Stop** calling `ops_session_a_b` live. No `--live` without a fresh named GO.
- Protocol engine **stays dark** without GO (`CHIP_LANE_GO_TICKET` / ops GO receipt law unchanged).
- Collab code may remain deployed; ambient invocation stays dead.

## Rerun

If Lyla chooses rerun:

- Requires a **new** GO token of the form `RAZ-335-ops-ab` (new receipt / new env gate as the harness already requires).
- Same scenario as the prior arms unless Lyla **names a different** ground-truth scenario.
- Solo arm first, then trio, same session tracker rules as the pre-fire runbook.

## Abort (session spend)

- Session refuse threshold is **`$0.25`** (`SpendRefused` from the ops session tracker).
- If an arm would pass that cap, **`SpendRefused` stops the arm**. Write the failure receipt; do not continue the other arm on the same tracker as if the cap reset.
- **Do not retry live in the same process** after `SpendRefused`. Exit, archive what you have, wait for Lyla.

## Out of scope here

- Inventing Discord webhooks or tokens.
- pm firing the ops session.
- Merging draft PRs without Lyla / Ally GO.
