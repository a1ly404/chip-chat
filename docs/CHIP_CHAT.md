# Chip Chat (CLI)

CLI-first multi-agent chat against **OpenRouter only**, using a dedicated API key (`OPENROUTER_CC_API_KEY`). Source of truth for Chip Chat is **this repository** — not razzorstack, not docker homelab `.env`.

Runs on **WSL2 Ubuntu** or native **Ubuntu** with Python 3.11+ and bash. No razzorstack checkout, no Hyper-V flip, no Open WebUI dependency.

## Install (WSL / Ubuntu)

```bash
git clone https://github.com/a1ly404/chip-chat.git
cd chip-chat
chmod +x bin/chip
cp .env.example .env
# Edit .env and set OPENROUTER_CC_API_KEY=sk-or-v1-…
```

Optional: add repo root to `PATH` or symlink `bin/chip` into `~/bin`.

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENROUTER_CC_API_KEY` | Yes (live mode) | Chip Chat–only OpenRouter key (`sk-or-…`). **Never** commit. |
| `CHIP_CHAT_DATA_DIR` | No | Override data directory (default `~/appdata/chip-chat`). |
| `CHIP_CHAT_MOCK` | No | `1` / `true` for offline mock completions. |

The CLI loads `repo/.env` if present (keys already in the shell are not overridden).

## Models

| Role | Slug |
|------|------|
| **Default** | `z-ai/glm-5.3-flash` |
| **Escalate** | `deepseek/deepseek-v4.1-flash` (`chip chat --escalate`) |
| **Blocked** | `deepseek/deepseek-chat` (not on Chip Chat OpenRouter allow-list) |
| **Future** | `typesafe/jev-1.13` — reserved for Decisions-API routing only; not wired in CLI yet |

Config: `config/chip_chat_personas.json`, `config/chip_chat_models.json`.

## Commands

```bash
./bin/chip ping
./bin/chip ping --spend
./bin/chip chat
./bin/chip chat --once 'Reply with exactly: pong'
./bin/chip chat --escalate --once 'Harder question…'
./bin/chip agent spawn alice --persona planner
./bin/chip agent say alice "Outline demo steps"
./bin/chip room demo --demo
./bin/chip room example --once 'Say hello in one sentence.' --mock
./bin/chip spend
```

### Offline smoke (no key)

```bash
CHIP_CHAT_MOCK=1 ./bin/chip ping
CHIP_CHAT_MOCK=1 ./bin/chip chat --once 'Reply with exactly: pong'
CHIP_CHAT_MOCK=1 ./bin/chip room demo --demo
```

### Live smoke

```bash
export OPENROUTER_CC_API_KEY='sk-or-v1-…'
./bin/chip ping
./bin/chip room demo --demo
```

## Room law (Amendment 5)

Channel files live at `config/chip_chat/channels/<room>.md` (`demo.md`, `example.md`).

`chip room <name>` loads that file into the system prompt:

- `## Law` is **binding**. It overrides persona wording.
- `## Memory` is **advisory**. It must not override law.
- A file with neither heading is treated entirely as binding law.
- A room with no file still runs; the CLI prints `law=(none)`.

```bash
./bin/chip room demo --demo --mock
./bin/chip room example --once 'One sentence.' --mock
```

## Data path

1. `$CHIP_CHAT_DATA_DIR` if set  
2. Else `$HOME/appdata/chip-chat/`  
3. Else repo `var/chip-chat/` (CI)

Files: `threads/*.jsonl`, `agents.json`, `spend.json`, `spend.jsonl`, `standup_facts.jsonl`.

## Spend gates (live OpenRouter)

Before each live completion, Chip Chat refreshes `GET /api/v1/auth/key` and tracks spend locally.

| Threshold | Behavior |
|-----------|----------|
| **$5.00** monthly | `WARN` on stderr + standup fact; command still runs |
| **$10.00** monthly | **Refuse** live calls (`SpendCutoffError`). Mock/`--mock` still works |

```bash
./bin/chip spend
./bin/chip ping --spend
```

## Tests

```bash
pip install -e '.[dev]'
python3 -m pytest scripts/test_chip_chat.py -q
```

## Package layout

```
bin/chip                 # bash entrypoint
scripts/chip/              # Python package `chip`
config/chip_chat_personas.json
config/chip_chat_models.json
docs/CHIP_CHAT.md
```
