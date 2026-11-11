# Background agent jobs (`chip bgjob`)

Parity item P1.4. A background job is a task prompt run as a **bounded turn loop** by an injected
*step* function, with every cap enforced by the engine before work starts or before each hop.
Module: `scripts/chip/bg_jobs.py`. Tests: `scripts/test_chip_bg_jobs.py`.

> Not to be confused with `chip job`, the PM job board (`scripts/chip/jobs.py`) that records
> goal/acceptance/stop slices for humans and personas.

Running is **off by default**: `chip bgjob run` and `chip routine run-due --via-jobs` refuse
(exit 3) unless `CHIP_JOBS=1`. The CLI only ever uses the offline mock step; it never calls a
model or a host. A real step (model turn, tool call) is supplied by the caller in code.

## Storage

`CHIP_CHAT_DATA_DIR/bg_jobs/<id>.json`, one record per job (atomic replace on write):

| field | meaning |
| --- | --- |
| `mode` | `read` (may run in parallel) or `mutate` (serialized) |
| `max_hops`, `max_spend_usd` | per-job ceilings fixed at enqueue |
| `status` | `queued` → `running` → `ok` / `failed`; or `cancelled` |
| `reason` | `hop cap`, `spend cap`, `step error: <Type>`, `interrupted`, `cancelled` |
| `hops` | receipts: `{hop, at, output, spend_usd[, error]}` per hop |
| `spend_usd` | total actual spend |
| `source` | who queued it (`cli`, `routine:<id>`, …) |

## Caps

| env | default | rule |
| --- | --- | --- |
| `CHIP_JOBS_MAX_HOPS` | 8 (hard max 50) | per-job ceiling; enqueue above it is refused; loop without `done` → `failed: hop cap` |
| `CHIP_JOBS_MAX_SPEND_USD` | 0.25 (hard max 5) | per-job ceiling; checked before each hop and after each hop (overshoot → `failed: spend cap`) |
| `CHIP_JOBS_DAILY_MAX` | 50 | jobs started per UTC day; extra jobs stay `queued` |
| `CHIP_JOBS_DAILY_SPEND_USD` | 1.00 | actual spend today **plus the full `max_spend_usd` of each running job** (reservation) plus the candidate's ceiling must fit |
| `CHIP_JOBS_READ_CONCURRENCY` | 3 | parallel `read` jobs |

`mutate` jobs run one at a time: a tick selects at most one, and none while another is running.
Deferred jobs are reported with a reason and left `queued` for the next tick. Bad env values fall
back to defaults; values are clamped to the hard ceilings.

## Step contract

```python
step(job: dict, hop_index: int, prior_hops: list[dict]) -> {"output": str, "spend_usd": float, "done": bool}
```

Exceptions fail only that job (`step error`), never the tick. `bg_jobs.recover(now)` marks jobs
left `running` by a dead process as `failed: interrupted`.

## Routines

`bg_jobs.routine_runner()` adapts `routines.tick` so each due routine is enqueued as a `read`
job (`source=routine:<id>`) and run under the same caps. A routine whose job is deferred by a cap
gets a `failed` history row and its job is cancelled. Note that the runner's tick also starts any
other startable queued jobs.

## Transcript (mock)

```console
$ chip bgjob enqueue --prompt "look around" --job-id c1 --mock
{"id": "c1", "status": "queued", "mode": "read", "max_hops": 8, ...}
$ chip bgjob run --mock
REFUSED: CHIP_JOBS is not enabled; refusing to run background jobs
$ CHIP_JOBS=1 chip bgjob run --mock
{"ran": [{"id": "c1", "status": "ok", "hops": [{"hop": 0, "output": "mock: hop 1/1 for job c1", ...}]}], "deferred": []}
$ chip bgjob caps --mock
{"enabled": false, "caps": {...}, "today": {"day": "...", "started": 1, "spent_usd": 0.0, "reserved_usd": 0.0}}
$ CHIP_ROUTINES=1 CHIP_JOBS=1 chip routine run-due --via-jobs --mock
```

## Real model-turn step (P1.4b)

Module: `scripts/chip/bg_model_step.py`. `make_model_step()` returns a step that runs one chat
completion per hop through `chip.runtime.complete`, so live hops get the existing monthly spend
guard (hard stop at `CHIP_SPEND_CAP_USD`, default $5), the thrift downgrade, and rows in the main
spend ledger. Each hop also appends a per-job row to `CHIP_CHAT_DATA_DIR/bg_jobs_spend.jsonl`
(`job_id`, `hop`, `model`, `cost_usd`, `cost_source`, `budget_mode`, `mock`).

| budget mode | read job | mutate job |
| --- | --- | --- |
| normal | runs | runs |
| thrift | runs (thrift model, short answers) | refused before the call (`step error: BudgetRefused`) |
| stopped | refused | refused |

