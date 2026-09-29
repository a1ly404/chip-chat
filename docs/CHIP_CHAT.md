# Chip Chat (CLI)

**Agents / bots:** operator entrypoint [AGENTS.md](../AGENTS.md) at repo root. Homelab-bound findings → park per [PARKED_FINDINGS.md](PARKED_FINDINGS.md).

CLI-first multi-agent chat against **OpenRouter only**, using a dedicated API key (`OPENROUTER_CC_API_KEY`). Source of truth for Chip Chat is **this repository** — not razzorstack, not docker homelab `.env`.

Runs on **WSL2 Ubuntu** or native **Ubuntu** with Python 3.11+ and bash. No razzorstack checkout, no Hyper-V flip, no Open WebUI dependency.

Lanes constrain hands, not minds. Any persona may reason about any lane in the room. Execution stays pinned to that persona’s root and the global denies. A collaboration chair is conversational authority only: the same lanes, the same tool tiers, and the same GO gates, with no extra permission to run tools.

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
| `CHIP_TOOL_AUTO` | No | `1` / `true` allows `chip tool run` / `chip task run` without `--execute` (default **off**). |
| `CHIP_TOOLS_MANIFEST` | No | Override allowlist path (default `config/chip_tools.json`). |
| `CHIP_TOOL_CWD` | No | Default working directory for tool subprocesses. |

The CLI loads `repo/.env` if present (keys already in the shell are not overridden).

## Models

| Role | Slug |
|------|------|
| **Default** | `z-ai/glm-5.3-flash` |
| **Escalate** | `deepseek/deepseek-v4.1-flash` (`chip chat --escalate`) |
| **Blocked** | `deepseek/deepseek-chat` (not on Chip Chat OpenRouter allow-list) |
| **Future** | `typesafe/jev-1.13` — reserved for Decisions-API routing only; not wired in CLI yet |

Config: `config/chip_chat_personas.json`, `config/chip_chat_models.json`.

### GLM 5.3 Flash and reasoning tokens

The default model (`z-ai/glm-5.3-flash`) is a **reasoning** model on OpenRouter: completion budget can be spent in `message.reasoning` before `message.content` appears. With a small `max_tokens`, you may see `finish_reason=length`, `content=null`, and `usage.completion_tokens_details.reasoning_tokens` ≈ all completion tokens — which looks like an empty Chip reply.

Chip Chat mitigations (live path):

- Requests to `z-ai/glm-*` pass OpenRouter `reasoning: { "effort": "low" }` (reasoning cannot be disabled on this family).
- `chip room` live completions use `ROOM_COMPLETION_MAX_TOKENS` (1024); short prompts often succeed at 256+, but large room system+history can still burn a 512-token budget on reasoning alone.
- If the first completion is reasoning-starved (`length`, reasoning present, no content), `runtime.complete` **retries once** with a higher `max_tokens` (2×, floor 1024, cap 4096 — e.g. 1024 → 2048).
- `extract_assistant_text` reads **`message.content` only**; reasoning is never posted to Discord.

