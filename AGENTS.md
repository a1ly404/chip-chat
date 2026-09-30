# AGENTS.md — operator manual for automated agents

This file is for **non-human operators**: OpenCode, Go bots, Cursor Cloud Agents, Grok Bot automations, and shell CI. Humans can read it too, but the audience is agents that must run Chip Chat without tribal knowledge.

**Deeper references (prefer links over duplicating):**

| Topic | Doc |
| --- | --- |
| CLI, rooms, spend, tools, MemPalace | [docs/CHIP_CHAT.md](docs/CHIP_CHAT.md) |
| Relay, inject, delegate, webhooks | [docs/CHIP_RELAY.md](docs/CHIP_RELAY.md) |
| Rules 1–20 | [docs/OPERATING_LAW.md](docs/OPERATING_LAW.md) |
| Infisical / Discord ids | [docs/SECRETS.md](docs/SECRETS.md) |
| Hyper-V Docker relay | [docs/DOCKER.md](docs/DOCKER.md) |
| Phase history | [docs/SPRINT_LOG.md](docs/SPRINT_LOG.md) |

---

## 1. What Chip Chat is

**Chip Chat** is a **standalone**, **cheap** multi-agent system that talks to **OpenRouter only** via a project-local API key (`OPENROUTER_CC_API_KEY`). It is **not** razzorstack, not homelab Open WebUI, and not the Grok Razzorstack PM stack.

- **CLI:** `bin/chip` → Python package under `scripts/chip/` (rooms, spend gates, mock mode, allowlisted tools, MemPalace lite).
- **Discord lane:** `bin/chip-relay` → `scripts/chip_relay/` listens on `#chip-dev` (room id `dev` in `config/chip_relay/rooms.json`), posts persona replies via **one webhook URL per room** with `username=<persona>`.
- **Tracker:** Program tracking for Chip Chat work is **Linear issue RAZ-16** (comments only). Do not open parallel trackers in this repo unless Ally asks. After meaningful ships, post a one-line `learning:` comment on RAZ-16 (see §7).

Personas for `#chip-dev`: `pm`, specialist lanes `buffrdev`, `homelabdev`, `opsdev`, `chipchatdev`, plus legacy `dev` (hop smokes). Definitions: `config/chip_chat_personas.json`; channel law: `config/chip_chat/channels/pm.md`, `dev.md`.

---

## 2. Source-of-truth paths

