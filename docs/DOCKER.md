# Chip relay — Docker (Hyper-V)

Container image runs `bin/chip-relay --listen` (live Discord gateway). No WSL, no host bind-mount of operator toolchains, and **no secrets baked into the image**.

## Build

From the repo root:

```bash
docker build -t chip-relay .
```

## Data directory

Chip Chat ledger and room threads use `CHIP_CHAT_DATA_DIR` when set; otherwise `$HOME/appdata/chip-chat`. The image sets:

```text
CHIP_CHAT_DATA_DIR=/data/chip-chat
```

`docker-compose.yml` mounts the named volume `chip-chat-data` at that path so transcripts and spend survive restarts.

## Environment

1. Copy `.env.example` to `.env` on the host (never commit `.env`).
2. Compose bind-mounts `./.env` read-only at `/app/.env` for **boot** credentials only:
   - `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`
   - `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`
   - Optional: `INFISICAL_API_URL`, `INFISICAL_PROJECT_ID`
   - Optional fallback: `OPENROUTER_CC_API_KEY` if Infisical key health fails (see `startup.py`)

On `--listen`, the relay fetches runtime secrets from Infisical (prod `chipchat` project): `DISCORD_CHIP_RELAY_TOKEN`, `CHIP_WEBHOOK_DEV`, `CHIP_WEBHOOK_SELFTEST`, and OpenRouter key. Heartbeats post to the channel configured in `config/chip_relay/rooms.json` (`heartbeat_channel_id`); no extra Discord channels are created in Phase 1.

Room webhook env names must match Infisical (`CHIP_WEBHOOK_*`). See `docs/CHIP_RELAY.md` and `docs/SECRETS.md`.

## Run with Compose

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f chip-relay
```

No host ports are published. Health is checked **inside** the container on `127.0.0.1:48082/health` (override with `CHIP_RELAY_HEALTH_PORT`).

- `503` + body `starting` until the Discord gateway fires `on_ready`
- `200` + body `ok` when the gateway is ready

Manual check from the host (exec into the container):

```bash
docker compose exec chip-relay python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:48082/health').read())"
```

## Restart policy

`restart: unless-stopped` keeps the relay up across Docker daemon restarts on Hyper-V.

## Spend ledger (same volume)

`spend.json` and `spend.jsonl` live under `CHIP_CHAT_DATA_DIR` (the `chip-chat-data` volume in compose). Relay heartbeats and `./bin/chip spend` read that path. After a container restart, `monthly_usd` should not drop to zero unless OpenRouter reports a positive `usage_monthly` or the ledger file was lost (wrong data dir). Local token estimates are kept when the API returns zero or omits usage.

## Speaker maps

Human Discord ids for `#chip-dev` / `#chip-relay` are in `config/chip_relay/rooms.json` (see `docs/SECRETS.md`). Rebuild or pull an image that includes an updated `rooms.json`, or bind-mount the file on the host, then `docker compose restart chip-relay`.
