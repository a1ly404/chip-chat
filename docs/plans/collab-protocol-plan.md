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

## reopen (spec only, not callable)

Stub pointer: full design is under **reopen (full design, pending Lyla post-RAZ-335)** below (`room`, `actor`, `reason_code`; chair stamp **and** `require_protocol_go`; converged → revealed only; one reopen per `round_id`). Not implemented.

## pm lane root and tool tier

`config/chip_lanes.json` pins `pm` to `CHIP_LANE_PM_ROOT` only. Set that env to the writable chip-chat worktree. There is no razzorstack extra root. `hard_denies` still last-match-wins, so `/mnt/nas/media`, `boards/`, `.env`, and `tool_receipts.jsonl` refuse even inside a root that would otherwise allow them.

pm's lane `tools` list is `pytest`, `git-add`, and `git-commit`. git add/commit paths and cwd must stay inside that root. The global manifest is unchanged for every other persona. RAZ-335 tier-1 rows `gluetun-restart`, `qb-restart`, `airvpn-port-regab`, `fw-rule-reload`, and `qb-port-update` are marked `audience: global-only` and are not on the pm list. `lanes.refuse_blob` still runs before spawn.

## Lane correction — airvpn_port_grab.py

`config/chip_tools.json` row `airvpn-port-regab` argv is `python3 scripts/airvpn_port_grab.py`. The script file may still be absent on disk; **absence is not cancellation**. Disposition after the RAZ-335 ops A/B: ownership **transferred to `cc` (chip-chat lane)** — not voided, not cancelled, not buffrdev, not "drop the tier-1 row". Do not treat the row as dead. Cross-ref mothball / disposition: [`docs/ops/post-ab-runbook.md`](../ops/post-ab-runbook.md). A room reply at 2026-09-29T14:47:40Z that said `delegate:buffrdev` remains wrong. verified-as-of: `2026-09-29T17:29:07Z`.

## Canvas holes still without a code slice

