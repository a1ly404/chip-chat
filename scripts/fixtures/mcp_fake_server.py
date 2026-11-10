"""Tiny fake MCP stdio server for tests. Mode is argv[1]: ok | garbage | badinit | exit | hang | paged.

Tools: echo (returns its text), add (a+b), danger (should never be reached by policy), env
(returns the value of FAKE_MCP_SECRET so tests can check env plumbing).
"""

import json
import os
import sys
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "ok"
TOOLS = [
    {"name": "echo", "description": "Echo text", "inputSchema": {"type": "object"}},
    {"name": "add", "description": "Add numbers", "inputSchema": {"type": "object"}},
    {"name": "danger", "description": "Never allowed", "inputSchema": {"type": "object"}},
    {"name": "env", "description": "Report env", "inputSchema": {"type": "object"}},
]
CALLS_LOG = os.environ.get("FAKE_MCP_CALLS")


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        continue
    if method == "initialize":
        if MODE == "badinit":
            send({"jsonrpc": "2.0", "id": rid, "result": {"nope": 1}})
            continue
        sys.stderr.write("fake server starting\n")
        send({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info"}})
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "0"}}})
    elif method == "tools/list":
        if MODE == "garbage":
            sys.stdout.write("this is not json\n")
            sys.stdout.flush()
            continue
        if MODE == "exit":
            sys.exit(3)
        if MODE == "hang":
            time.sleep(30)
        if MODE == "paged":
            cursor = (msg.get("params") or {}).get("cursor")
            if not cursor:
                send({"jsonrpc": "2.0", "id": 99999, "result": {"stale": True}})
                send({"jsonrpc": "2.0", "id": "srv-1", "method": "sampling/createMessage", "params": {}})
                send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS[:2], "nextCursor": "p2"}})
            else:
                send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS[2:]}})
            continue
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        p = msg.get("params") or {}
        name, args = p.get("name"), p.get("arguments") or {}
        if CALLS_LOG:
            with open(CALLS_LOG, "a", encoding="utf-8") as fh:
                fh.write(name + "\n")
        if name == "echo":
            text = str(args.get("text", ""))
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}]}})
        elif name == "add":
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": str(args.get("a", 0) + args.get("b", 0))}]}})
        elif name == "env":
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": os.environ.get("FAKE_MCP_SECRET", "<unset>") + "|" + ("LEAKED" if "PARENT_ONLY" in os.environ else "clean")}]}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "ran " + str(name)}], "isError": False}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method"}})
