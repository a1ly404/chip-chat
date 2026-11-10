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

Not yet: fetching inputs from the live alert stream / Linear (callers pass files or dicts), and
any mutating or host-executor job from the picker.
