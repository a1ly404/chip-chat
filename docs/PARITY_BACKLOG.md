# Parity backlog: Chip Chat engine vs a general operator assistant

Status: living backlog for the hourly parity loop. Inventory taken read-only on `main` @ `6afdfac`
(353 passed, 1 skipped, mock mode, Python 3.13 on Linux).

Rules for this file:

- **Progress = merged, tested features.** An item moves to `done` only when its code, tests and a short doc
  (module docstring or `docs/` page) are merged on `main`. No percentages.
- Every item has a **done-test**: the concrete test(s) or CLI transcript that must pass in CI.
- New capabilities ship **default-off** behind an env flag, mock-first, with no live spend in CI.
- Two consecutive failed attempts on the same item mark it `blocked` here with the exact reason.
- Generic engine only: no operator hostnames, IPs, channel ids, tailnet names or private pack paths.

Status legend: `done` / `partial` / `missing` / `blocked` / `in-progress`.

## Phase 1 — core assistant

### P1.1 Scheduled routines with run history — `done` (mock runner; real dispatch via P1.4)

Evidence: `docs/examples/scheduler.crontab` (supercronic sample calling a `bin/chip-job` that is **not** in
`bin/`); `scripts/chip/budget_mode.py` / `topology.py` mention schedules; no engine scheduler, no routine store,
no run-history ledger.

Gap: a generic routine model (id, schedule, prompt/job kind, enabled, next-run) persisted under
`CHIP_CHAT_DATA_DIR/routines/`, a tick function that decides what is due (pure, clock injected), and an
append-only run-history JSONL per routine (start, end, status, receipt/summary, spend).

Done-test: `scripts/test_chip_routines.py` — create/list/enable/disable/delete; cron and interval parsing;
`due(now)` deterministic with injected clock (incl. DST-safe UTC math and missed-run catch-up cap = 1);
tick dispatches via injected runner (mock) and appends run history; runner error → `failed` row; flag
`CHIP_ROUTINES=0` default → tick refuses. CLI: `chip routine add|list|run-due|history` mock transcript.

### P1.2 Durable memory and recall — `done` (store + CLI; turn injection behind `CHIP_MEMORY` is a follow-up)

Evidence: `scripts/chip/mempalace.py` (per room/agent JSONL, regex extraction, keyword `select_top_k`,
`format_recall_block`), `scripts/chip/learnings.py` (advisory, read-only), tests
`scripts/test_chip_mempalace.py`, `scripts/test_chip_relay_learnings*.py`.

Gap: memory is room-scoped and extraction-only. Missing: explicit `remember`/`forget` (by id) with provenance
(who/when/source), a global user-profile scope alongside room scope, dedup/supersede of contradicting facts,
CLI to list/search, and caps on stored size.

Done-test: `scripts/test_chip_memory_store.py` — remember → recall round-trip across scopes; forget by id
removes from recall; supersede keeps newest; size cap evicts oldest; secret-shaped text refused on write;
recall budget respected. CLI `chip memory add|list|search|forget` mock transcript.

Shipped: `scripts/chip/memory_store.py`, `docs/MEMORY.md`, 19 tests. Follow-up (P1.2b): inject recall for
user + current room into turns when `CHIP_MEMORY=1`, and agent-proposed memories that need confirmation.

### P1.3 Reusable skills catalog — `done`

Shipped: `scripts/chip/skills.py`, `chip skill list|show|search|add|delete`, `docs/SKILLS.md`,
`scripts/test_chip_skills.py`. Turn injection behind `CHIP_SKILLS` (default off) is follow-up P1.3b.

Evidence: pack surface `skill_bodies_dir` (`scripts/chip/system_pack.py`, `scripts/chip/pack_validate.py`),
`systems/acme-example/skill-bodies/checkout-latency.md`, `monitoring_skills.json`; plan
`docs/skills/SKILLS_FREEMIUM_PLAN.md`.

Gap: skills are pack data for monitoring only. Missing: a generic catalog API (list, show, search by
description), front-matter schema (name, description, triggers, version), user-created skills under the data
dir (pack skills read-only), and injection of a selected skill body into a turn with a byte budget.

