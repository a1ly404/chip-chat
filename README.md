# Chip Chat

Chip Chat is a **multi-agent Discord relay and chat engine** with pluggable **system packs** and **Python plugins**. It routes room traffic through OpenRouter (or deterministic mock mode), enforces spend gates, and posts persona replies via Discord webhooks.

Licensed under the [MIT License](LICENSE).

## Quick start (mock mode)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,relay]'

export CHIP_CHAT_MOCK=1
export CHIP_SYSTEM_PACK=acme-example
./bin/chip ping
./bin/chip room smoke -m 'Status?' --as pm --mock
```

Mock mode never calls OpenRouter or Discord; use it for local development and CI.

## System packs

A **pack** is a directory tree of JSON/Markdown configuration: personas, rooms, channel law, tools manifest, topology, case-intake surfaces, and related assets. The engine loads one pack at a time.

| Variable | Purpose |
|----------|---------|
| `CHIP_SYSTEM_PACK` | Pack id under `systems/<id>` (default in this repo: `acme-example`) |
| `CHIP_PACK_DIR` | Extra directory prepended to the pack search path |

The shipped **`acme-example`** pack is a minimal, safe default for tests and smoke runs. Production deployments typically point `CHIP_PACK_DIR` at a separate private pack repository (not shipped here).

### Writing a pack

1. Create `systems/<your-pack-id>/` with `system.json` (schema v0 surfaces).
2. Add `config/chip_chat/` (personas, channels, tools, lanes, …) and `config/chip_relay/` as needed.
3. Set `CHIP_SYSTEM_PACK=<your-pack-id>` or place the tree on `CHIP_PACK_DIR`.

Pack paths must stay inside allowed roots enforced by `chip.system_pack` (see `scripts/chip/system_pack.py`).

## Plugins

**Plugins** extend the engine with optional Python modules (monitor feeds, secret bootstrap, custom skills). Enable them with:

| Variable | Purpose |
|----------|---------|
| `CHIP_PLUGINS` | Comma-separated import paths or entrypoint names registered at startup |

Plugin code lives outside the core tree; the engine imports only what you list. See `systems/acme-example` for a small pack without operator-specific plugins.

## Configuration (environment names)

Common variables (values are host-specific; never commit `.env`):

- `OPENROUTER_CC_API_KEY` — OpenRouter API key for live completions
- `CHIP_CHAT_MOCK` / `--mock` — offline deterministic replies
- `CHIP_CHAT_DATA_DIR` — ledger, threads, job board
- `CHIP_SYSTEM_PACK`, `CHIP_PACK_DIR`, `CHIP_PLUGINS` — pack and plugin wiring
- Relay: `DISCORD_CHIP_RELAY_TOKEN`, `CHIP_WEBHOOK_DEV`, `CHIP_WEBHOOK_SELFTEST`, `CHIP_RELAY_AUTO`, inject/selftest author ids

Copy `.env.example` to `.env` and fill in values on your machine. Secret-store boot env names for private deployments are documented in the private plugin repository, not in this engine tree.

## CLI and relay

- `./bin/chip` — rooms, spend, tools, mock/live completions
- `./bin/chip-relay` — Discord gateway, inject, delegate handoffs

See `docs/CHIP_CHAT.md` and `docs/CHIP_RELAY.md` in this tree for flags and topology (PM hub, delegate hop cap).

## Contributing

See [AGENTS.md](AGENTS.md) for automated-agent and human contributor conventions.

## Host hooks and scheduling

Some duties (container-engine repair, watchdogs, stack restart after boot) must run on
the host, not in a container. The engine names them but does not implement them; see
[docs/host-hooks.md](docs/host-hooks.md) for the contract and
[plugins/example/host-hooks.yaml](plugins/example/host-hooks.yaml) for a sample manifest.
Recurring in-stack jobs use a scheduler crontab like
[docs/examples/scheduler.crontab](docs/examples/scheduler.crontab).
