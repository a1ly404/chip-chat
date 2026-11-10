# Durable memory (remember / forget / recall)

Module: `scripts/chip/memory_store.py` · Tests: `scripts/test_chip_memory_store.py` · Flag: `CHIP_MEMORY` (default off)

Explicit, user-controlled memory with provenance. It sits beside the advisory MemPalace extraction
(`chip.mempalace`), which stays unchanged.

## Scopes

- `user` — one global profile shared by every room.
- `room:<name>` — facts for one room (name sanitised like room logs).

Each scope is one JSON file under `$CHIP_CHAT_DATA_DIR/memory/` (`user.json`, `room__<name>.json`), written
atomically. `audit.jsonl` records `remember`, `supersede`, `evict` and `forget` events by id; it never
stores the text of a forgotten fact.

## Record

`id (m_<12 hex>), scope, kind (fact|preference|decision), key, text, status (active|superseded),
provenance {by, source, at}, superseded_by`

## Rules

- **Provenance required:** every write names `by` (who) and `source` (where it came from); `at` is UTC.
- **Secrets refused:** API-key, token, private-key, `password=`-style and credential-in-URL shapes are
  refused on write (CLI exit 3). Hex digests such as commit SHAs are allowed.
- **Dedup:** re-remembering the same text (case/whitespace-insensitive) returns the existing record.
- **Supersede:** a new fact with the same `--key` in the same scope supersedes the old one (newest wins;
  the old one is kept as `superseded` for audit and excluded from recall).
- **Caps:** text ≤ 1000 chars; at most 500 active facts per scope (oldest evicted first).
- **Recall:** keyword overlap ranking, newest first on ties, `top_k` and a character budget; facts that do
  not fit are skipped, never truncated. `format_recall_block()` renders an advisory prompt block.

## CLI

```
chip memory add "Standup is at nine" [--scope user|room:<name>] [--by NAME] [--source SRC] [--kind fact] [--key KEY]
chip memory list [--scope S] [--all]
chip memory search "standup time" [--scope S ...] [--top-k 5] [--budget 2000]
chip memory forget m_0123456789ab
```

Exit codes: 0 ok, 1 not found, 2 invalid input, 3 refused (secret-shaped / empty / too long).

## Not yet

Turn injection is not wired: nothing reads this store during chat yet. That slice will inject
`recall()` results for the user scope plus the current room only when `CHIP_MEMORY=1`, and will let
agents propose memories that need user confirmation before `remember()`.