Done-test: `scripts/test_chip_skills.py` — parse front-matter; invalid skill skipped with reason; list merges
pack + user dirs (user cannot shadow pack id without explicit override flag); select by trigger; turn
injection respects budget; CLI `chip skill list|show|add`.

### P1.4 General background workers with hop/spend caps — `done` (mock step; real model step is P1.4b)

Evidence: `scripts/chip/workers.py` (bounded post-turn queue: active/timeout/depth caps, spend guard, restart
recovery) with `scripts/test_chip_workers.py`; relay delegate hop cap in `scripts/chip_relay/delegate.py`.

Gap: workers run one injected callable per job; there is no general "background agent" job kind (a task
prompt run in mock/LLM mode producing a result receipt), no per-job hop cap / spend ceiling recorded on the
job, and no CLI to enqueue/inspect.

Done-test: `scripts/test_chip_workers_agent.py` — agent job kind runs a mock turn loop bounded by
`max_hops`; exceeding hops → `failed: hop cap`; per-job spend ceiling enforced before each hop; receipts list
hops and spend; CLI `chip worker enqueue|list|show` mock transcript.

Shipped: `scripts/chip/bg_jobs.py`, `chip bgjob enqueue|list|show|cancel|caps|run`,
`chip routine run-due --via-jobs`, `docs/JOBS.md`, `scripts/test_chip_bg_jobs.py` (25 tests): per-job hop and
spend caps (pre- and post-hop), daily job + spend caps with running-job reservation, parallel read jobs up to
`CHIP_JOBS_READ_CONCURRENCY`, mutating jobs serialized, `CHIP_JOBS` default off. Follow-up P1.4b: a real
model-turn step (budget-mode aware, spend ledger rows) and a PM job picker that enqueues from alerts/Linear
within policy.

P1.4b slice 1 — `done`: `scripts/chip/bg_model_step.py` (real model step via `runtime.complete`: spend guard,
thrift/stop rules — mutate refused in thrift, everything refused at the cap — per-job rows in
`bg_jobs_spend.jsonl`; `CHIP_JOBS_MODEL_STEP` default off) and `scripts/chip/bg_picker.py` (policy-driven
read-only picks from alert/Linear inputs, per-day dedupe, daily pick cap; `CHIP_PICKER` default off), CLI
`chip bgjob pick` and `chip bgjob run --model-step`, `scripts/test_chip_bg_model_step.py` (21 tests, fakes
only). P1.4c: live input adapters (alert stream + Linear poll) feeding the picker, and a scheduled PM tick
(routine) that runs pick → run_pending.

P1.4c — `done`: `scripts/chip/pm_tick.py` (alert-stream adapter with a persisted byte cursor over the
relay's `monitoring_dispatch.jsonl` or any JSONL path; Linear poll adapter via an injected client plus a
small read-only GraphQL client behind `CHIP_PM_LINEAR`; `tick()` = recover → budget check → pick →
run_pending with history rows; `kind=pm-tick` routines dispatched by `chip routine run-due --via-jobs`;
`CHIP_PM_TICK` default off), CLI `chip bgjob tick`, `scripts/test_chip_pm_tick.py` (20 tests, fakes
only). Follow-ups (P1.4d): routine path with the real model step, retry cost added to per-job spend,
daily Discord summary of tick history, and enabling the flags in the live relay (deploy step).

### P1.5 MCP connector support with allowlist + per-tool approval levels — `missing`

Evidence: none (only a word match in `scripts/chip/taskflow.py`).

Gap: an MCP client (stdio transport first) that lists tools from a configured server, an allowlist per
server, and per-tool approval level (`auto` / `ask` / `deny`), with every call audited.

Done-test: `scripts/test_chip_mcp.py` against an in-repo fake stdio MCP server fixture — initialize +
`tools/list`; un-allowlisted tool refused; `ask` tool returns a pending approval and does not run; `auto`
tool runs and is audited; malformed server output fails closed. Flag `CHIP_MCP=0` default.

## Phase 2 — tools

### P2.1 Web search / fetch — `missing`

Evidence: only OpenRouter / internal HTTP calls (`scripts/chip/openrouter.py`, relay gateways).

Done-test: `scripts/test_chip_web.py` — fetch tool with scheme allowlist (http/https), private-address
refusal, size and time caps, HTML→text extraction, content fenced as untrusted; search provider interface
with a fake provider; no network in CI (local `http.server` fixture).

