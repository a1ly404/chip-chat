# Collab protocol — contract

Base: `main` at `4c247d1` (`4c247d19356e65229e3c927e5a4dcf2c8c725367`), pull request #101.
Suite baseline: **433 passed / 1 skipped**. It must stay green and grow.
Branch: `chipchatdev/collab-protocol`. Slices A–E are draft PRs stacked on this plan commit. Nothing in those PRs merges to `main`.

This file is the spec. Behavior not named here is out of scope. Mid-flight deviations are named in the PR body.

Substrate reused from #101 (`scripts/chip/boards.py`): `protocol_turn` (unchanged), `reveal_all`, `seal_entry`, flock + version CAS, schema 1, last-match-wins, `boards/` mutation law.

New module: `scripts/chip/collab.py`. It stores round state on the same board object (schema stays `1`):

- `phase`: `idle` | `brainstorm` | `revealed` | `converged`
- `goal`: string
- `participants`: list of persona ids, order is the round-robin
- `chair`: current persona id
- `chair_history`: list of persona ids, append-only
- `rejected`: list of `{id, body, reason, author}`
- `decision`: object or `null`

Entry kinds added: `critique`, `decision`. Hypotheses stay `kind: hypothesis`.

`protocol_turn` still appends `sealed: false`. `submit_hypothesis` calls it, then `seal_entry` as the author, so the stored hypothesis is `sealed: true` without changing `protocol_turn`.

Claims: chair assignments call `chip.taskflow.claim_if_free(room, persona, scope)`. No second claim registry.

## Slice A — sealed brainstorm

Branch `chipchatdev/collab-pa-brainstorm`, base this plan branch.

- `begin_brainstorm(room, goal, participants)` creates the board if needed, sets `phase=brainstorm`, `goal`, `participants`, and appends one unsealed `kind: hypothesis` goal entry authored by `participants[0]` with body equal to `goal`. Chair starts as `participants[0]`. `chair_history` gains that id once.
- `submit_hypothesis(room, actor, body)` allowed only when `phase==brainstorm` and `actor` is a participant. Uses `protocol_turn` then seals. Duplicate of a `rejected[].body` raises `PermissionError` matching `rejected`.
- `reveal_all(room)` then sets `phase=revealed`. `submit_hypothesis` after that raises `PermissionError` matching `reveal`.

Tests in `scripts/test_chip_collab.py`:

- `test_sealed_submission_after_reveal_refused`

## Slice B — forced critique

Branch `chipchatdev/collab-pb-critique`, base slice A.

- `critique(room, actor, target_id, body)` appends `kind: critique` with `target_id`, only in `phase==revealed`.
- Target must be a hypothesis whose author is not `actor`. Self-target raises `PermissionError` matching `self-critique`.
- At most 3 critiques per persona per round. A fourth raises `PermissionError` matching `cap`.
- `end_critique(room)` succeeds only when every participant has at least one critique whose target hypothesis author is a different participant. Otherwise `PermissionError` matching `early`. Success sets `phase` unchanged (`revealed`) and `critique_closed: true`.

Tests:

- `test_self_critique_refused`
- `test_early_convergence_refused_until_each_critiques_another`

## Slice C — convergence + dissent

Branch `chipchatdev/collab-pc-converge`, base slice B.

- `converge(room, chair, plan_body, assignments, dissent)` requires `critique_closed` and `chair` equal to the board chair. Writes `decision` and one `kind: decision` entry. `decision` fields: `plan` (string), `assignments` (list of `{persona, scope}`), `dissent` (list of `{author, text}` stored verbatim).
- Each assignment calls `taskflow.claim_if_free`. A lost claim raises and does not write `decision`.
- `dissent` entries are not updated by later `edit_entry` on the decision id: `edit_entry` of a `kind: decision` entry raises `PermissionError` matching `dissent`.
- `reject_hypothesis(room, entry_id, reason)` copies that hypothesis body into `rejected` and removes it from `entries`. A later `submit_hypothesis` with the same body raises `PermissionError` matching `rejected`.

Tests:

- `test_dissent_survives_convergence_verbatim`
- `test_reject_hypothesis_blocks_resubmission`

## Slice D — chair rotation

Branch `chipchatdev/collab-pd-chair`, base slice C.

- `rotate_chair(room)` sets `chair` to the next participant after the current chair (wrap), appends that id to `chair_history`.
- Chair does not change lane `decide` results or `tool.run` denies. Same `boards/` global deny.

Doc: one paragraph in `docs/CHIP_CHAT.md`: lanes constrain hands, not minds. Any persona may reason about any lane. Execution stays pinned to that persona’s root and the global denies. Chair is conversational authority only.

Tests:

- `test_chair_gets_no_extra_tool_powers`

## Slice E — A/B runner

Branch `chipchatdev/collab-pe-ab`, base slice D.

- `run_ab(room, problem, personas, *, solo, trio_log)` in `scripts/chip/collab_ab.py`.
- Solo arm: one persona, one `protocol_turn` hypothesis, no critique. Trio arm: `begin_brainstorm` → one sealed hypothesis each → `reveal_all` → one cross-critique each → `end_critique` → `converge` with the first hypothesis as `plan_body` and dissent from the other two bodies.
- Mock only. No OpenRouter. Each arm records `tokens` as `len(body)` (deterministic stand-in for cost) and `ms` from a counter the test injects (default 0).
- Board `comparison` object: `evidence` (count of entries with non-empty body), `hypotheses` (count), `critiques_that_killed` (count of critiques whose target id is in `rejected` — trio arm rejects nothing unless a critique body contains `kill:`, in which case that target is rejected before converge), `time_ms`, `tokens_by_persona`, `grade`.
- Rubric: grade `pass` when trio hypotheses >= solo hypotheses and trio recorded at least one cross-critique; else `fail`.
- Live OpenRouter run is **not** part of this module. A checked-in transcript `docs/plans/collab-protocol-ab-transcript.md` records one low-stakes mock problem (`"Should the health port stay 48082?"`) with both arms’ comparison JSON. A live call is out of scope for this contract because the baseline suite is offline.

Tests:

- `test_ab_runner_both_arms_cost_accounting`

## Acceptance

Every slice PR body contains that slice’s commit hash and the verbatim `pytest -q` summary line. Draft. Base is the previous slice branch, not `main`.
