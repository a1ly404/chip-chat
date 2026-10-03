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
2. With `CHIP_RELAY_MONITORING_LOGS=1`, non-webhook bot posts are ingested (humans in that channel still use normal pm routing if added later).
3. **pm** matches a reusable skill from `config/chip_chat/monitoring_skills.json` (plus optional overlay under `$CHIP_CHAT_DATA_DIR/chip_state/monitoring_skills_overlay.json`).
4. Skill runs read-only: four-line receipt posted back to `#monitoring-logs` via `CHIP_WEBHOOK_MONITORING` with `username=pm`.
5. A short learning appends to `$CHIP_CHAT_DATA_DIR/chip_state/monitoring_skills/<skill_id>/learnings.jsonl` for the next match.

Unmatched alerts create an overlay skill stub (repeatable fingerprint) instead of silently dropping.

## Seeded skills

| Skill id | When it matches | Law |
| --- | --- | --- |
| `stale-gluetun-health-target` | Monitor still probes **gluetun** (404 / no container) | Live VPN is **wireguard**; 404 means **stale target** — never recreate gluetun |
| `qb-firewalled-not-success` | `qb-connection` / `firewalled` | Firewalled qBittorrent is **not** a green probe |

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
