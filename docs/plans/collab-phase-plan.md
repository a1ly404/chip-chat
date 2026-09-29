# Collab phase — contract

Base: `07b3225` (`07b32252ae73a2f81809957d9ccee21bc4bf992a`).
Branch: `chipchatdev/collab-phase`. Nothing in slices 1–5 merges to `main`.
Suite at this base must stay green and grow: **423 passed / 1 skipped**.

This file is the spec. Slice PRs cite it. Behavior not named here is out of scope.

## Shared module

`scripts/chip/boards.py`

- Room board path: `{data_dir}/boards/{room}.json` (`config.data_dir()`).
- On-disk object, always:

```json
{"schema": 1, "room": "<room>", "version": <int>, "entries": []}
```

- `schema` is the integer `1` on every board. Loaders reject any other value.
- `version` starts at `0` for an empty new board and increments by 1 on every successful append or seal.
- Entry: `id` (stable string), `author` (persona), `kind` (`hypothesis` only in this phase), `body` (string), `sealed` (bool), `version` (board version at write).
- In-memory handle: `Board` with `version` and `entries`. `refresh(board)` re-reads the file when the on-disk `version` differs from memory (conflict). Callers then append against the reloaded entries. Same version is a no-op read.

## Mutation law (same shape as receipt log)

Global denies are appended **after** persona rules. Last match wins, so a trailing deny beats an earlier allow (`lanes.decide`).

Hard deny fragments (added to `config/chip_lanes.json` `hard_denies` in slice 1):

- `boards/`
- `unlink`
- `truncate` is already covered for receipt paths; board unlink/truncate use an explicit guard, not a new global `truncate` fragment (that fragment is not added: it would change existing lane decisions).

`refuse_board_mutation(path, op)` for `op in {unlink, truncate}`:

- Builds blob `"{op} {path}"`.
- Calls `decide(blob, [("allow", "boards/"), ("allow", op)])`.
- Deny raises `PermissionError` matching `boards/`.
- A persona allow list that names `boards/` still loses.

`tool.run` (slice 1 escalation): when the tool argv or path argument resolves under `boards/`, `tool.run` raises `PermissionError` matching `boards/` before execution, even if the persona rule list allows `boards/`.

## Sealed entries

`edit_entry(board, *, actor, entry_id, body)`:

- Missing id → `KeyError`.
- If `sealed` and `actor != author`: deny. Implementation calls `decide(f"edit {entry_id} author={author}", [("allow", "edit"), ("deny", f"author={author}")])` when `actor != author`. Deny wins. Raises `PermissionError`.
- Author of a sealed entry may replace `body`. Unsealed entries may be edited by any persona that passes `decide` with `[("allow", "edit")]`.
- Edit does not change `id`, `author`, or `sealed`. It bumps board `version` and rewrites the file.

`seal_entry(board, *, actor, entry_id)` sets `sealed` true. Only `author` may seal. Second seal is a no-op success.

`append_entry` adds an unsealed hypothesis authored by `actor`.

## Protocol turn

`protocol_turn(board, *, actor, body) -> Board`:

1. `refresh` (version-check). If on-disk version ≠ memory version, reload entries (conflict path) then continue.
2. Append one hypothesis.
3. Write file with `version + 1`.

No merge of bodies. Conflict never drops the in-flight append; it reloads first, then appends.

## Slices

Each slice is one commit (or a tight stack of commits) on its own branch, one **draft** PR into the previous slice branch (slice 1 into `chipchatdev/collab-phase`).

| Slice | Branch | Depends on | Files |
| --- | --- | --- | --- |
| 0 | `chipchatdev/collab-phase` | `07b3225` | this plan only |
| 1 | `chipchatdev/collab-s1-board-law` | slice 0 | `scripts/chip/boards.py`, `config/chip_lanes.json`, `scripts/chip/tool.py` hook, `scripts/test_chip_boards.py` |
| 2 | `chipchatdev/collab-s2-hypothesis` | slice 1 | hypothesis helpers already in `boards.py` if slice 1 landed them; slice 2 adds `list_open` / `list_sealed` and tests only if not already covered |
| 3 | `chipchatdev/collab-s3-protocol-turn` | slice 2 | `protocol_turn` if not in slice 1; concurrent writer test |
| 4 | `chipchatdev/collab-s4-cli` | slice 3 | `chip board show` and `chip board append` in `scripts/chip/cli.py`, mock-free, data-dir temp |
| 5 | `chipchatdev/collab-s5-room-note` | slice 4 | one transcript line on `threads/<room>.jsonl` when `protocol_turn` succeeds: `{"kind":"board","version":N}` via existing `store.append_jsonl` |

Slice 1 must include the four behaviors below even if later slices also touch the module. Later slices must not redefine them.

### Slice 1 named tests (`scripts/test_chip_boards.py`)

1. `test_sealed_edit_deny_beats_allow` — persona A edits persona B’s sealed hypothesis after an allow rule; `PermissionError`; last-match-wins (`decide` returns `deny`).
2. `test_board_unlink_and_truncate_refused` — `refuse_board_mutation` for `unlink` and `truncate` raises `PermissionError` matching `boards/`.
3. `test_tool_run_persona_allow_cannot_reach_boards` — persona allow of `boards/` still raises from `tool.run` before the tool executes.
4. `test_protocol_turn_refreshes_on_version_conflict` — memory `version` stale vs file; after the turn, both writers’ entries exist and `version` is the reloaded value plus one.
5. `test_board_schema_is_1` — new board and reloaded board have `schema == 1`; a file with `schema: 2` raises `ValueError`.

### Slice 2

- `list_open(board) -> list` unsealed hypotheses, file order.
- `list_sealed(board) -> list` sealed, file order.
- Tests: `test_list_open_and_sealed_partition`.

### Slice 3

- Two `Board` handles, same path. First appends. Second (stale) `protocol_turn` reloads then appends. File has 2 entries, `version == 2` if start was 0.
- Test: `test_two_handles_conflict_reload_then_append` (may be the slice 1 test if identical; slice 3 keeps it and adds `test_refresh_same_version_does_not_drop_entries`).

### Slice 4

- CLI: `board show <room>` prints JSON of the board. `board append <room> --as <persona> -m <body>` calls `protocol_turn`.
- Tests: `test_cli_board_show_schema_1`, `test_cli_board_append_bumps_version` in `scripts/test_chip_boards.py`.

### Slice 5

- Successful `protocol_turn` appends one JSON object to `threads/<room>.jsonl` with keys `kind` = `board` and `version` equal to the new board version.
- Test: `test_protocol_turn_writes_thread_note`.
- Failure paths (deny, schema error) write no thread note.

## Acceptance (every slice PR)

- `python3 -m pytest -q` from the worktree: baseline **423 passed, 1 skipped**, plus new tests, zero failures.
- PR body contains the slice commit hash and the verbatim pytest summary line.
- Draft. Base branch is the previous slice, not `main`, except slice 0 which is plan-only on `chipchatdev/collab-phase` and has no PR required by this contract (push the branch).

## Out of scope

Discord relay changes, MemPalace, spend gates, new tools in `config/chip_tools.json`, merging to `main`, schema other than `1`.
