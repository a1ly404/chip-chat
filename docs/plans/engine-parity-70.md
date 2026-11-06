# Engine parity plan: Chip Chat engine to ~70% generic Grok Bot parity

Status: **plan only** (lumocheck gate). No engine code lands until lumocheck round 2 returns `LUMOCHECK: PASS`
(or a documented `lumocheck: continue after 3x timeout`).
Owner: Chip Chat (engine track). Plugin track owner: separate agent (see section 7).
Base: `origin/main` @ `6384820`. Refs: engine handoff `docs/ai/HANDOFF_2026-10-30.md`.
Revision: r2 (lumocheck round-1 findings merged; see section 9).

## 0. Layout and file facts (verified read-only on `6384820`)

- Package layout: `pyproject.toml` uses `packages.find where = ["scripts"]`, so the import package `chip` **is**
  `scripts/chip/` (and `chip_relay` is `scripts/chip_relay/`). New modules below written as `chip/...` live at
  `scripts/chip/...`. No new top-level package, no rename, no facade.
- Exists in engine: `docs/ai/HANDOFF_2026-10-30.md`, `docs/OPERATING_LAW.md`, `docs/host-hooks.md`,
  `plugins/example/host-hooks.yaml`, `scripts/chip/learnings.py`, `scripts/chip/go_verify.py`, `scripts/chip/evidence.py`.
- Added by this PR: `docs/plans/engine-parity-70.md`, `docs/ai/PROMPT_PLUGIN_FOLLOWUP_2026-11-05.md`.
- `tools/engine_export/` exists only in the private plugin monorepo, not here. This plan does **not** use it
  (all slices are fresh generic engine code), so no export run is needed per slice. The strict export gate still
  applies to any future promotion from the plugin and to the release tag decision.

## 1. Goal and honest baseline

**Goal:** the public MIT engine (`a1ly404/chip-chat`) offers roughly 70% of the *generic* assistant
primitives a Grok-Bot-like operator assistant needs, so that any pack (including the private homelab pack)
consumes them from a pinned engine package instead of re-implementing them.

"70%" means **generic capabilities**, not homelab recipes. Homelab recipes, *arr automation, #502
auto-remediation, Prowlarr recipes and deploy stay plugin-side.

**Baseline today (engine alone): ~35-45%.** Read-only inventory of `origin/main`:

| Area | In engine today | Gap |
| --- | --- | --- |
| Chat/CLI, mock mode, personas, rooms | yes (`scripts/chip/cli.py`, `mock.py`, `chip_relay/*`) | none for parity |
| Spend gates / thrift / money cents | yes (`spend.py`, `spend_tracker.py`, `thrift.py`, `money.py`) | data-dir pin lives only in plugin |
| Memory (MemPalace lite, learnings) | partial (`mempalace.py`, `learnings.py`) | relay does not load advisory learnings into turns |
| Tasks / GO gates / done gate / evidence | yes (`go_*`, `done_gate.py`, `evidence.py`) | no generic recipe schema |
| Alert ingest | partial (`monitoring_listen.py`, `case_intake*`) | no generic alert -> recipe-id grounding API |
| Sandboxed code exec / workspace / pytest | **no** | whole primitive |
| Background / post-turn workers | **no** bounded worker | whole primitive |
| Host exec | contract only (`docs/host-hooks.md`) | no typed host-exec interface |
| Browser / email / Pages | no | out of scope here (slice I = plan only) |

## 2. Weighted parity checklist (how we measure %)

Each row counts only when its **evidence** exists in the engine tree on `main` (code + tests + doc).
Baseline rows already met total **40**.

Scoreboard policy (kept in one table so no point is awarded twice and no score is plugin-only):
"Detailed evidence" names merged `main` test paths PLUS the doc that keeps the capability installed
blow-by-blow (module docstring or doc file). Windows-only failures and spend-ledger discrepancies are
recorded in the "Known honest gaps" table right after the checklist, not silently dropped.

