# Parked findings (cross-lane receipts)

Durable receipts when Chip Chat / OCgo discovers work that belongs in another lane (usually **razzorstack** homelab). Discord/Slack threads are not SoT — this file + Linear are.

## How to park

1. Comment on the **relevant Linear ticket** with `lane: razzorstack` (or `lane: ops`) and what is waiting on whom.
2. Add one row to the table below (newest first).

## Open parked items

| Date (UTC) | Linear | Summary | Target lane | Owner / next step |
|------------|--------|---------|-------------|-------------------|
| 2026-09-28 | — (RAZ-329 parent; rationale in comments 1b5b8d6c/6cb25352) | monitoring-api (razzorstack) stale-image fix: ONE stale binding of 25 containers — recreate package + verification receipts | razzorstack (Windows SoT) | Cursor/homelab-dev: `docs/parked/monitoring-api-fix-plan.md` (one-shot `docker compose up -d --force-recreate --no-deps monitoring-api`); unblocks Card 2's HARD blocked-until + Phase B clock |
| 2026-09-28 | RAZ-330 | Slice B skill bodies staged 5/5 + move MANIFEST (`.cursor/skills/*.md`, pinned SHA at move) | razzorstack (Windows SoT) | Cursor: copy `docs/skill-bodies/*.body.md` per `docs/skill-bodies/MANIFEST.md`, SHA-pin, RAZ-16 receipt on move (RAZ-237: OCgo is no-writer; Lyla 2026-09-28 no lane-waiver) |
| _example_ | RAZ-xxx | _one line_ | razzorstack | Cursor homelab on Windows SoT |

## Resolved (archive)

Move rows here when the other lane merged or closed the work.

| Date closed | Linear | Resolution |
|-------------|--------|------------|
| | | |
