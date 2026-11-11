"""MCP tools in the room chat turn (parity P1.5 slice 3).

``chip room <name> -m TEXT`` (the shot path the relay uses for every chat reply) calls
:func:`run_chat_turn` instead of a bare ``runtime.complete``. When ``CHIP_MCP`` is off, or no tool is
allowlisted at level ``auto`` / ``ask``, it makes exactly one completion and returns its text, so the
turn is byte-for-byte what it was before this slice.

When tools are offered:

1. The planner block from :func:`chip.mcp_tools.planner_prompt` is appended to the first system
   message.
2. Call lines in the reply go through :func:`chip.mcp_tools.plan_turn` (same allowlist, levels,
   approval queue and audit as ``chip mcp call``; nothing is widened). The requester is
   ``room:<room>:<agent>``, so the person or bot that asked can never approve its own ``ask`` call.
3. If any call reached the policy gate, the model gets one more completion with the results framed
   as data. At most ``CHIP_MCP_CHAT_ROUNDS`` tool rounds per turn (default 2, hard 5); call lines
   after the cap are refused with ``tool round cap`` and do not run.
4. The posted reply is the final text with call lines stripped, followed by a short receipt footer
   (one line per call: server, tool, status, approval id for pending calls). Receipts never include
   the arguments or the tool output; the output only goes back to the model.

Every completion is passed to the injected ``complete`` callable, so spend accounting (the session
ledger delta measured by the caller) covers all rounds.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping

from chip import mcp_tools

ROUNDS_ENV = "CHIP_MCP_CHAT_ROUNDS"
FOOTER_HEAD = "MCP receipts:"
MAX_FOOTER_LINES = 10

Completer = Callable[..., tuple[str, Any, str, Any]]


def chat_rounds(env: Mapping[str, str] | None = None) -> int:
    e = os.environ if env is None else env
    return mcp_tools.clamp_rounds(e.get(ROUNDS_ENV))


def with_planner(messages: list[dict[str, str]], tools: list[Mapping[str, str]]) -> list[dict[str, str]]:
    """Copy of ``messages`` with the planner block appended to the first system message."""
    block = mcp_tools.planner_prompt(list(tools))
    out = [dict(m) for m in messages]
    if not block:
        return out
    for m in out:
        if m.get("role") == "system":
            m["content"] = f"{m.get('content', '')}\n\n{block}"
            return out
    return [{"role": "system", "content": block}, *out]


def receipt_footer(receipts: list[Mapping[str, Any]]) -> str:
    """Short, data-free receipt lines for the chat reply ('' when no receipts)."""
    if not receipts:
        return ""
    lines = [FOOTER_HEAD]
    for r in receipts[:MAX_FOOTER_LINES]:
        line = f"- {r.get('server')} {r.get('tool')}: {r.get('status')}"
        if r.get("status") == "pending" and r.get("approval_id"):
            line += f" (approval {r.get('approval_id')} waiting on a human)"
        elif r.get("status") in ("refused", "error") and r.get("reason"):
            line += f" ({str(r.get('reason'))[:120]})"
        elif r.get("is_error"):
            line += " (tool reported an error)"
        lines.append(line)
    if len(receipts) > MAX_FOOTER_LINES:
        lines.append(f"- … {len(receipts) - MAX_FOOTER_LINES} more")
    return "\n".join(lines)


_RECEIPT_KEYS = ("server", "tool", "status", "approval_id", "reason", "is_error", "args_sha256", "ms")


def sanitize(receipts: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Receipts safe to store or print: known keys only, no tool output."""
    return [{k: r[k] for k in _RECEIPT_KEYS if k in r} for r in receipts[:MAX_FOOTER_LINES]]


def run_chat_turn(
    messages: list[dict[str, str]],
    *,
    complete: Completer,
    requested_by: str,
    complete_kwargs: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    config: Any = None,
    caller: mcp_tools.Caller | None = None,
) -> dict[str, Any]:
    """One chat turn with optional MCP tool rounds.

    Returns ``{"text", "reply", "payload", "routed", "ledger", "receipts", "completions"}`` where
    ``text`` is the final model text (call lines stripped when tools were offered), ``reply`` is
    ``text`` plus the receipt footer, and ``payload`` / ``routed`` / ``ledger`` come from the last
    completion.
    """
    kw = dict(complete_kwargs or {})
    tools = mcp_tools.offered_tools(config=config, env=env)
    if not tools:
        text, payload, routed, ledger = complete(messages, **kw)
        return {
            "text": text,
            "reply": text,
            "payload": payload,
            "routed": routed,
            "ledger": ledger,
            "receipts": [],
            "completions": 1,
        }
    convo = with_planner(messages, tools)
    cap = chat_rounds(env)
    receipts: list[dict[str, Any]] = []
    rounds = 0
    completions = 0
    while True:
        text, payload, routed, ledger = complete(convo, **kw)
        completions += 1
        text = text or ""
        if not mcp_tools.parse_calls(text)[0] and not mcp_tools.LOOSE_RE.search(text):
            break
        if rounds >= cap:
            planned = mcp_tools.plan_turn(text, requested_by=requested_by, config=config, refuse_reason="tool round cap")
            receipts += planned["receipts"]
            break
        planned = mcp_tools.plan_turn(text, requested_by=requested_by, tools=tools, config=config, caller=caller)
        receipts += planned["receipts"]
        if not any("args_sha256" in r for r in planned["receipts"]):
            break  # only malformed / not-offered lines: nothing ran, nothing new to show the model
        rounds += 1
        convo = [
            *convo,
            {"role": "assistant", "content": text},
            {"role": "user", "content": f"{planned['results']}\n\nUse these results to answer the original message."},
        ]
    final = mcp_tools.strip_calls(text)
    footer = receipt_footer(receipts)
    reply = f"{final}\n\n{footer}" if footer and final else (footer or final)
    return {
        "text": final,
        "reply": reply,
        "payload": payload,
        "routed": routed,
        "ledger": ledger,
        "receipts": sanitize(receipts),
        "completions": completions,
    }


__all__ = ["FOOTER_HEAD", "ROUNDS_ENV", "chat_rounds", "receipt_footer", "run_chat_turn", "sanitize", "with_planner"]
