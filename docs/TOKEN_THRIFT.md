# TOKEN_THRIFT — Chip Chat's token discipline (ported from Grok Bot habits)

Audience: every agent that drives `chip` / `chip-relay`. Binding per
`docs/OPERATING_LAW.md` rule 2 and the thrift ladder in AGENTS.md §7.
Mechanization: `scripts/chip/thrift.py` (gates) — this doc is the why + the habit list.

## The ladder (cheapest sufficient tool first)

1. **Script it** — grep, `git log`, local pytest, `chip tool run` dry-run. Zero model tokens.
2. **CI** — `python3 -m pytest scripts/…` on the PR; GitHub Actions runs `ci.yml` on every PR.
3. **Cheap agent / mock** — `CHIP_CHAT_MOCK=1 ./bin/chip …` — deterministic, $0.
4. **Live OpenRouter** — only with an Ally GO or a bounded acceptance envelope,
   and only after `./bin/chip spend` shows headroom.

## Gates the runtime enforces (thrift.py)

| Gate | Behavior |
| --- | --- |
| `classify_message` | Routes a message into a cost class before any call. |
| `check_budget` | Refuses class escalation beyond the window budget. |
| `check_hops` | Delegate chains cap at 3 hops per chain (`MAX_DELEGATE_HOPS`); a human/inject turn resets. |
| `check_thrift_skip` / `gate_tool_class` | Tool execution must match the task's tool class; `--execute` / `CHIP_TOOL_AUTO` stays opt-in. |
| `check_open_ended_dig` | Refuses open-ended work ("until green", "find root cause") without a written stop condition (law rule 15). |

## Ledger habits (spend.py — disk-persistent)

- Ledger lives under `CHIP_CHAT_DATA_DIR` (`spend.json` + `spend.jsonl` history);
  `monthly_usd` syncs from the OpenRouter API (`monthly_source: "api"`) and
  **survives restarts** — never treat it as session-local.
- **$5.00 / month → WARN** (keep working). **$10.00 → hard stop** (`SpendCutoffError`).
- Before ANY live inject/selftest: `./bin/chip spend` or `./bin/chip ping --spend`.
- Never overwrite the ledger with `usage_monthly=0` from a stale API read —
  heartbeats use `ledger_monthly_usd` (sprint 2026-09-25 note).

## Grok Bot habits worth keeping

- **One bounded envelope per turn**: task + acceptance + stop condition, or refuse.
- **Log every live turn** to the ledger; alert on drift (session window: alert at
  $0.50, stop at $1.00 — per-session windows tighten the monthly caps, never loosen).
- **Mock learns the protocol**: mock understands `delegate:` + `hop-ok-*` so relay
  discipline is testable at $0 (scripts/chip/mock.py).
- **Failures become learnings**: post-crash lessons land in
  `config/chip_chat/personas/<agent>/learnings.md` as
  `YYYY-MM-DD | context | lesson | tag`, and per repo law as a `learning:` comment
  on Linear RAZ-16.
