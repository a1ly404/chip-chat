# DEBT_SWEEP — P4 enumeration (2026-09-27 night; enumerate, DON'T delete)

## TODO/FIXME debt
- `scripts/chip/*.py`, `scripts/chip_relay/*.py`: **no actionable TODO/FIXME debt**.
  The only TODO-* strings are `personas.py`'s *intentional* placeholder-key law
  (fail-loud on unconfigured Discord ids) — that's a feature, not debt.
- Older-than-this-week resolve: nothing found older than 2026-09-26 (repo's debt
  already burned during the harvest rounds).

## Dead-code / duplicate helper candidates (enumerated only)
| candidate | location | status |
| --- | --- | --- |
| `tool.run_or_raise()` | `scripts/chip/tool.py` | production callers: 0 (tests only). KEEP — it is the pending Phase-C programmatic executor surface (strict `--execute` contract); re-evaluate at Phase C wire-in |
| `replay.load_turns / format_assistant` | `scripts/chip_relay/replay.py` | production callers: 0 (harness tests only). KEEP — used by validation replays; candidate for consolidation IF a second replay format ever lands |
| `spend.monthly_session_delta_usd` | same module | used internally by `effective_monthly_usd` — fine |
| `authorize.set_compat` | `scripts/chip/authorize.py` | used internally by authorize() — fine, but rename candidate (`_set_compat`) on next touch |
| watchdog `heartbeat_line` | `scripts/chip_relay/watchdog.py` | used by gateway — fine |

## Rule
Delete nothing from this list without a dedicated PR (each row = one-line diff);
the sweep reproduces under `/tmp/opencode` tooling — no state changed tonight.