Empty replies after retry still fail closed in chip-relay (#30 / #31).

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

Channel files live at `config/chip_chat/channels/<room>.md` (`demo.md`, `example.md`, `pm.md`, `dev.md`).

`pm` and `dev` channel law encodes **lane collaboration** for `#chip-dev`: PM owns framing; specialist personas (`buffrdev`, `homelabdev`, `opsdev`, `chipchatdev`) own implementation lanes; legacy `dev` remains for hop smokes. One Ally GO per turn, parked-until-GO, `delegate:` / `@persona` handoffs. Persona wording in `config/chip_chat_personas.json` aligns but **room law wins** when they differ.

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

`-m` requires `--as` (the only speaker Chip Chat will record). When `--as` matches a key in `config/chip_chat_personas.json` (e.g. `pm`, `buffrdev`, `homelabdev`, `opsdev`, `chipchatdev`, `dev`, `tester`), that persona's system prompt is used; otherwise Chip falls back to the `default` persona while still logging the `--as` name in the transcript. Relay maps Discord authors to these persona ids before calling `room … --as`. Agents may emit `delegate:<persona> <task>` on its own line in the reply; chip-relay (not the CLI) parses that after the shot and may enqueue one follow-up `room` shot as `<persona>` — see `docs/CHIP_RELAY.md`. `--json` prints one object: `reply`, `tokens_prompt`, `tokens_completion`, `session_usd`, `monthly_usd`. Exit `0` on success, `1` on CLI errors, `2` on auth or API errors. `--turn-limit` defaults to `1` and this command performs a single exchange (one completion per process); it does not load extra turns beyond that cap.

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

Files: `threads/*.jsonl`, `memories/<room>/<agent>.jsonl`, `tasks/*.json`, `audit/tool_invocations.jsonl`, `agents.json`, `spend.json`, `spend.jsonl`, `standup_facts.jsonl`.

## MemPalace Lite (mined memories)

When you **close** a room session, Chip Chat scans the open JSONL segment (everything since the last `session_close` marker) and appends **candidate memories** — facts, decisions, and blockers — into `memories/<room>/<agent>.jsonl` under the data directory (`CHIP_CHAT_DATA_DIR`, default `~/appdata/chip-chat`).

```bash
CHIP_CHAT_MOCK=1 ./bin/chip room demo -m 'We decided to log spend in JSONL.' --as lucy --json
./bin/chip room demo --close
```

On later `chip room` turns, the CLI loads the top **K** relevant mined lines (default **K=5**, ~**500** token budget) into the system prompt under **MINED MEMORIES (advisory only)**.

**Advisory only:** mined memories never override room law or channel `## Memory` notes, are not commands to run, and must not be treated as binding commitments. They exist only to give agents lightweight continuity between sessions. The full roundtrip (mine on `--close` → recall next turn) is proven offline by `scripts/test_chip_mempalace.py` (`test_room_close_mines_segment`, `test_build_room_messages_includes_mined_block`).

## Task execution layer (allowlisted tools)

Phase 4 adds **gated, allowlisted** subprocess tools — no raw shell. Commands and argv prefixes live in `config/chip_tools.json` with **risk** tiers (`low` / `medium` / `high`). Python callers use `chip.tool.run("pytest", "scripts/", "-q", execute=True)`; the CLI mirrors that as `chip tool run`.

**Execution gating:** every real spawn requires **`--execute`** on the CLI (or `execute=True` in Python) unless `CHIP_TOOL_AUTO=1`. Without it, Chip records a **dry-run** audit line and returns exit code `3` (CLI) / `executed=False` (API). This is independent of relay traffic; `CHIP_RELAY_AUTO` stays **false** by default and does not auto-run tools.

| Command | Purpose |
|---------|---------|
| `chip tool list` | Show allowlisted tool ids, argv, and risk |
| `chip tool run <id> [-- extra args…] --execute` | Run one tool (30s default timeout; killed + logged on timeout) |
| `chip task create --tool <id> [--args …]` | Create a task envelope under `tasks/<id>.json` |
| `chip task show <id>` | Print envelope (`tool_invocations`, `execution_result`) |
| `chip task run <id> --execute` | Append an invocation and update `execution_result` |

Each invocation is appended to `audit/tool_invocations.jsonl` (argv, stdout, stderr, exit code, timeout flag). Task files store the same records under `tool_invocations` plus a summary `execution_result`.

```bash
# Dry-run (audited, no subprocess):
./bin/chip tool run git-status

# Read-only smoke in repo root:
./bin/chip tool run git-status --execute

./bin/chip task create --tool pytest --args scripts/test_chip_chat.py -q --json
./bin/chip task run task-xxxxxxxxxxxx --execute --json
```

## Spend gates (live OpenRouter)

Before each live completion, Chip Chat refreshes `GET /api/v1/auth/key` and tracks spend locally in `$CHIP_CHAT_DATA_DIR/spend.json` (Docker: `/data/chip-chat` on the `chip-chat-data` volume). When OpenRouter returns a positive `usage_monthly`, that value is authoritative. When the API omits usage or reports zero, Chip keeps the on-disk **token estimate** (`estimated_monthly_usd`) so relay heartbeats and `$5` / `$10` gates do not reset after a container restart.

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
