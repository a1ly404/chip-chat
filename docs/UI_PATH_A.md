# Path A — Local thin UI (Freeze Phase 5)

Path A is a **localhost-only** web UI that never calls OpenRouter itself. It lists Chip Chat rooms, shows recent turns from on-disk JSONL transcripts, and sends one user message per submit by shelling **`./bin/chip room <room> --once`**.

This keeps spend gates, mock mode, room law, and MemPalace behavior identical to the CLI.

## Goals

| Goal | How |
| --- | --- |
| Room selection | Channel names from `config/chip_chat/channels/*.md` |
| Transcript view | Tail of `~/appdata/chip-chat/threads/<room>.jsonl` (or `CHIP_CHAT_DATA_DIR`) |
| Send message | Subprocess: `bin/chip room <room> --once '<text>'` |
| No new secrets surface | UI binds `127.0.0.1` only; no Cloudflare, no Discord relay |
| Safe default | Unless `CHIP_CHAT_UI_LIVE=1`, every send appends `--mock` |

## Non-goals (Phase 5)

- Discord live deploy or relay wiring
- razzorstack / homelab integration
- Inventing a `dev` Discord speaker id (relay maps stay in `config/chip_relay/rooms.json`)
- Auth, TLS, or multi-user hosting

## Architecture

```text
Browser  →  http://127.0.0.1:8765/  →  scripts/chip_ui.py (stdlib HTTP)
                                              │
                                              ├─ GET /api/rooms     → scan channel *.md
                                              ├─ GET /api/turns     → read thread JSONL
                                              └─ POST /api/send     → subprocess bin/chip
```

Implementation choice: **stdlib `ThreadingHTTPServer`** instead of FastAPI to avoid new runtime dependencies in CI. The API shape matches what a future FastAPI port would expose (`/api/rooms`, `/api/turns`, `/api/send`).

## Run locally (WSL2 / Ubuntu)

From the repo root:

```bash
chmod +x bin/chip bin/chip-ui
cp .env.example .env   # optional for live sends

# Offline (default): each Send uses --mock
./bin/chip-ui
# Open http://127.0.0.1:8765/

# Live OpenRouter (still subject to $5 warn / $10 cutoff in the CLI)
CHIP_CHAT_UI_LIVE=1 ./bin/chip-ui
```

Equivalent without the launcher:

```bash
python3 scripts/chip_ui.py
```

### WSL notes

- Use **WSL2** with Python 3.11+ (`python3 --version`).
- Open the URL in your **Windows browser**; `127.0.0.1:8765` on the WSL side forwards to Windows localhost for default WSL2 networking.
- Keep data under WSL home (`~/appdata/chip-chat`) so CLI and UI share the same transcripts.
- For live sends, set `OPENROUTER_CC_API_KEY` in `.env` or the shell before `CHIP_CHAT_UI_LIVE=1`.

## Environment

| Variable | Purpose |
| --- | --- |
| `CHIP_CHAT_DATA_DIR` | Shared data root (threads, spend ledger) |
| `CHIP_CHAT_UI_LIVE` | `1` / `true` / `yes` → omit `--mock` on send |
| `OPENROUTER_CC_API_KEY` | Required only when `CHIP_CHAT_UI_LIVE` is set |

## HTTP API (local)

| Method | Path | Response |
| --- | --- | --- |
| `GET` | `/` | Single-page HTML form |
| `GET` | `/api/rooms` | `{"rooms": ["demo", …]}` |
| `GET` | `/api/turns?room=demo&n=12` | `{"room": "demo", "turns": [{ts, role, agent, content}, …]}` |
| `POST` | `/api/send` | JSON body `{"room": "…", "text": "…"}` → CLI result + refreshed turns |

Errors from the CLI surface as HTTP 502 with `stderr` in the JSON body.

## Tests

Offline coverage in `scripts/test_chip_chat.py::test_ui_lists_turns_and_mock_send` imports `chip_ui` helpers and asserts mock subprocess behavior.

## Future (out of Phase 5 scope)

- Optional FastAPI package extra with the same routes (if we want OpenAPI / async)
- TUI (`textual` or `curses`) calling the same subprocess helpers
- Room `--as` / persona picker in the form
