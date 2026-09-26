# Sprint log

## Phase 1 — Containerize chip-relay (Freeze)

| Field | Value |
| --- | --- |
| Date | 2026-09-25 |
| Intent | Dockerfile + compose for Hyper-V Docker; internal `/health` on 48082; persistent `chip-chat-data` volume; boot `.env` mount; document Infisical/heartbeat env. |
| PR | #13 |
| Merge hash | `f4cdfb6` |

## Phase 2 — Speaker maps + selftest persona (Freeze)

| Field | Value |
| --- | --- |
| Date | 2026-09-25 |
| Intent | Populate `rooms.json` `speakers` for `#chip-dev` / `#chip-relay` (TODO placeholders until ops paste Discord ids); loud failure when maps are unset; selftest resolves `--as` from speakers (deprecate forced `pm`); unit tests + `docs/SECRETS.md` restart policy. |
| PR | #14 |
| Merge hash | `10899ab` |

**2026-09-25:** Real `pm` Discord id in `rooms.json`; `chip-selftest` bot id → `selftest` speaker on `#chip-selftest`; selftest author defaults documented (`SECRETS.md`, `.env.example`).

**2026-09-26:** Ally human `187044496872046592` → `pm` on `#chip-dev` / `#chip-relay`; removed `TODO_DEV` (AI `dev` is webhook-only). Spend: do not overwrite ledger with OpenRouter `usage_monthly=0`; heartbeats use `ledger_monthly_usd`; persistence regression test.

## Phase 3 — MemPalace Lite (Freeze)

| Field | Value |
| --- | --- |
| Date | 2026-09-25 |
| Intent | Mine-as-you-go memories on `chip room --close`; store under `memories/<room>/<agent>.jsonl`; recall top-K (~5, ~500 tokens) into the system prompt as **advisory only**; offline unit tests for extract/recall/top-K. |
| PR | #15 |
| Merge hash | `506b94e` |

## Phase 4 — Task execution layer (Freeze)

| Field | Value |
| --- | --- |
| Date | 2026-09-25 |
| Intent | Allowlisted tool manifest (`config/chip_tools.json`) with risk tiers; `chip tool` / `chip task` CLI and `chip.tool.run`; execution gated on `--execute` / `CHIP_TOOL_AUTO` (default off); task envelopes with `tool_invocations` + `execution_result`; 30s timeout kill; JSONL audit trail; offline tests. |
| PR | #16 |
| Merge hash | `033568c` |

## Phase 5 — Path A local UI (Freeze)

| Field | Value |
| --- | --- |
| Date | 2026-09-25 |
| Intent | Thin localhost UI scaffolding: room list, JSONL transcript view, send via `./bin/chip room … --once` (mock unless `CHIP_CHAT_UI_LIVE=1`); `bin/chip-ui` entrypoint; design doc `docs/UI_PATH_A.md`; WSL run notes in README. No auth, no Cloudflare. |
| PR | #18 |
| Merge hash | `816bc62` |

## Phase 6 — Spec A standing law bundle (Freeze)

| Field | Value |
| --- | --- |
| Date | 2026-09-26 |
| Intent | law.md + constraints.jsonl on `/data/chip-chat`; session_boot inject order; BLOCKED/NEED_TOOL semantics; OPERATING_LAW 1–20; pm/dev channel law; mock tests T1–T4. |
| PR | #20 |
| Merge hash | `13171f2` |

## Phase 7 — Spec B evidence-gated done (Freeze)

| Field | Value |
| --- | --- |
| Date | 2026-09-26 |
| Intent | acceptance[] + evidence[]; try_done(); never_auto_done.txt; task envelope close path; mock tests T1–T4. |
| PR | #21 |
| Merge hash | `0ede43e` |

## Phase 8 — Spec D thrift ladder (Freeze)

| Field | Value |
| --- | --- |
| Date | 2026-09-26 |
| Intent | Static keyword class table; thrift_skip/budget/need_ally_go/max_hops; validation stubs; mock tests T1–T3. |
| PR | #22 |
| Merge hash | `75cc45b` |
