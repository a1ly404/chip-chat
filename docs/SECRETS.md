# Secrets (Chip Chat / relay)

**Never commit secret values.** Use Infisical or a local `.env` (gitignored) for development only.

## Infisical project `chipchat`

Production-oriented names live in Infisical project **`chipchat`** (prod), including:

- `OPENROUTER_CC_API_KEY` — Chip Chat OpenRouter key (live completions and `GET /api/v1/key` health)
- `DISCORD_CHIP_RELAY_TOKEN` — dedicated relay bot token
- `CHIP_WEBHOOK_DEV`, `CHIP_WEBHOOK_SELFTEST` — Discord webhook URLs for relay rooms

### Speaker maps (`config/chip_relay/rooms.json`)

Discord **user snowflake ids** and optional **handles** (username, lower-cased at runtime) map humans to Chip persona ids (`pm`, `dev`, `tester`, …). Replace every `TODO_*` placeholder in the committed file with real values from Discord (Developer Mode → copy user id). **Do not commit** webhook URLs or bot tokens while editing.

| Room (channel) | JSON path | Personas (examples) |
| --- | --- | --- |
| `#chip-dev` | `rooms[].id == "dev"` → `speakers` | `pm`, `dev` |
| `#chip-relay` | `rooms[].id == "relay"` → `speakers` | `pm` |

**Live / restart policy:** edit `config/chip_relay/rooms.json` on the Hyper-V host (or bake an updated file into the deploy artifact), then `docker compose restart chip-relay` (or restart the relay process). Startup and dispatch refuse to guess `--as` while a room still has only `TODO_*` speaker entries.

**Selftest (Layer 3)** — no forced `--as pm`. Set:

- `CHIP_RELAY_SELFTEST=1`
- `CHIP_RELAY_SELFTEST_AUTHOR_ID` — must match a key under `speakers.discord_ids` for the target room, **or**
- `CHIP_RELAY_SELFTEST_AUTHOR_HANDLE` — must match `speakers.handles` (after lower-casing)
- Optional: `CHIP_RELAY_SELFTEST_ROOM` (default `dev`), `CHIP_RELAY_SELFTEST_MESSAGE`

Access requires **Cloudflare Access** (`CF-Access-Client-Id`, `CF-Access-Client-Secret`) plus **machine identity** (`INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`) for the Infisical API. Relay startup documents the env var names in `docs/CHIP_RELAY.md`; values stay in Infisical or your private `.env`.

## Incident note (ghost-key class)

At one point, an Infisical-sourced `OPENROUTER_CC_API_KEY` failed OpenRouter key health while the same logical key in the repo **`.env`** passed — a **ghost-key** class mismatch (wrong/stale copy in the vault vs what operators used locally). **Verified 2026-09-25:** Infisical and `.env` both pass `GET /api/v1/key` with **no divergence**. If health fails after a rotation, reconcile Infisical and local `.env` before debugging application code.