**Sealed by landed slices (P1–P7, P10):** P1 phase gate, P2 sealed goal, P3 stamped speaker, P5 normalized reject + round id, P7 wake block, P10 GO ticket + monthly cutoff (`rotate_chair` dark-gate call site covered by merged #117 / commit `b4809d9`). P4 and P6 have **partial** seal only — remainders below. **P8 is not implemented.** **P9 is not spend proof.**

Still open / remainder:

- **P4 remainder.** Path scopes refuse outside the persona root. A slash-free ticket scope is still not checked against a charter. No slice yet.
- **P6 remainder.** `end_critique` needs one cross-critique or one abstain. It does not require every hypothesis to be read. No slice yet.
- **P8.** Cross-host flock. Spec below. **Not implemented.** POSIX `flock` cannot synchronize WSL and Hyper-V because they are different files.
- **P9.** `run_ab` grade is `len(body)` and a synthetic `kill:` line. That is a **mock grade**, not spend proof. Live ops A/B under GO `REDACTED` **completed**: tie 4/4 both arms; Ally mothball; session cap disarmed; spend `$0.0062` / `$0.25`; receipt RAZ-335 / `63b479a0`. See [`docs/ops/post-ab-runbook.md`](../ops/post-ab-runbook.md) for what happened (not only the predicted flow).

## Cross-host flock (E3 successor, spec only — do not implement in this doc PR)

Local `flock` on `{data_dir}/boards/{room}.json.lock` is correct for **one host**. WSL `~/appdata/chip-chat` and the Hyper-V relay data dir are **different files**, so two writers can both append and both believe they hold the lock. That is why P8 is open.

### Proposal (design-only)

1. **Single-writer relay host (preferred default).** The relay container is the only process that calls `boards.save` for room `dev`. WSL agents read a snapshot or call the relay HTTP surface. They do not open the relay's lock file over a share (SMB/9p/UNC).
2. **Stamp stays (single-host fallback that already exists).** `host` + `data_dir` on the board must match `local_host()` and the process data dir. Mismatch raises `PermissionError` `board_foreign_host` and logs `board_foreign_host`. This refuses a copied file opened on the wrong machine. It does **not** merge two live writers; it is the **single-host fallback** until a shared lock exists.
3. **HTTP compare-and-swap (optional second writer path).** If Lyla later wants two writers: a lock service on the relay host — HTTP CAS on `version`, timeout ~2s, one in-flight writer. Clients send the full board they read plus the next version. The server rejects when `version` is not current. No POSIX flock across SMB or 9p. No second claim registry.

### Rejected heavier options (not proposed)

- **Redis / Zookeeper / etcd** as the board lock: heavier ops surface, new failure domain, and no chip-chat deploy path today. List only as rejected unless Lyla explicitly GOs a cluster lock. Prefer single-writer or HTTP CAS on the relay.

### Trade-offs

| Option | Pros | Cons |
| --- | --- | --- |
| Single-writer relay | No new service; matches Discord lane already | WSL cannot mutate `dev` board locally; inject/relay becomes the write path |
| Stamp-only fallback | Already shipped; fail-closed on foreign host | Does not prevent dual live writers on two data dirs |
| HTTP CAS on relay | True multi-writer without shared flock | New endpoint, timeout/retry law, must not invent webhook URLs |
| Redis/ZK | Familiar distributed lock | Rejected: weight, secrets, and ops out of chip-chat lane |

Until a shared lock ships, the law is **single-writer**. A second host that sees a foreign stamp stops. It does not copy the board over and continue.

## reopen (full design, pending Lyla post-RAZ-335)

**Not callable. Do not implement code. Do not add `reopen` to `chip_tools.json`.**

Signature: `reopen(room, actor, reason_code) -> board`.

Fields (all required in the call and in the audit row):

| Field | Law |
| --- | --- |
| `room` | Board id (e.g. `dev`). |
| `actor` | Must equal chair-stamped speaker (`CHIP_TURN_SPEAKER`) **and** `board["chair"]`. |
| `reason_code` | One of `new_evidence`, `aborted_claim`, `lyla_override`. Empty / unknown → `PermissionError` `reason`. |

Preconditions, all required:

- **`require_protocol_go`** passes (`CHIP_LANE_GO_TICKET` matches `RAZ-\d+`, monthly ledger under `$10`).
- **Chair-stamped speaker:** dispatch has set `CHIP_TURN_SPEAKER`, `actor` equals that speaker, and that speaker equals `board["chair"]`. Both gates — stamp **and** protocol GO — are required.
- `board["phase"]` is `converged` only. No reopen from `brainstorm` / `revealed` / `idle`.
- **One reopen per `round_id`:** if an audit/decision row already records `status=reopened` for this `round_id`, raise `PermissionError` `reopen-once`. A new brainstorm (new `round_id`) resets the budget.

Effect (converged → revealed only):

- Set `phase` to `revealed`. Leave `entries`, `rejected`, `round_id`, `host`, and `data_dir` in place.
- Append a decision/audit row with `status=reopened`, `reason_code`, `actor`, and **`verified-as-of`** (UTC ISO-8601) plus the human `reason` text if supplied alongside the code.
- Do not clear the previous `converged` decision. Log `board_reopened` to the board audit jsonl.

Non-effects: no tool tier change, no claim release, no delegate hop reset, no spend charge by itself.

**Post-RAZ-335 status:** live ops A/B mothballed (tie 4/4; see post-ab runbook). `reopen` remains **not callable** until Lyla explicitly GOs an implementation slice. The names `chip.done_gate.reopen_required` and `chip.monitor.mark_human_reopen` are different APIs and stay as they are.

verified-as-of: `2026-09-29T17:29:07Z`.

## Search receipt — scripts/chip hygiene (work-exhausted)

Re-scanned `scripts/chip/**/*.py` on `origin/main` @ `b2cdf643a0c8d5225dae1c80ba9191d2d96589da` (pm churn post-merge). Patterns: bare `except:`, `time.sleep`, `subprocess.*`, `urlopen`/`requests`/`httpx` without timeout, Path reads that should catch `FileNotFoundError`, silent `except Exception: pass`.

| Pattern | Result |
| --- | --- |
| bare `except:` | **none** |
| `time.sleep` | **one** intentional retry backoff in `jev_router.py` (single retry then fallback). Not a silent hang bug; no fix PR. |
| `subprocess.run` | **two:** `tool.run` (handles `TimeoutExpired` + `OSError`, returns exit code); `ops_session_a_b._chip_room_reply` (timeout=180, raises on nonzero `returncode`). Neither ignores returncode. |
| HTTP without timeout | **none** — all `urlopen` call sites pass `timeout=…`. |
| Path / FileNotFoundError | Config and loaders guard with `is_file()` or raise loudly; no silent miss found that warrants a patch. |
| Silent swallow | `lanes` build path re-raises after receipt; collectors return typed failures. No bare pass-on-error bug found. |

**Verdict:** no REAL bug → **no hygiene fix PR invented**. Prior receipt that claimed "no time.sleep / only tool.run" is stale; this row supersedes it. verified-as-of: `2026-09-29T16:36:31Z`.
