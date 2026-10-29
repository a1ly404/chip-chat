# Monthly budget modes (normal / thrift / stopped)

Chip Chat's OpenRouter key has a monthly **hard stop** (default **$5.00**). At
**80 %** of the cap (default **$4.00**) Chip drops into **thrift mode** for the
rest of the calendar month. Code: `scripts/chip/budget_mode.py`; ledger:
`$CHIP_CHAT_DATA_DIR/spend.json`.

| Mode | Monthly spend (defaults) | Behaviour |
| --- | --- | --- |
| `normal` | < $4.00 | Unchanged. |
| `thrift` | $4.00 to < $5.00 | Live calls go to the cheapest model (`CHIP_THRIFT_MODEL`, default `qwen/qwen3.7-flash`), `max_tokens` capped (`CHIP_THRIFT_MAX_TOKENS`, default 256), posted replies clipped (`CHIP_THRIFT_REPLY_CHARS`, default 600). Write/act paths refused: solver restarts, task execution (`execute=True`), monitoring fix actions, delegate hops, PM orchestrator hops, scoped-GO writes. Status and read-only Q&A still work. |
| `stopped` | >= $5.00 | **HARD STOP**: live model calls refused (`SpendCutoffError`). Mock mode still works (there is no free/local live fallback). Act paths stay refused. |

## Notices

The relay posts **one** `[chip-budget] THRIFT …` notice when it first sees
thrift mode and **one** `[chip-budget] HARD STOP …` notice at the cap. Each says
the spend and cap, what stopped, what still runs, and how to top up. They are
plain system lines (no LLM call) that mention the operator only when one is
configured: `CHIP_NOTICE_OPERATOR_MENTION` (e.g. `<@id>`), else the pack's
`operator_discord_id`, else no mention. They are sent through the existing
`CHIP_WEBHOOK_RELAY` webhook (#chip-relay), falling back to `CHIP_WEBHOOK_DEV`
when RELAY is unset. The "already posted" state
is kept in `spend.json` under `budget_notices` and cleared at the monthly
rollover. Checks run on every heartbeat tick and after each handled message.

## Month rollover

`spend.json` carries `month` (UTC `YYYY-MM`, the boundary OpenRouter uses for
`usage_monthly`). The first ledger load in a new month resets `monthly_usd`,
`estimated_monthly_usd` and `budget_notices`, keeps last month in
`previous_month`, and the mode drops back to `normal`. Ledgers without a
`month` are stamped from `verified_as_of`. When OpenRouter reports
`usage_monthly`, it is used even when it is 0, so lifetime `usage` can't trip the
gates right after the rollover.

## Status

* Relay health server: `GET /status` returns JSON
  `{"ready": …, "budget": {"mode", "month", "monthly_usd", "thrift_usd", "cap_usd", "thrift_model"}}`.
  `GET /health` is unchanged.
* `./bin/chip spend` prints `thresholds warn=$4.00 cutoff=$5.00` and
  `budget_mode=<mode> month=<YYYY-MM>`.
* Gateway startup logs `budget mode=<mode>`. Thrift and stopped no longer
  block startup; the per-call gates enforce the budget.

## Env vars (all optional; examples in `.env.example`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `CHIP_SPEND_CAP_USD` | `5.00` | Monthly HARD STOP in USD |
| `CHIP_SPEND_THRIFT_PCT` | `0.80` | Thrift threshold as a fraction of the cap |
| `CHIP_THRIFT_MODEL` | `qwen/qwen3.7-flash` | Model used in thrift mode |
| `CHIP_THRIFT_MAX_TOKENS` | `256` | Max completion tokens in thrift mode |
| `CHIP_THRIFT_REPLY_CHARS` | `600` | Max posted reply length in thrift mode |

## Availability actions at thrift and the cap

`act_block_reason(action, availability=True)` marks an allowlisted container
restart. Thrift keeps availability actions; the hard stop keeps them too unless
`CHIP_STOP_ALLOW_RESTART=0` (watch-only tail). Every write-class act (tasks, GO
writes, delegate/orchestrator hops) is still refused in both modes. Callers that
do not pass `availability` keep the old behaviour.