Off by default: building the step needs `CHIP_JOBS_MODEL_STEP=1` (plus `CHIP_JOBS=1` to run).
Jobs are single-turn unless `params.single_turn` is false; multi-hop jobs finish when the reply
contains `[[done]]`, else the hop cap ends them. All existing per-job hop/spend and daily caps
still apply. Offline (`--mock` / `CHIP_CHAT_MOCK=1`) never calls OpenRouter and records $0.

## PM job picker (P1.4b)

Module: `scripts/chip/bg_picker.py`. Turns normalized alert / Linear inputs into **read-only**
background jobs so PM can queue investigation work on its own:

```json
{"kind": "alert", "id": "disk-01", "title": "Disk 95%", "severity": "critical", "detail": "..."}
{"kind": "linear", "id": "ABC-12", "title": "Flaky CI", "labels": ["chip-job"], "state": "Todo"}
```

Policy (`CHIP_CHAT_DATA_DIR/bg_picker_policy.json`, unknown keys ignored): `alert_severities`
(default critical, warning), `linear_labels` (default `chip-job`), `linear_closed_states`,
`daily_max` (`CHIP_PICKER_DAILY_MAX`, default 10, hard max 100), `job_max_spend_usd` and
`job_max_hops` (default 1), both clamped to the bg_jobs ceilings.

Fixed rules: picked jobs are always `mode=read`; one job per input per UTC day; no new job while
an earlier one for the same input is queued or running; input text is quoted as data in the
prompt. Enqueueing needs `CHIP_PICKER=1`; `--dry-run` works with it off and writes nothing.

```console
$ chip bgjob pick --input alerts.jsonl --dry-run --mock
$ CHIP_PICKER=1 chip bgjob pick --input alerts.jsonl --mock
$ CHIP_JOBS=1 CHIP_JOBS_MODEL_STEP=1 chip bgjob run --model-step --mock
```

Not yet: any mutating or host-executor job from the picker.

## PM tick: live inputs -> pick -> run (P1.4c)

`scripts/chip/pm_tick.py` is one pass of the PM loop, gated by `CHIP_PM_TICK=1` (default off) on top of
`CHIP_PICKER` and `CHIP_JOBS`:

1. `bg_jobs.recover` fails jobs left `running` by a dead process.
2. Budget mode: at `stopped` nothing is picked or run and no input is consumed. In `thrift` the
   model step still refuses mutate jobs (the picker only makes read jobs).
3. Inputs:
   * **Alert stream** (`AlertStream`): tails a JSONL file with a persisted byte cursor
     (`pm_tick_alert_cursor.json`), so each event is read once. Default path is the relay's
     `chip_state/monitoring_dispatch.jsonl` (rows with a nested `alert`), override with
     `CHIP_PM_ALERTS_PATH` or `--alerts`. Fields are mapped generically (`id|fingerprint|key`,
     `title|summary|message`, `severity|level|priority`). Partial trailing lines wait for the next
     tick; a truncated/rotated file restarts at 0; at most 500 lines per tick.
   * **Linear poll** (`linear_items`): asks an injected client for open issues with the first
     picker label (default `chip-job`). `CHIP_PM_LINEAR=1` + `CHIP_LINEAR_API_KEY` enable the
     built-in read-only GraphQL client. A failed poll is recorded and does not block alerts.
   * `--linear-input FILE`: extra picker inputs from a file (for an external poller).
4. `bg_picker.pick` (policy, dedupe, daily pick cap). The alert cursor advances only after this succeeds.
5. `bg_jobs.run_pending` under the job caps (daily jobs/spend, read concurrency, hop/spend). With
   `CHIP_JOBS` off the tick stops at `picked-only`.

Each tick appends a row to `pm_tick_history.jsonl`. `--dry-run` works with every flag off and writes
nothing (cursor included).

### Real alert shapes and failed routines (P1.4e)

Relay dispatch rows usually carry no severity (`{"key", "detail", "raw"}` plus a `gate`), which the
picker policy would otherwise skip. The tick now:

* drops rows the relay marked `gate=ignored` (or `ignored: true`); `cooldown` rows still count and the
  picker's per-day dedupe absorbs repeats;
* infers a severity when none is given, with ordered `[regex, severity]` rules (first match wins):
  resolved/recovered/healthy again → `info`; outage/leak/data loss/breach → `critical`;
  down/unhealthy/exited/crash/failed/error/OOM/timeout/degraded/stuck/not running → `warning`;
  no match → no severity (not picked). Override with `alert_severity_rules` in
  `bg_picker_policy.json` (a non-empty list replaces the defaults; bad regexes/severities are skipped).
  Inferred items carry `severity_source: "inferred"`;
* gives alerts with no id a stable `fp-<sha256>` fingerprint of their normalized text.

