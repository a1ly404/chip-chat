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
| Merge hash | TBD |

**2026-09-25:** Real `pm` Discord id in `rooms.json`; `chip-selftest` bot id → `selftest` speaker on `#chip-selftest`; `dev` still TODO; selftest author defaults documented (`SECRETS.md`, `.env.example`).

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
| Merge hash | TBD |
