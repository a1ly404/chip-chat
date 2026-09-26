# Secrets (Chip Chat / relay)

**Never commit secret values.** Use Infisical or a local `.env` (gitignored) for development only.

## Infisical project `chipchat`

Production-oriented names live in Infisical project **`chipchat`** (prod), including:

- `OPENROUTER_CC_API_KEY` — Chip Chat OpenRouter key (live completions and `GET /api/v1/key` health)
- `DISCORD_CHIP_RELAY_TOKEN` — dedicated relay bot token
- `CHIP_WEBHOOK_DEV`, `CHIP_WEBHOOK_SELFTEST` — Discord webhook URLs for relay rooms

### Speaker maps (`config/chip_relay/rooms.json`)

**Humans** typing in Discord are mapped via `speakers` (snowflake ids and optional handles → persona id). **AI personas** (`pm`, `dev`, …) speak outbound through **Discord webhooks** with a webhook `username`; they do not need a human snowflake. The `dev` persona is AI-only (webhook + `delegate:dev` hops); there is no human “dev” Discord account in the map.

| Room (channel) | JSON path | Human speakers (examples) |
| --- | --- | --- |
| `#chip-dev` | `rooms[].id == "dev"` → `speakers` | Ally → `pm` |
| `#chip-relay` | `rooms[].id == "relay"` → `speakers` | Ally → `pm` |
| `#chip-selftest` | `rooms[].id == "selftest"` → `speakers` | `selftest` bot (Layer 3) |

**Known Discord ids (2026-09-26):**

- Ally (human): `187044496872046592` → `pm` on `#chip-dev` and `#chip-relay`
- `pm` bot user id `1553148704494714890` → `pm` (bot-authored messages in those channels)
- `chip-selftest` bot `1553182793041838141` → `selftest` on `#chip-selftest`

**Live / restart policy:** edit `config/chip_relay/rooms.json` on the Hyper-V host (or bake an updated file into the deploy artifact), then `docker compose restart chip-relay` (or restart the relay process). Startup and dispatch refuse to guess `--as` while a room declares `speakers` but only `TODO_*` placeholders remain.

**Selftest (Layer 3)** — no forced `--as pm`. Set:

- `CHIP_RELAY_SELFTEST=1`
- `CHIP_RELAY_SELFTEST_AUTHOR_ID` — must match a key under `speakers.discord_ids` for the target room, **or**
- `CHIP_RELAY_SELFTEST_AUTHOR_HANDLE` — must match `speakers.handles` (after lower-casing)
- Recommended defaults for Layer 3: `CHIP_RELAY_SELFTEST_ROOM=selftest`, `CHIP_RELAY_SELFTEST_AUTHOR_ID=1553182793041838141` (maps to persona `selftest` on `#chip-selftest`). Alternative author: Ally human id `187044496872046592` when testing a room where that id is under `speakers.discord_ids`.
- Optional: `CHIP_RELAY_SELFTEST_ROOM` (code default `dev` if unset), `CHIP_RELAY_SELFTEST_MESSAGE`

Access requires **Cloudflare Access** (`CF-Access-Client-Id`, `CF-Access-Client-Secret`) plus **machine identity** (`INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`) for the Infisical API. Relay startup documents the env var names in `docs/CHIP_RELAY.md`; values stay in Infisical or your private `.env`.

## Incident note (ghost-key class)

At one point, an Infisical-sourced `OPENROUTER_CC_API_KEY` failed OpenRouter key health while the same logical key in the repo **`.env`** passed — a **ghost-key** class mismatch (wrong/stale copy in the vault vs what operators used locally). **Verified 2026-09-25:** Infisical and `.env` both pass `GET /api/v1/key` with **no divergence**. If health fails after a rotation, reconcile Infisical and local `.env` before debugging application code.
