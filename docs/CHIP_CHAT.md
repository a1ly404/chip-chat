# Chip Chat (CLI)

**Agents / bots:** see [AGENTS.md](../AGENTS.md) at repo root.

CLI-first multi-agent chat against **OpenRouter only**, using a dedicated API key (`OPENROUTER_CC_API_KEY`). This repository is the Chip Chat engine — install it as a package and point `CHIP_SYSTEM_PACK` / `CHIP_PACK_DIR` at your deployment pack.

Runs on Linux or macOS with Python 3.11+ and bash.

Lanes constrain hands, not minds. Any persona may reason about any lane in the room. Execution stays pinned to that persona’s root and the global denies.

**Topology (PM hub):** the human operator talks **only to `pm`**; PM posts the GO receipt as `pm`. Specialists are **charters + path denies**, not a second conversation. `delegate:` is only for cross-charter file ownership — one hop at a time, cap 3; specialists post receipts on the same room webhook with a different `username`, then stop (no agent↔agent chains). Config: pack `topology` surface; enforcement: `scripts/chip/topology.py`.

## Install

```bash
git clone https://github.com/example-org/chip-chat.git
cd chip-chat
chmod +x bin/chip
cp .env.example .env
# Set OPENROUTER_CC_API_KEY in .env (never commit)
pip install -e '.[dev,relay]'
export CHIP_SYSTEM_PACK=acme-example
```

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENROUTER_CC_API_KEY` | Yes (live mode) | OpenRouter key for Chip Chat only. |
| `CHIP_CHAT_DATA_DIR` | No | Ledger, threads, job board (default from active pack, e.g. `~/.local/share/chip-chat`). |
| `CHIP_CHAT_MOCK` | No | Offline mock completions. |
| `CHIP_SYSTEM_PACK` / `CHIP_PACK_DIR` | No | System pack selection (default example: `acme-example`). |
| `CHIP_TOOL_AUTO` | No | Allow tool execution without `--execute` (default off). |

## Models

Default and escalate slugs come from the active pack (`config/chip_chat_models.json` under the pack). Case-intake escalation may use a separate escalate slug when enabled in pack config.

See `docs/CHIP_RELAY.md` for Discord relay flags.
