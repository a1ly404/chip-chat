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

## Flag

`CASE_INTAKE_ENABLED=0` in compose. `1` runs intake before the normal chip shot and stops the shot (mock solver only).
