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
