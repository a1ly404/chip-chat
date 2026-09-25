# Secrets (Chip Chat / relay)

**Never commit secret values.** Use Infisical or a local `.env` (gitignored) for development only.

## Infisical project `chipchat`

Production-oriented names live in Infisical project **`chipchat`** (prod), including:

- `OPENROUTER_CC_API_KEY` — Chip Chat OpenRouter key (live completions and `GET /api/v1/key` health)
- `DISCORD_CHIP_RELAY_TOKEN` — dedicated relay bot token
- `CHIP_WEBHOOK_DEV`, `CHIP_WEBHOOK_SELFTEST` — Discord webhook URLs for relay rooms

Access requires **Cloudflare Access** (`CF-Access-Client-Id`, `CF-Access-Client-Secret`) plus **machine identity** (`INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`) for the Infisical API. Relay startup documents the env var names in `docs/CHIP_RELAY.md`; values stay in Infisical or your private `.env`.

## Incident note (ghost-key class)

At one point, an Infisical-sourced `OPENROUTER_CC_API_KEY` failed OpenRouter key health while the same logical key in the repo **`.env`** passed — a **ghost-key** class mismatch (wrong/stale copy in the vault vs what operators used locally). **Verified 2026-09-25:** Infisical and `.env` both pass `GET /api/v1/key` with **no divergence**. If health fails after a rotation, reconcile Infisical and local `.env` before debugging application code.