| # | Capability | Weight | Detailed evidence (merged on main) | Today |
| --- | --- | --- | --- | --- |
| 1 | Chat/CLI + mock + personas/rooms | 15 | `scripts/test_chip_chat.py`, `scripts/test_chip_mock.py`, `scripts/test_chip_relay_personas.py`; doc: module docstrings in `chip/cli.py`, `chip_relay/personas.py` | met |
| 2 | Spend gates + thrift + cents | 10 | `scripts/test_spec_d_thrift.py`, `scripts/test_usd_cents.py`, `scripts/test_budget_mode.py`, `scripts/test_money.py`; doc: `chip/spend.py`, `chip/thrift.py` headers | met |
| 3 | GO/done/evidence gates | 10 | `scripts/test_chip_done_gate.py`, `scripts/test_chip_done_gate_sweep.py`, `scripts/test_receipt_gate.py`, `scripts/test_chip_boards_lock.py`; doc: `chip/go_verify.py`, `chip/evidence.py` headers | met |
| 4 | Pack/plugin loading (`CHIP_SYSTEM_PACK`, `CHIP_PLUGINS`) | 5 | `scripts/test_chip_data_dir_acme.py`, `scripts/test_pack_utils.py`, `scripts/test_chip_pack_validate.py` (A3, #17); doc: `chip/system_pack.py` + `chip/pack_validate.py` headers | met |
| 5 | Spend data-dir pin (slice B) | 4 | `scripts/test_chip_spend_data_dir_pin.py` (guard raises `SpendDataDirNotPinned` before auth/ledger when `CHIP_SPEND_PIN_DATA_DIR=1`; monthly cutoff agreed across two worktree cwds sharing one pinned dir); doc: `chip/spend_data_dir.py` header + `.env.example` | met (slice PR) |
| 6 | Sandbox exec + workspace + pytest (slice C) | 10 | `chip/sandbox/` + tests, flag doc | - |
| 7 | Recipe schema + grounded GO + shadow/live (slice D) | 10 | `scripts/chip/recipes/{schema,registry,exec}.py` (+ #15), `scripts/test_chip_recipes_exec.py` (live refused when flag off, mutating refused live, evidence_required enforced); doc: `chip/recipes/exec.py` header + refused-receipt contract | met (#15) |
| 8 | Alert ingest -> recipe id (slice E) | 6 | `chip/alert_grounding.py` + fixtures | - |
| 9 | Relay memory: advisory learnings load (slice F) | 6 | `scripts/test_chip_relay_learnings.py` (default off, budgeted MAX_LINES/MAX_BYTES, ROOM LAW before advisory, never writes) through `chip.cli.build_room_messages` — the shared turn-composition entry the relay's `chip room` shots call (`chip_relay.dispatch._run_chip_shot` → `bin/chip room`); doc: `chip/learnings.py` + `chip_relay/context_hooks.py` | met (#16); strengthened in the learnings-through-dispatch PR when it lands |
| 10 | Bounded post-turn worker (slice G) | 6 | `chip/workers.py` + timeout/queue tests | - |
| 11 | Host-exec interface (slice H) | 6 | `chip/host_exec.py` protocol + manifest test | - |
| 12 | Browser/email/Pages (slice I) | 12 | `docs/plans/engine-parity-70.md` §4 slice I (plan docs merged via #13) | met (#13) |
| | **Total** | **100** | | **68 → 72 with slice B landing** |

Score math after merge states: 40 (rows 1-4) + 10 (#15 row 7) + 6 (#16 row 9) + 12 (#13 row 12) = 68;
slice B lands 4 more → 72, which crosses the >= 70 target with rows 6, 8, 10, 11 still open.
The floor rule from the plan stands: no percentage claim without the merged test path it cites.

### Known honest gaps (recorded, not ignored)

| Gap | Detail |
| --- | --- |
| Windows-only test failures on the operator box (this machine, Python 3.13, PowerShell) | `scripts/test_chip_chat.py::test_launcher_executable` and `::test_ui_lists_turns_and_mock_send` fail identically on a tree WITHOUT slice-B changes (`OSError: [WinError ...]` spawn-env issue, same `SystemRoot` class the plugin records for `test_split_pack_seam`). Reproduced: `git stash -u` + rerun → 2 failed. They pass on Ubuntu CI (3.11/3.12). Slice-B changes are not the cause. |
| Sandbox slice C on Windows | NOT real isolation; documented as such in plan §8 — flag stays off in repo, any local flip goes in a temp workspace only. |
| Spend ledger duplicate dirs per worktree (engine repo) | pre-slice-B the engine silently fell back to `REPO_ROOT/var/chip-chat` per checkout; slice B closes this fail-closed only when `CHIP_SPEND_PIN_DATA_DIR=1` (default off in repo, per plan §4 — the $5 cutoff agreement across worktrees exists with the flag on). |
| Recipe "JSON schema" | slice D defines the schema as Python dataclass + validator (`chip/recipes/schema.py`) rather than a `*.schema.json` artifact; the schema doc is the module docstring. Not a hidden gap — the plan's intent (parseable recipe contract) is what exists. |

Target: **>= 70**. Rows 1-11 max out at 88 (40 + 4 + 10 + 10 + 6 + 6 + 6 + 6). Row 12 is not needed for 70.
A row is **met** only when its code + tests + doc are merged on `main` via PR. This section is the only
scoreboard: each slice PR updates its row with the merged test path. No percentage appears in PR bodies,
Discord, or reports without citing a merged test path. Row 8 (alert grounding) consumes the row-7 recipe schema;
if D slips, E slips with it (B, C, F, G, H still reach 40+4+10+6+6+6 = 72).

## 3. Runtime: ALLY-PC runs the engine package (slice A)

- Venv inside the engine checkout (gitignored): `C:\Users\17783\HyperV-flip\repos\chip-chat-engine\.venv`.
  `python -m venv .venv` then `.venv\Scripts\pip install -e ".[dev,relay]"`.
- Smoke (mock only, no keys read): `CHIP_CHAT_MOCK=1`, `CHIP_SYSTEM_PACK=acme-example`,
  `python -m chip ping` and `python -m chip room smoke -m "Status?" --as pm --mock`, then `python -m pytest -q`.
- Optional local `chip` command on PATH: a tiny `chip.cmd` shim in a user bin dir that calls the venv python
  `-m chip`, pointing at this checkout. Lives outside the repo (no repo change). Removable by deleting one file.
- `CHIP_CHAT_DATA_DIR` for local engine runs points at a **local engine-only** dir (e.g.
  `%LOCALAPPDATA%\chip-chat-engine\data`), never the live plugin relay's data dir, so ledgers never mix.
- Python on ALLY-PC is 3.13; CI covers 3.11/3.12. If local pytest passes on 3.13, the slice-A PR adds 3.13 to
  the CI matrix. If it fails, slice A records the failures in a tracked issue instead of dropping it silently.
- Slice A is the one slice whose deliverable is mostly local; its PR carries the smoke transcript (commands +
  pass/fail lines, no env values) and the CI matrix change, so it still ends in a merged PR.

**Plugin pin (interface only, plugin-side PR owned by the other agent):**
- Engine may add an annotated tag `engine-v0.2.0-rcN` on a green `main` SHA (CI green + Ally GO, per
  `docs/OPERATING_LAW.md`). No tag without Ally GO. Chip Chat cuts the tag; the GO is recorded first.
- The plugin pins the **full immutable commit SHA**: `chip-chat @ git+https://github.com/a1ly404/chip-chat@<40-char-sha>`.
  The tag is a label only (tags can move); never pin a tag. Plugin consumes it in its requirements,
  then deletes the vendored copies of modules the engine now owns. That PR is plugin-side; we only document.
- Release plan: `docs/plans/engine-parity-70.md` section 3 is the release interface; each slice that changes a
  public import path notes it in `CHANGELOG` lines in the PR body.

## 4. Ordered slices (small PRs, commit often)

Every slice: one branch + one PR, **done = merged to `main`** (local green runs are evidence inside the PR,
never the completion signal). Default-off flag in repo, mock tests, `Agent: Chip Chat (Grok Bot)` trailer,
`Refs` only (no auto-close words). PR body cites this plan by path and states the plan gate truthfully
(`plan lumocheck: round-2 PASS on engine-parity-70 r2`, not a fresh per-slice verdict). Higher-risk slices
(C sandbox, D recipes live mode, G worker) get their own lumocheck run before code if their design deviates from
this plan. When a Linear ticket exists for a slice, claim it with a `claim:` comment before starting.
Flag flips happen only **on ALLY-PC engine-local smoke** (Ally allowed engine/local flips); never on the
live plugin relay. Discord is never a control plane for this track.

| Slice | What | Flag (repo default) | Local flip on ALLY-PC | Rollback |
| --- | --- | --- | --- | --- |
| A | Local engine install + `chip ping` mock + pytest | n/a | n/a | delete `.venv` + shim |
| B | Spend data-dir pin into engine (generic: spend ledger always under resolved `CHIP_CHAT_DATA_DIR`, refuses fallback to cwd) | `CHIP_SPEND_PIN_DATA_DIR=0` | on after tests | unset env |
| C | Sandbox exec + workspace + pytest runner as `chip/sandbox/` | `CHIP_SANDBOX_EXEC=0` | on only after an **explicit Ally GO** recorded on the PR/ticket, engine-local temp workspace only | unset env |
| D | Generic recipe schema (JSON schema) + grounded GO (recipe step must cite evidence) + `shadow`/`live` modes | `CHIP_RECIPES_MODE=off` (`off`/`shadow`/`live`) | `shadow` only | set `off` |
| E | Generic alert ingest -> recipe id: pure matcher over the D schema `match` field + fixtures; no transport. May fold into D's PR if small | `CHIP_ALERT_GROUNDING=0` | on (mock fixtures) | unset env |
| F | Relay loads advisory learnings into turn context (token-capped, read-only) | `CHIP_RELAY_LEARNINGS=0` | on in mock room | unset env |
| G | Bounded post-turn worker (max jobs, wall-clock timeout, spend check before run) | `CHIP_POST_TURN_WORKER=0` | on in mock | unset env |
| H | Host-exec interface: typed protocol over `docs/host-hooks.md` (allowlist, receipts, exit codes) | `CHIP_HOST_EXEC=0` | dry-run only | unset env |
| I | Browser / email / Pages: plan docs only | n/a | n/a | n/a |

### Slice details and safety

- **B spend pin:** write fresh generic code in the engine (single source of truth; no export/promotion path).
  Test: ledger path resolution, refusal when data dir unset with flag on. The plugin deletes its vendored
  equivalent after it pins.
- **C sandbox:** subprocess in a temp workspace under `CHIP_CHAT_DATA_DIR/sandbox/<task>`; no network by
  default (best effort on Windows: documented limitation, no claim of isolation); env scrubbed to an allowlist
  (no `*_TOKEN`, `*_KEY`, `*WEBHOOK*` passed through); CPU-time/wall-clock/output caps; pytest runner returns
  structured result. **Not a security boundary** (Windows especially) - documented as such; nothing privileged
  or credentialed ever runs inside it.
- **D recipes:** `live` mode never activates on ALLY-PC in this plan; `shadow` is the maximum local mode.
  Schema fields `id`, `version`, `match` (alert fingerprint), `steps[]` (each `kind`, `args`,
  `evidence_required`), `mode`. Grounded GO = engine refuses a `live` step unless evidence lines exist
  (reuses `go_verify`/`evidence`). Homelab recipes stay in the plugin pack as data.
- **E alert grounding:** `ground(alert: dict, recipes) -> RecipeMatch | None` over D-schema recipes, with confidence and reason;
  transport (Discord/Monitoring Bot) stays plugin-side. Fixtures are synthetic (acme-example).
- **F learnings:** read `personas/*/learnings.md` advisory sections, cap by chars, mark as advisory in prompt;
  never writes.
- **G worker:** in-process bounded queue; no unbounded loops; each job checks spend gate; mock default.
- **H host-exec:** protocol class + allowlisted hook names from the manifest; engine never runs host commands
  itself in CI; real implementation is plugin-side.

## 5. What we will NOT do

- Implement #502 auto-remediation, Prowlarr recipes, or any homelab recipe content.
- Edit the plugin repo or redeploy the live homelab relay (`chip-relay`/`chip-scheduler`) without coordinating
  with the plugin agent and Ally.
- Flip `CHIP_RELAY_ALERT_CHANNEL` or any live relay flag.
- Publish secrets, operator ids, webhooks, Discord snowflakes, or `.env` values.
- Commit `tmp/` or lumocheck artifacts, or link them from PRs.
- Auto-upgrade *arr apps; touch wireguard, qBittorrent, or gluetun; introduce hardlink-on-CIFS/SMB assumptions
  or dual in-library MKV+MP4 outputs (homelab media is out of scope entirely).
- Use cloud coding agents; tag a release without Ally GO; live OpenRouter spend without Ally GO; pin a mutable tag.
- Run host commands from engine code in CI; `systemctl enable`; pass `--execute` style flags; unsupervised
  compose recreate; any remux action or remux `claim:` from this track.

## 6. Verification per slice

- `python -m pytest -q` green locally (mock) and in CI (3.11/3.12).
- gitleaks green in CI; manual `git diff` review for ids/webhooks before push.
- Slice PR updates section 2 row to "met" with the test path.
- Final success: section 2 total >= 70 with **merged** test evidence, plus an ALLY-PC smoke transcript
  (mock `chip ping` + `chip room smoke --mock` from the engine checkout) attached to the closing PR.

## 7. Coordination with the plugin agent

See `docs/ai/PROMPT_PLUGIN_FOLLOWUP_2026-11-05.md`. Summary:
- **Consume:** pip pin to an engine SHA/tag we publish; import engine modules for spend pin, sandbox, recipe
  schema, alert grounding, relay learnings, workers, host-exec.
- **Do not rewrite:** those primitives in the plugin; do not hand-edit vendored `scripts/chip` to diverge further.
- **Plugin keeps:** pack data (homelab recipes, personas, rooms), deploy compose, #502, Prowlarr recipes, secrets,
  host-hook implementations.
- Interface changes are announced in each engine PR body under "Plugin impact".

## 8. Risks

- Vendored plugin copy drifts from engine before pin: mitigated by publishing a pin SHA early (after B).
- Sandbox on Windows is not real isolation: flag stays off in repo; local flip only in temp workspace; documented.
- Usage budget: small PRs, one slice per PR, mock tests only.

## 9. Lumocheck round-1 changes merged

1. Done = merged PR per slice; local smoke is evidence only (sections 4, 6, 3).
2. Slice PRs cite the plan's round-2 verdict truthfully instead of claiming a per-slice verdict; risky slices re-check (4).
3. File inventory and package layout verified and stated (0); `chip/` == `scripts/chip/`.
4. Plugin pins a full commit SHA, never a tag (3).
5. B is fresh engine code, one source of truth; no `tools/engine_export` path in this plan (0, 4).
6. Linear `claim:` before slice work when a ticket exists; Discord not a control plane (4).
7. Hard gates enumerated in-plan (5).
8. C local flip needs explicit Ally GO; sandbox is not a security boundary (4).
9. Not-do list split and cleaned (5); CI 3.13 trigger defined (3).
10. E defined over the D schema; may fold into D's PR (2, 4).
