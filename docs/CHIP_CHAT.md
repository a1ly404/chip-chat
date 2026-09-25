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
| `CHIP_CHAT_ROOM_HISTORY_LIMIT` | No | Default prior transcript lines for `chip room` (default `10`). |
| `CHIP_CHAT_MEMORY_TOP_K` | No | Mined memories recalled per room turn (default `5`). |
| `CHIP_CHAT_MEMORY_TOKEN_BUDGET` | No | Approx token budget for mined memory block (default `500`). |

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
./bin/chip room demo -m 'Start test' --as lucy --json --mock
./bin/chip room demo --close
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
./bin/chip room demo -m 'Start test' --as lucy --json --mock
```

`-m` requires `--as` (the only speaker Chip Chat will record). When `--as` matches a key in `config/chip_chat_personas.json` (e.g. `pm`, `dev`, `tester`), that persona's system prompt is used; otherwise Chip falls back to the `default` persona while still logging the `--as` name in the transcript. Relay maps Discord authors to these persona ids before calling `room … --as`. Agents may emit `delegate:<persona> <task>` on its own line in the reply; chip-relay (not the CLI) parses that after the shot and may enqueue one follow-up `room` shot as `<persona>` — see `docs/CHIP_RELAY.md`. `--json` prints one object: `reply`, `tokens_prompt`, `tokens_completion`, `session_usd`, `monthly_usd`. Exit `0` on success, `1` on CLI errors, `2` on auth or API errors. `--turn-limit` defaults to `1` and this command performs a single exchange (one completion per process); it does not load extra turns beyond that cap.

Before each room turn, Chip Chat reads `threads/<room>.jsonl` and appends the last **N** user/assistant lines to the messages (after room law, before the new line). **N** defaults to **10** (`--history-limit` or `CHIP_CHAT_ROOM_HISTORY_LIMIT`). Assistant lines include a `[agent]` prefix when the transcript recorded one. Room law stays in the system prompt; history is conversational context only.

```bash
# Second shot sees the first exchange in context (mock, no key):
CHIP_CHAT_MOCK=1 ./bin/chip room demo -m 'First' --as lucy --json
CHIP_CHAT_MOCK=1 ./bin/chip room demo -m 'What did I just say?' --as mick --json
```

## Data path

1. `$CHIP_CHAT_DATA_DIR` if set  
2. Else `$HOME/appdata/chip-chat/`  
3. Else repo `var/chip-chat/` (CI)

Files: `threads/*.jsonl`, `memories/<room>/<agent>.jsonl`, `agents.json`, `spend.json`, `spend.jsonl`, `standup_facts.jsonl`.

## MemPalace Lite (mined memories)

When you **close** a room session, Chip Chat scans the open JSONL segment (everything since the last `session_close` marker) and appends **candidate memories** — facts, decisions, and blockers — into `memories/<room>/<agent>.jsonl` under the data directory (`CHIP_CHAT_DATA_DIR`, default `~/appdata/chip-chat`).

```bash
CHIP_CHAT_MOCK=1 ./bin/chip room demo -m 'We decided to log spend in JSONL.' --as lucy --json
./bin/chip room demo --close
```

On later `chip room` turns, the CLI loads the top **K** relevant mined lines (default **K=5**, ~**500** token budget) into the system prompt under **MINED MEMORIES (advisory only)**.

**Advisory only:** mined memories never override room law or channel `## Memory` notes, are not commands to run, and must not be treated as binding commitments. They exist only to give agents lightweight continuity between sessions.

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
