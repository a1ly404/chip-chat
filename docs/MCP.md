# MCP client (stdio)

Chip can call tools on [Model Context Protocol](https://modelcontextprotocol.io) servers that run as
local subprocesses (stdio transport, newline-delimited JSON-RPC 2.0). Off by default: set `CHIP_MCP=1`.

Code: `scripts/chip/mcp_client.py`. Tests: `scripts/test_chip_mcp.py` (spawns the in-repo fake server
`scripts/fixtures/mcp_fake_server.py`).

## Config

`$CHIP_CHAT_DATA_DIR/mcp/servers.json` (or `CHIP_MCP_CONFIG=/path/servers.json`):

```json
{
  "servers": {
    "notes": {
      "command": ["python3", "-m", "notes_mcp"],
      "env": {"NOTES_TOKEN": "$NOTES_TOKEN"},
      "timeout_s": 15,
      "tools": {"search": "auto", "create_note": "ask", "delete_note": "deny"}
    }
  }
}
```

- `command` is an argv list; no shell.
- `env`: the server gets only `PATH`, `HOME`, `LANG` (plus Windows temp/system vars) and this block.
  `"$NAME"` copies `NAME` from Chip's environment at spawn time, so secrets stay out of the file.
  An unset reference fails the call.
- `timeout_s` per request, clamped to 0.1–120 s.
- `tools` is the allowlist. Anything not listed is refused before the server is started.

## Approval levels

| level | behaviour |
| --- | --- |
| `auto` | runs, audited |
| `ask` | records a pending approval and returns `status: pending` (CLI exit 4); does not run |
| `deny` | refused, audited |
| anything else | treated as `deny` |

An `ask` approval is bound to the exact server, tool and argument hash, must be approved by someone
other than the requester, and is single use.

A tool must also be advertised by the server's `tools/list`; an allowlisted tool the server does not
offer is refused.

## Fail closed

Non-JSON stdout, non-JSON-RPC messages, a missing `initialize` protocol version, a malformed
`tools/list` / `tools/call` result, an oversized line, a server exit, a missing binary or a timeout
all raise and are audited as `error`. Server notifications are ignored; server-to-client requests
(sampling, roots, ...) are answered with "not supported". Results over 20 000 characters are clipped
to their text content.

## Audit

`$CHIP_CHAT_DATA_DIR/mcp/audit.jsonl` gets one row per decision: `ok`, `error`, `refused`, `pending`,
`approved`, `rejected`. Rows carry `args_sha256`, never the arguments. Approval state lives in
`mcp/approvals.jsonl` (append-only).

## CLI

```bash
chip mcp servers                    # config + policy (works with the flag off)
chip mcp tools notes                # advertised tools with policy level
chip mcp call notes search --args '{"q": "deploy"}' --by pm
chip mcp call notes create_note --args '{"title": "x"}' --by dev   # -> pending, exit 4
chip mcp pending
chip mcp approve mcpa-0123abcd4567 --by operator
chip mcp call notes create_note --args '{"title": "x"}' --by dev --approval mcpa-0123abcd4567
chip mcp audit --limit 20
```

Exit codes: 0 ok, 1 protocol error, 2 refused / bad input, 3 `CHIP_MCP` off, 4 pending approval.

## Model turns and background jobs (slice 2)

`scripts/chip/mcp_tools.py` is the tool planner. It never widens policy: every call still goes
through `mcp_client.call` (allowlist, level, advertised check, approvals, audit).

* **Offered tools.** `offered_tools()` lists allowlisted tools at level `auto` or `ask` from
  `servers.json` (no subprocess). `deny` / unknown levels are never offered. Empty when `CHIP_MCP` is off.
* **Planner prompt.** `planner_prompt()` adds a short block to the system prompt. The model asks for a
  tool with one line per call:

  ```text
  [[mcp notes search {"q": "deploy"}]]
  ```

  At most 3 calls per reply. Malformed lines, non-object arguments, extra calls and tools that were not
  offered become `refused` receipts and never run.
* **Receipts.** `run_calls()` / `plan_turn()` return one receipt per call: `ok` (with a text excerpt
  clipped to 1500 chars), `pending` (an `ask` tool queued an approval; nothing ran), `refused` or
  `error`. Receipts are appended to `mcp/receipts.jsonl` with the requester, the argument hash and the
  excerpt size, never the arguments or the result. `render_results()` frames results for the next turn
  as data, not instructions.
* **Background jobs.** With `CHIP_MCP=1` the model step (`bg_model_step`) offers the tools to every job
  unless the job sets `params.mcp_tools: false`. Calls run as requester `bgjob:<id>`. The hop record
  stores its receipts (max 10, bounded strings). A hop that made calls is never the last one, so the
  model always sees its results; `params.mcp_max_rounds` (default 2, hard 5) caps tool rounds per job,
  after which call lines are refused with `tool round cap`.
* **Ask → approval queue → PM summary.** An `ask` call queues a single-use approval bound to the exact
  arguments. Pending approvals show in the PM daily summary under **Waiting on you** (and in the
  decision ping). After someone other than the requester approves (`chip mcp approve <id> --by <you>`),
  the next identical call (same server, tool and arguments) from any turn or job uses it once.
  The summary also counts the day's MCP receipts by status; refused calls count as refusals.
* **Model turns.** `plan_turn(reply, requested_by=...)` is the entry point for any turn loop: it strips
  call lines from the reply, runs the calls and returns `{"text", "receipts", "results"}`.

CLI: `chip mcp offered` (tools + planner prompt), `chip mcp receipts --limit N`.

## Not yet

The relay's chat turn does not call `plan_turn` yet (engine hook is ready; wiring is a later slice).
Resuming a specific job automatically after its `ask` call is approved is not automatic: the approval
is used by the next identical call. HTTP/SSE transport is later.
