# #monitoring-logs — relay listen + pm skills

**Status:** code on `main` after Ally GO; **live ingest requires deploy flag** `CHIP_RELAY_MONITORING_LOGS=1`.

## What is configured by default

| Piece | Default | Notes |
| --- | --- | --- |
| `config/chip_relay/rooms.json` room `monitoring-logs` | **present** | Channel id `1480439800350769252`, outbound `CHIP_WEBHOOK_MONITORING`, persona `pm` |
| `CHIP_RELAY_MONITORING_LOGS` | **off** (`0`) | Flip to `1` on the Hyper-V relay host to ingest bot alerts |
| `CHIP_WEBHOOK_MONITORING` | required when flag on | Same webhook tier as standup/monitor-watchdog posts; **not** loaded for standup cron tokens |
| OpenRouter / pm skill runner | **no LLM spend** | Deterministic skill match + four-line receipt |
| `CHIP_RELAY_PM_ORCHESTRATOR` | **unchanged (off)** | Monitoring path is not the job-board tick |
| `CHIP_FEED_QB_URL` | **unchanged (unset)** | No qBittorrent password on the listen process |

Infisical runtime fetch for `--listen` stays **Discord + OpenRouter only** (`scripts/chip_relay/infisical_rest.py`). Set `CHIP_WEBHOOK_MONITORING` in deploy `.env` or extend Infisical separately — do not add `CHIP_STANDUP_GITHUB_TOKEN`, `LINEAR_API_KEY`, or `CHIP_ALERT_INGEST_TOKEN` to the listen boot path.

## Flow

1. Discord bot is a member of `#monitoring-logs` (channel id in `rooms.json`).
2. With `CHIP_RELAY_MONITORING_LOGS=1`, **webhook alerts** (Monitoring Bot, Razzorstack Monitor), **embed-only bot posts** (Razzorstack Bot), and plain bot text are ingested. **pm webhook receipts** and chip-relay `CreateMessage` are ignored (no loop).
3. **Universal outage soak (10 minutes):** every skill records the **first sighting**, appends learnings, and runs its configured `fix_action` only after the same alert class has stayed down for **≥ `soak_seconds`** (default 600). Shorter blips stay `status: blocked` with `evidence: tool:monitoring-outage.watch`.
4. **pm** matches a skill from `config/chip_chat/monitoring_skills.json` (plus overlay at `$CHIP_CHAT_DATA_DIR/chip_state/monitoring_skills_overlay.json`). New classes get `watch_only` + learnings until Ally adds a `fix_action` — **no Python change required**.
5. **`fix_action` kinds** (declarative): `record_retired_target`, `verify_source_contains`, `docker_restart_if_down` (policy allowlist only — **never gluetun**), `homelab_delegate`, `manual_note` (operator step only — no restart/reauth), `watch_only`.
6. Four-line receipt posts to `#monitoring-logs` via `CHIP_WEBHOOK_MONITORING` (`username=pm`). Learnings append under `chip_state/monitoring_skills/<id>/learnings.jsonl`.

Unmatched alerts create an overlay skill stub (repeatable fingerprint) instead of silently dropping — **except** lines matched by registry `ignore_any` (status posts, tickers, heartbeats). Those are dropped with **no** overlay, soak, or Discord receipt.

## Seeded skills (catalog 2026-09-19..2026-10-02 PT)

All rows live in `config/chip_chat/monitoring_skills.json` (history catalog). Regression samples: `scripts/fixtures/monitoring_skill_catalog.json`. Match priority follows **file order** (first `match_any` hit wins).

**Not seeded:** LazyLibrarian (not in use); media-workflow ticker, Nancy reclaim, PGS embed **morning report** status posts only (PGS embed job failures are not `ignore_any`), old port-sync notes, RAZ status posts, retired Jellyfin/Jellyseerr/Calibre-Web, Lidarr instant recovery, chip-relay self-test.

**Gluetun / AirVPN labels:** no gluetun container — stale probe retirement only (`record_retired_target`), never restart/health-check gluetun.

**lumocheck / lumo-tamer timeout:** `manual_note` only after soak (Ally manual login — no auto restart or reauth).

Add or tune rows by editing `monitoring_skills.json` or the overlay file (`match_any` + `fix_action` + optional `followup`) — no Python change required. Add benign noise classes under top-level `ignore_any` (same substring/regex rules as `match_any`).

Bodies live under `config/chip_chat/monitoring_skills/*.md`.

## Hard refuses (tests enforce)

- Never post monitoring receipts to **#plex-fix** (channel id block + forbidden webhook env names).
- Skill receipts must not instruct **gluetun / wireguard / qbittorrent** container restarts or `compose up` from this path.

## Spend gate

Before handling an alert, relay reads the monthly ledger. At **$10** monthly (`SpendCutoffError` class), monitoring ingest refuses live handling (same family as gateway startup WARN at $5).

## Deploy checklist

1. Ensure the relay Discord app can read `#monitoring-logs`.
2. Set `CHIP_WEBHOOK_MONITORING` to the monitoring channel webhook URL.
3. Set `CHIP_RELAY_MONITORING_LOGS=1` and restart `chip-relay`.
4. Post a test bot line containing `gluetun` + `404`; expect one `pm` receipt and a learning row under `chip_state/monitoring_skills/`.

## Tests

```bash
python3 -m pytest scripts/test_chip_relay_monitoring_listen.py -q
```
