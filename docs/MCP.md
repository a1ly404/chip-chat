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

## Not yet

Model turns do not call MCP tools yet (slice 2: expose allowlisted `auto` tools to the tool planner
and background jobs, with `ask` routed to the approval queue). HTTP/SSE transport is later.