| Surface | Path |
| --- | --- |
| **Git SoT** | [github.com/a1ly404/chip-chat](https://github.com/a1ly404/chip-chat) (this repo) |
| **Hyper-V deploy** | `C:\Users\17783\chip-chat` (Docker relay; see [docs/DOCKER.md](docs/DOCKER.md)) |
| **WSL dev checkout** | `~/Tools/chip-chat` (typical; any clone with `bin/chip` on PATH works) |
| **Discord** | `#chip-dev` via chip-relay; Ally human snowflake `187044496872046592` → persona `pm` ([docs/SECRETS.md](docs/SECRETS.md)) |

**Routing law for agents:** Chip Chat triage and GO traffic stay in this lane (`#chip-dev`, `chip-relay inject`, or `chip room`). **Never** route Chip Chat work through Grok Razzorstack **PM SendToAgent** or razzorstack docker paths.

### Repo boundaries (non-negotiable)

| Repo | SoT | WSL edits? | Commit location |
|------|-----|------------|-----------------|
| a1ly404/chip-chat | No SoT policy | Yes (`~/Tools/chip-chat` or any clone) | Same as edit location |
| a1ly404/razzorstack | Windows only ([PR #567](https://github.com/a1ly404/razzorstack/pull/567)) | No legacy WSL clone (`/home/daffy/Tools/razzorstack` — hooks block). `/mnt/c/.../HyperV-flip/...` passes hooks; **OCgo still must not use** | Windows SoT only (`C:\Users\17783\HyperV-flip\repos\razzorstack`) |
| Oracle / ops | Per-repo policy | Varies | Per-repo `AGENTS.md` |

**Failure-flow law:** Razzorstack-adjacent findings → **park + lane-flag**; never self-execute from chip-chat or OCgo.

1. **Primary:** Linear comment on the relevant ticket (`lane: razzorstack`, state what is blocked on).
2. **Secondary:** Receipt row in [docs/PARKED_FINDINGS.md](docs/PARKED_FINDINGS.md) (not Discord/Slack — those are not durable receipts).

**Lane assignments**

| Lane | Repo | Operators |
|------|------|-----------|
| Chip Chat | chip-chat (this repo) | OCgo, chip personas |
| Homelab / Cursor | razzorstack | Cursor agents on Windows SoT |
| Ops | Oracle / ops repos | opsdev; per-repo policy |

**OCgo:** Your deliverables land in **chip-chat** only. Do not open, edit, or commit in razzorstack from WSL or Windows.

---

## 3. Secrets

- **OpenRouter:** `OPENROUTER_CC_API_KEY` — **only** for Chip Chat / chip-relay. Load from Infisical project **`chipchat`** (prod) and/or repo **`.env`** (gitignored). See [docs/SECRETS.md](docs/SECRETS.md).
- **Do not** read or copy keys from **razzorstack** `docker/.env` or homelab vaults for this project.
- **Relay runtime:** `DISCORD_CHIP_RELAY_TOKEN`, `CHIP_WEBHOOK_DEV`, `CHIP_WEBHOOK_SELFTEST` — Infisical + boot `.env` (`INFISICAL_*`, `CF_ACCESS_*`). [docs/CHIP_RELAY.md](docs/CHIP_RELAY.md), [docs/DOCKER.md](docs/DOCKER.md).
- **Spend gates (live OpenRouter):** refresh `GET /api/v1/auth/key`; ledger under `CHIP_CHAT_DATA_DIR` (default `~/appdata/chip-chat`, Docker `/data/chip-chat`). **$5.00** monthly → WARN (still runs). **$10.00** → **refuse** live calls (`SpendCutoffError`); `--mock` / `CHIP_CHAT_MOCK=1` still works. Check `./bin/chip spend` or `./bin/chip ping --spend` before live relay selftest or inject without `--mock`. [docs/CHIP_CHAT.md](docs/CHIP_CHAT.md).

**Ghost-key class:** If Infisical and local `.env` disagree on OpenRouter health, reconcile vault vs `.env` before debugging app code ([docs/SECRETS.md](docs/SECRETS.md)).

---

## 4. How to talk to the room

### Humans and Ally in Discord

Type in `#chip-dev`. Relay maps your Discord id via `config/chip_relay/rooms.json` → `speakers` (Ally → `pm`). Unmapped humans fall back to the room’s default `as` field only when speaker maps are fully configured (placeholders `TODO_*` cause **startup failure** — [docs/SECRETS.md](docs/SECRETS.md)).

**Wake a persona** on the first line of a message ([docs/CHIP_RELAY.md](docs/CHIP_RELAY.md)):

```text
@chipchatdev fix the flake in scripts/test_chip_relay.py
chipchatdev: same task with persona: prefix
pm: frame the slice for Ally
```

### Inject without Discord (automation / CI)

`inject` loads **Infisical runtime secrets** the same way as `--listen` (`apply_runtime_secrets` in `scripts/chip_relay/startup.py`), then runs one `handle_message` turn:

```bash
# Mock — no OpenRouter spend; webhook URL still required in env for dispatch
export CHIP_WEBHOOK_DEV=https://example.invalid/dev
CHIP_CHAT_MOCK=1 ./bin/chip-relay inject --as-ally -m "GO: validation ping" --mock

# Live Ally GO (respects $5 WARN on inject; refuses at WARN+ for monthly_usd)
./bin/chip-relay inject --as-ally -m "GO: ship AGENTS.md" --room dev
```

`--as-ally` uses snowflake `187044496872046592` → `pm`. Alternative: `--as-author-id <snowflake>` or `CHIP_RELAY_INJECT_AUTHOR_ID`.

### Delegate handoffs (agent → agent)

Agents emit a **single line** in the reply:

```text
delegate:<persona> <task>
```

Example: `delegate:chipchatdev run pytest on relay delegate tests`. Relay posts the primary reply under the **source** persona’s webhook `username`, logs `relay_handoff` to `threads/<room>.jsonl`, then runs **one** follow-up `chip room` as `<persona>` with `<task>` as `-m`. Nested `delegate:` in the same dispatch completion is **not** chained; the next hop happens when that persona’s webhook posts `delegate:…` ([docs/CHIP_RELAY.md](docs/CHIP_RELAY.md)).

**Hop cap:** At most **3** nested delegate hops **per handoff chain** (resets on each **human or inject-bot** turn — not a lifetime room total). A fourth `delegate:` in the same chain is refused, logged, and relay posts in-channel: `[chip-relay] ERROR: delegate hop cap (3)` without calling chip ([docs/CHIP_RELAY.md](docs/CHIP_RELAY.md), `MAX_DELEGATE_HOPS` in `scripts/chip_relay/delegate.py`).

### Long messages and `message.txt`

Discord may split long posts; inline `content` can be empty or truncated. Gateway **`effective_gateway_content`** merges inline text with allowed attachments: `message.txt` or `text/plain`, UTF-8, ≤ 64 KiB (`scripts/chip_relay/routing.py`). Handoffs and `GO:` lines in the attachment body route like normal message text.

### Visible speakers (webhooks)

Outbound AI speech uses **one webhook URL** per room (`CHIP_WEBHOOK_DEV` etc.) with **`username=<persona>`** (`pm`, `buffrdev`, …). That is how multiple specialists appear as distinct speakers in the same channel ([docs/SECRETS.md](docs/SECRETS.md)).

---

## 5. CLI for non-Discord agents (OpenCode / Go / scripts)

Entrypoint: `./bin/chip` (sets `PYTHONPATH=scripts`). Full flag reference: [docs/CHIP_CHAT.md](docs/CHIP_CHAT.md).

### Room shot (one completion per process)

```bash
# Offline smoke — deterministic mock completions (incl. delegate: lines in mock)
CHIP_CHAT_MOCK=1 ./bin/chip room dev -m 'delegate:dev Reply hop-ok-dev only.' --as pm --json --mock

# Structured output for parsers
CHIP_CHAT_MOCK=1 ./bin/chip room dev -m 'Status?' --as chipchatdev --json --mock
# Fields: reply, tokens_prompt, tokens_completion, session_usd, monthly_usd
```

- `-m` **requires** `--as` (transcript speaker + persona system prompt when id exists in `config/chip_chat_personas.json`).
- Room law: `config/chip_chat/channels/<room>.md` (`## Law` binding; `## Memory` advisory).
- Shared transcript: `threads/<room>.jsonl` (last N lines, default 10).

### Mock mode

Use `CHIP_CHAT_MOCK=1`, `--mock`, or offline pytest. Mock understands `delegate:` and `hop-ok-*` patterns for relay hop tests (`scripts/chip/mock.py`, [docs/CHIP_CHAT.md](docs/CHIP_CHAT.md)).

### Task allowlist (do not over-assume)

`config/chip_tools.json` is **narrow** today: `pytest`, `git-status`, `task-show` only. There are **no** Buffr/homelab/fleet tools in the manifest. Real execution needs `chip tool run … --execute` or `CHIP_TOOL_AUTO=1` (default off). Relay does **not** auto-run tools (`CHIP_RELAY_AUTO` stays false). [docs/CHIP_CHAT.md](docs/CHIP_CHAT.md).

### MemPalace lite

On `chip room <room> --close`, the CLI mines advisory candidate memories into `memories/<room>/<agent>.jsonl`. Later turns recall top-K (~5, ~500 token budget) under **MINED MEMORIES (advisory only)** — never overrides room law. [docs/CHIP_CHAT.md](docs/CHIP_CHAT.md).

### Persona `learnings.md` (stubs)

Append-only files exist for some personas (e.g. `config/chip_chat/personas/pm/learnings.md`, `dev/learnings.md`) with header **“not loaded at runtime yet”**. Runtime loader is **backlog** ([docs/SPRINT_LOG.md](docs/SPRINT_LOG.md) — “Per-persona learnings”). Do not treat learnings files as binding prompt law until wired.

### Waking `chipchatdev` from OpenCode / CLI (no Discord)

Prefer explicit persona on the room shot:

```bash
CHIP_CHAT_MOCK=1 ./bin/chip room dev -m 'Your task envelope here' --as chipchatdev --json --mock
```

For Discord-visible work, use inject or human `@chipchatdev …` as in §4.

---

## 6. Deploy / Hyper-V hygiene

- **Preserve operator `.env`** on the deploy host; compose bind-mounts it read-only for boot creds ([docs/DOCKER.md](docs/DOCKER.md)). Never bake secrets into the image.
- **Stamp deploys:** write `BUILD_SHA.txt` at repo root with the git commit you deployed so operators can correlate running containers with git (operator convention; not read by chip-relay today).
- **Relay stuck in `Created`:** run `./scripts/chip_relay_up.sh` from the deploy directory ([docs/DOCKER.md](docs/DOCKER.md)).
- **Prefer WSL-native paths** for dev (`~/Tools/chip-chat`, `~/appdata/chip-chat`). Running compose against **UNC shares** from Windows into Hyper-V Docker is a common footgun — use the Hyper-V host clone (`C:\Users\17783\chip-chat`) for production relay.
- **Do not** remux media, mutate homelab libraries, or ship Buffr gym-app features from the Chip Chat lane unless Ally explicitly GOs that work to the right specialist persona ([docs/OPERATING_LAW.md](docs/OPERATING_LAW.md) rules 13, 7).

---

## 7. Token thrift for external agents

Follow [docs/OPERATING_LAW.md](docs/OPERATING_LAW.md) rule **2** and Spec D thrift (`scripts/chip/thrift.py`):

1. **Script** (grep, local pytest, `chip tool run` dry-run)
2. **GHA** / CI (`python3 -m pytest scripts/…`)
3. **Cheap agent** (`CHIP_CHAT_MOCK=1`, `--mock`)
4. **Live OpenRouter** only with Ally GO or a bounded acceptance envelope

Refuse open-ended digs (“until green”, “find root cause”) without Ally GO and a written stop condition (rule 15). Before re-deriving context, read RAZ-16 / ticket learnings (rule 18).

After a ship, post on **Linear RAZ-16**: `learning: <one line>`.

---

## 8. Out of scope

Unless Ally names a GO for that lane:

- Homelab **remux**, library file deletes, Plex/media-byte mutation (`homelabdev` charter only with GO)
- **Buffr** / razzorstack gym-app feature AutoDev (`buffrdev` charter)
- **Oracle** / fleet ops execution (`opsdev` charter)
- Inventing credentials, Discord tokens, or webhook URLs
- Impersonating Ally in external channels (rule 17)

---

## 9. Quick copy-paste recipes

**Mock delegate hop (CLI only):**

```bash
CHIP_CHAT_MOCK=1 ./bin/chip room dev -m 'delegate:dev Reply hop-ok-dev only.' --as pm --json --mock
```

**Live inject — Ally GO (check spend first):**

```bash
./bin/chip spend
./bin/chip-relay inject --as-ally -m "GO: @chipchatdev document the relay health port" --room dev
```

**Room shot JSON for automation parsers:**

```bash
CHIP_CHAT_MOCK=1 ./bin/chip room dev -m 'ping' --as chipchatdev --json --mock
```

**Relay health (inside Docker container):**

```bash
docker compose exec chip-relay python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:48082/health').read())"
```

**CI-style relay tests (no Discord):**

```bash
pip install -e '.[dev]'
python3 -m pytest scripts/test_chip_relay_delegate.py scripts/test_chip_relay_gateway.py -q
```

---

## Single writable checkout — agents (RAZ-330+, permanent)

**Mirrors razzorstack Windows-SoT discipline:** one place may receive git writes; every other checkout is read-only.

| Role | Path | Git writes |
|------|------|------------|
| **Main clone (WSL)** | `~/Tools/chip-chat` with **`main` checked out** | **Forbidden** — `git pull` / read / reference only |
| **Agent worktree** | `~/Tools/chip-chat-worktrees/<ticket-or-branch>` | **Required** for all chip-chat code, docs, and commits |
| **Hyper-V deploy tree** | `C:\Users\17783\chip-chat` | Deploy lane only; not agent edit SoT unless ticket says so |

### Rules (Cursor, OCgo, cloud agents)

1. **Never** edit, `git add`, `git commit`, or `git push` from `~/Tools/chip-chat` when `main` is checked out.
2. **Always** create or reuse a worktree: `git worktree add ~/Tools/chip-chat-worktrees/raz-XXX -b raz-XXX-short-description origin/main` (from the main clone, read-only ops only).
3. Branch name should match the Linear ticket or agreed branch slug; one active write worktree per ticket slice.
4. If the task cannot be scoped to a worktree (e.g. no disk path, policy conflict), **stop** and comment on the Linear ticket — do not fall back to editing `main`.
5. Pre-commit from a worktree is fine; verify with `git rev-parse --show-toplevel` — must **not** be `~/Tools/chip-chat` for agent commits.

**Violation recovery:** `git status` on main must stay clean; park accidental main edits via stash/patch into the correct worktree branch before push.

**SoT doc cross-ref:** razzorstack [DEVELOPMENT.md](https://github.com/a1ly404/razzorstack/blob/main/docs/setup/DEVELOPMENT.md#single-writable-clone-raz-237) (Windows SoT); this section is the chip-chat equivalent.


**Razzorstack (homelab) — separate repo:** Chip Chat deliverables stay in **chip-chat** (WSL worktrees above). **OCgo, chipchatdev, and Cursor** may edit **razzorstack** on **Windows** via `C:\Users\17783\HyperV-flip\repos\razzorstack-worktrees\<branch>` (or `/mnt/c/...` same path). WSL `~/Tools/razzorstack` remains **read-only**; do not commit homelab changes from the chip-chat repo tree.
