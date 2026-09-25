# Chip Chat

Cheap multi-agent chat CLI for **WSL2 Ubuntu** and native **Ubuntu**. Talks to **OpenRouter** with a dedicated key — no razzorstack, no homelab docker, no Open WebUI.

**Source of truth:** this repo (`a1ly404/chip-chat`).

## Quick start

```bash
git clone https://github.com/a1ly404/chip-chat.git
cd chip-chat
chmod +x bin/chip
cp .env.example .env   # set OPENROUTER_CC_API_KEY
CHIP_CHAT_MOCK=1 ./bin/chip ping
```

## Requirements

- Linux (WSL2 or Ubuntu)
- Python **3.11+**
- `OPENROUTER_CC_API_KEY` in `.env` or environment (see `.env.example`)

## Commands

| Command | Description |
|---------|-------------|
| `chip ping` | Auth key check + tiny completion |
| `chip chat --once '…'` | Single-turn chat |
| `chip chat --escalate --once '…'` | Uses escalate model |
| `chip room demo --demo` | Planner → builder handoff demo |
| `chip agent spawn / say` | Named agents |
| `chip spend` | Spend ledger + thresholds |

Offline: `CHIP_CHAT_MOCK=1` or `--mock` on any command.

## Models

- **Default:** `z-ai/glm-5.3-flash`
- **Escalate:** `deepseek/deepseek-v4.1-flash` (`--escalate`)
- **Do not use:** `deepseek/deepseek-chat` (blocked — allow-list)
- **Future router:** `typesafe/jev-1.13` (Decisions API only; not in CLI yet)

## Spend safety

Live calls refresh OpenRouter usage and enforce:

- **$5** — WARN (stderr + `standup_facts.jsonl`)
- **$10** — cutoff (live calls refused; mock still OK)

Ledger under `~/appdata/chip-chat/` (or `CHIP_CHAT_DATA_DIR`). Docker relay: `docs/DOCKER.md`.

## Local UI (Path A)

The UI does not talk to OpenRouter. It lists rooms, shows the last turns from `~/appdata/chip-chat/threads/<room>.jsonl`, and sends one message with `./bin/chip room <room> --once`. Design: [docs/UI_PATH_A.md](docs/UI_PATH_A.md).

**WSL2:** run the server inside your Ubuntu distro, then open `http://127.0.0.1:8765/` in your Windows browser (default WSL2 forwards localhost).

```bash
chmod +x bin/chip-ui

# offline (default): passes --mock
./bin/chip-ui
# or: python3 scripts/chip_ui.py

# one live turn per Send (uses OPENROUTER_CC_API_KEY; still under the $5/$10 gates)
CHIP_CHAT_UI_LIVE=1 ./bin/chip-ui
```

## Docs & tests

- Full reference: [docs/CHIP_CHAT.md](docs/CHIP_CHAT.md)
- Relay skeleton (no Discord connection): [docs/CHIP_RELAY.md](docs/CHIP_RELAY.md)
- Tests: `pip install -e '.[dev]'` then `python3 -m pytest scripts/test_chip_chat.py -q`

## Live smoke checklist

```bash
export OPENROUTER_CC_API_KEY='sk-or-v1-…'
./bin/chip ping
./bin/chip chat --once 'Reply with exactly: pong'
./bin/chip room demo --demo
./bin/chip spend
```
