# Routines (scheduled jobs with run history)

Module: `scripts/chip/routines.py` · Tests: `scripts/test_chip_routines.py` · Flag: `CHIP_ROUTINES` (default off)

Routines are stored under `$CHIP_CHAT_DATA_DIR/routines/<id>.json`, each with an append-only
`<id>.history.jsonl` run ledger (kept when the routine is deleted).

## Schedules (UTC)

- `every:<N><s|m|h|d>` — fixed interval, minimum 60 s (e.g. `every:15m`)
- `cron:<min> <hour> <dom> <month> <dow>` — 5-field cron in UTC; `*`, `*/n`, `a-b`, `a-b/n`, lists.
  `dow` 0–6 with 0 (or 7) = Sunday; if both `dom` and `dow` are restricted, either matching fires.

All math is UTC with an injected clock, so DST changes never shift or double a run. Missed runs are
caught up **at most once**; the history row records how many slots were skipped (`missed_skipped`).

## Run history row

`routine_id, kind, scheduled_for, started_at, ended_at, status (ok|failed), summary, spend_usd, missed_skipped`

Runner exceptions or unknown statuses are recorded as `failed`; they never crash the tick.

## CLI

```
chip routine add <id> --schedule 'cron:0 15 * * *' --kind summary [--prompt ...]
chip routine list | enable <id> | disable <id> | delete <id>
chip routine history <id> [--limit N]
CHIP_ROUTINES=1 chip routine run-due     # exit 3 (REFUSED) when the flag is off
```

`run-due` currently dispatches through the offline mock runner only. Real dispatch (agent jobs with
hop/spend caps) is backlog item P1.4; call `routines.tick(now, runner)` with your own runner meanwhile.
Run `run-due` from any external scheduler (cron, a service, a container loop) every minute.
