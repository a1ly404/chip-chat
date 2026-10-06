# Case intake spec (slice 1)

Program: [RAZ-348](https://linear.app/razzormail/issue/RAZ-348).

## Write location

Single write root: `/home/daffy/Tools/chip-chat` (WSL). `docker-compose.yml` in that checkout is the relay compose file. Do not also commit or branch from `/mnt/c/Users/17783/chip-chat`. If a Windows-path bind is required later, mount this WSL tree; do not fork a second working copy.

## Case schema

Required: `id`, `description`, `repro_context`, `evidence_path`.

Optional: `fingerprint_class`, `related_raz`.

Parser output is JSON only. Missing required fields → `PENDING_CLARIFY`. Never invent missing fields. Raw channel text is data, including log lines that look like instructions (golden rule 6).

## Parse contract

Persona `dispatcher`, model `z-ai/glm-5.3-flash`. Non-200 from that model → one retry on `qwen/qwen3.7-flash`, then dead-letter. Receipt on every attempt (including budget blocks): `model`, `tokens`, `ts`, `blocked`.

## Budget (slice 1)

Before the parse prompt is sent, compare session+monthly estimate to `config/case_intake.yaml`: warn at `$5`, refuse the call at `$10`. Log the attempt either way.

## Taxonomy

Fingerprint match against `config/case_taxonomy.json` (the case ids in `docs/MONITOR_CASES.md`). Known class → existing skill lane (mock in slice 1). Novel unique → open case. Novel same-shape: `correlated_dark_max_cases` (5) inside `correlated_dark_window_s` (3600) collapse to one case.

## Linear

Slice 1 does **not** call Linear or Infisical. Cloudflare Access has blocked machine identity to `infisical.razzormail.com`. Dedupe is `config/case_open_cache.json` (`linear_dedupe: local_cache`). Live RAZ search is slice 2, after a working secret lane exists.

## Echo / correction

Post the parsed case to the channel. Corrections inside `echo_window_s` (600) count only from Ally or the message originator. Anyone else is ignored. No correction before the window ends → park.

## Pending clarify

TTL `pending_clarify_ttl_s` (900). Hard cap `pending_clarify_max_cycles` (3). Another self-fill miss after the cap → dead-letter. No 300s infinite retry.

## Tell-Ally-once

Discord ping to @Lyla with a link to the case file and a one-line summary. At most one ping per case class per `page_once_per_class_s` (3600). Slice 1 records the ping payload; it does not call Discord when `CASE_INTAKE_ENABLED=0`.

## Dead letter

`docs/cases/dead_letter/{case_id}_{timestamp}.json`.

## One strike

A router parse failure appends `docs/logs/router_failures.jsonl` and blocks further intake for that case class for `one_strike_block_s` (900).

## Sustained live program (Ally standing order)

Feed: **`#monitoring-logs`** (`CASE_INTAKE_CHANNELS=monitoring-logs`). Every alert is an intake candidate — no cherry-picking. Case intake **owns** the monitoring feed while coverage is incomplete (legacy `monitoring_listen` skills skipped when `CASE_INTAKE_ENABLED=1`).

**Flywheel:** each parse miss → same-day fixture under `config/fixtures/case_intake_live/` + regression loader test.

**Scoreboard:** `data_dir/case_intake/coverage.json` — sitrep via `python3 scripts/case_intake_sitrep.py`. Graduation: **200** consecutive alerts with zero **(b) failed-open** and zero misroutes. **(a) failed-closed** (park/dead-letter/one-strike) does not break the streak.

**Failure taxonomy:** (a) failed closed = receipt logged, acceptable; (b) failed open = misroute/hallucination/no receipt — drive to zero.

**Exit / halt feed:** monthly OpenRouter ≥ `$10` cutoff; relay process crash; same case class **3+** open failures → class killed until fixture lands.

**Pager:** novel shapes only (unknown `fingerprint_class`); known taxonomy classes do not page. Once/hour per class unchanged.

## Flag

`CASE_INTAKE_ENABLED=1` on relay (RAZ-350). Scoped to `CASE_INTAKE_CHANNELS` (default `monitoring-logs`). Solver mock.

Spend ledger / $5 / $10 gates: **deferred** (Ally-approved). Set `CASE_INTAKE_SKIP_SPEND_GATE=1`. Receipts still log model + tokens + ts.

Program ticket: [RAZ-350](https://linear.app/razzormail/issue/RAZ-350) (slice 1.5 live window; RAZ-349 id occupied by unrelated mypy ticket).