**Failed routines** (`RoutineFailures`): new `failed` rows in routine run history become `warning`
alerts `routine-failed:<routine id>` (detail = the run summary). The per-routine line cursor
(`pm_tick_routine_cursor.json`) commits only after the pick succeeds; on first use it starts at the
current end so old failures are not replayed. `pm-tick` routines are excluded so the PM never opens
jobs about its own loop. `chip bgjob tick` reads it by default; `--no-routine-failures` turns it off.

Container health or any other probe reaches the PM the same way: write alert rows (flat or relay
shape) to the alert JSONL. All picked jobs are still `read` only.

Scheduling: a routine with `kind=pm-tick` is dispatched to the PM tick by
`chip routine run-due --via-jobs` (other kinds still run as background jobs).

```console
$ chip bgjob tick --dry-run --mock
$ CHIP_PM_TICK=1 CHIP_PICKER=1 CHIP_JOBS=1 chip bgjob tick --mock
$ chip routine add pm --schedule every:15m --kind pm-tick
$ CHIP_ROUTINES=1 CHIP_PM_TICK=1 CHIP_PICKER=1 CHIP_JOBS=1 chip routine run-due --via-jobs
```

The routine path uses the mock step unless `--model-step` is given:
`chip routine run-due --via-jobs --model-step` (with `CHIP_JOBS_MODEL_STEP=1`) runs routine jobs and
pm-tick jobs with real model turns; `chip bgjob tick --model-step` does the same for a one-off tick.

## Retry cost (P1.4d)

When `runtime.complete` retries a reasoning-starved reply, the discarded first attempt is already in
the main spend ledger; its cost is now also returned on the payload (`runtime.RETRY_KEY`,
`runtime.retry_cost_usd(payload)`), and the model step adds it to the job's spend. Rows in
`bg_jobs_spend.jsonl` carry `retry_cost_usd`.

## Daily PM summary (P1.4d)

`chip bgjob summary [--date YYYY-MM-DD] [--json]` builds one local day (`CHIP_PM_SUMMARY_TZ`, IANA
name, default `UTC`) from the job records, `pm_tick_history.jsonl` and `bg_jobs_spend.jsonl`:

* **Jobs PM ran and why**: id, status/reason, source (`picker:alert:<id>`, `routine:<id>`, ...) and
  the quoted input title.
* **Changes**: pull-request links found in job outputs.
* **Refusals**: budget/cap refusals and picker policy skips by reason (dedupe skips left out).
* **Spend**: day total incl. retry cost, number of model hops.
* **Waiting on you**: queued `mutate` jobs, job output lines starting with `[[decision]]` (the model
  step's system prompt asks for this), and ticks skipped because the budget is stopped.

`--post` sends it to the incoming webhook in `CHIP_PM_SUMMARY_WEBHOOK` (JSON `{"content": ...}`, all
mentions disabled). If anything is waiting and `CHIP_PM_SUMMARY_PING_WEBHOOK` is set, a short
one-line-per-decision ping goes there too; quiet days send no ping. `CHIP_PM_SUMMARY_LINK` (optional,
`{date}` replaced) is appended to ping lines. Gated by `CHIP_PM_SUMMARY=1` (default off); a day posts
once unless `--force` (state in `pm_summary_sent.json`).

A routine with `kind=pm-summary` posts yesterday's summary (prompt `today` posts the current day) under
`chip routine run-due --via-jobs`:

```console
$ chip bgjob summary --mock
$ chip routine add daily-summary --schedule every:24h --kind pm-summary
$ CHIP_ROUTINES=1 CHIP_JOBS=1 CHIP_PM_SUMMARY=1 CHIP_PM_SUMMARY_WEBHOOK=... chip routine run-due --via-jobs
```

## MCP tools in jobs (P1.5 slice 2)

With `CHIP_MCP=1`, the model step offers allowlisted `auto`/`ask` MCP tools to jobs (opt out with
`params.mcp_tools: false`; `params.mcp_max_rounds` default 2, hard 5). Receipts are stored on each hop
record and pending `ask` approvals appear in the PM summary. See docs/MCP.md.

## Job policy

Which jobs may run, and which may run with sudo, is decided by the engine-native job policy
(PM as team lead, operator-confirmed rules, YOLO default off, hard denies): see [POLICY.md](POLICY.md).

With `CHIP_JOB_POLICY=1`, `run_pending` routes every mutate job through the policy before it starts
(D slice 2). The job's request lives in `params.policy_request`:

* allow: the job gets a fresh decision ticket.
* ask: the job stays queued and the operator confirm is posted.
* deny: the job fails with `policy deny: ...`.

A mutate job with `params.exec: true` runs its approved argv through `policy_exec` with time and spend
caps, a scrubbed env, sudo only when granted, and receipts bound to the decision. See POLICY.md "Executor".