### P2.2 File generation / attachments — `missing`

Evidence: none beyond relay routing mention of attachments.

Done-test: `scripts/test_chip_files.py` — write artifacts (md/csv/json) into a per-task output dir under the
data dir with path confinement; manifest with sha256; size cap; relay attachment envelope shape.

### P2.3 Email draft-then-send approvals — `missing`

Done-test: `scripts/test_chip_email.py` — drafts persisted; send requires an approval record bound to the
draft hash (reuse `chip.action_auth`); edited draft invalidates approval; transport is an injected fake.

### P2.4 Browser (Playwright) default-off — `missing`

Done-test: adapter interface + refusal when `CHIP_BROWSER=0`; optional test skipped if Playwright missing.

### P2.5 HTML Pages — `missing`

Done-test: render a page from markdown into the data dir with a stable id and an index; no network.

## Phase 3 — safety / quality

### P3.1 General JEV-style auto-review on every tool — `partial`

Evidence: `scripts/chip/jev_router.py` (registry-first scorer, shadow bands, fail-closed) with
`scripts/test_chip_jev_router.py`; `scripts/chip/tool.py` gating (`CHIP_TOOL_AUTO`, strict, audit).

Gap: review is wired to specific sites, not to a single pre-execution hook every tool path (tool run,
MCP, web, email, host-exec) passes through.

Done-test: one `review(action)` hook called by each tool path; tests prove each path calls it and that a
deny/gray verdict refuses with an audit row. Thresholds unchanged.

### P3.2 Tamper-proof approval store + socket-free job runner — `partial`

Evidence: `scripts/chip/action_auth.py` (hash-bound, single-use, expiry, bot approvers refused, policy pin)
with `scripts/test_chip_action_auth.py`; `scripts/chip/host_exec.py` dry-run adapter.

Gap: append-only hash-chained store (tamper evidence), and an engine-side runner that consumes a record and
executes from a file queue without any network listener.

Done-test: hash chain verify detects edited/removed rows; runner consumes once; replay refused.

### P3.3 Linux + Windows CI and per-feature regression suites — `partial`

Evidence: `.github/workflows/ci.yml` runs ubuntu-latest, Python 3.11/3.12. Known Windows-only failures are
listed in `docs/plans/engine-parity-70.md` (spawn env).

Done-test: `windows-latest` job green (fix or mark the known spawn-env tests), Python 3.13 added.

## Phase 4 — polish

### P4.1 Multi-agent rooms — `done`

Evidence: `chip room`, personas and dispatch (`scripts/chip/cli.py`, `scripts/chip_relay/personas.py`,
`scripts/chip_relay/dispatch.py`); tests `scripts/test_chip_chat.py`, `scripts/test_chip_relay_personas.py`,
`scripts/test_chip_relay_orchestrator.py`.

### P4.2 Discord channel adapter — `partial`

Evidence: `scripts/chip_relay/listener.py`, `gateway.py` (webhook post), `notice_webhook.py`.

Gap: adapter is relay-specific; no transport-neutral channel interface for other adapters.

### P4.3 Thin UI after CLI green — `partial`

Evidence: `bin/chip-ui`, `scripts/chip_ui.py` (local, mock by default). Revisit after Phase 1 CLI lands.

### P4.4 Docs / quickstart — `partial`

Evidence: `README.md` quick start (mock). Gap: per-feature docs for new Phase 1–2 commands.

## Ranked order (next unblocked first)

1. ~~P1.1 routines + run history~~ (done)
2. ~~P1.2 memory remember/forget/scopes~~ (done; P1.2b turn injection later)
3. ~~P1.3 skills catalog~~ (done)
4. ~~P1.4 background agent jobs with hop/spend caps~~ (done; P1.4b slice 1 + P1.4c PM tick done; P1.4d summary/live enable later)
5. P1.5 MCP client + allowlist/approval levels
6. P3.3 Windows CI (cheap, protects everything after)
7. P2.1 web fetch/search
8. P2.2 files/attachments
9. P2.3 email draft→approve→send
10. P3.1 universal review hook
11. P3.2 hash-chained approval store + runner
12. P2.5 Pages, P2.4 browser, P4.x polish
