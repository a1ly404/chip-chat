"""Chip Chat CLI commands."""

from __future__ import annotations

import argparse
import json
import re
import sys

from chip import config, law, mock, openrouter, runtime, spend, store

_AGENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")


def _print_spend_summary(ledger: dict) -> None:
    print(spend.format_summary(ledger))


def cmd_spend(args: argparse.Namespace) -> int:
    key = None
    if not (args.mock or config.mock_mode()):
        try:
            key = openrouter.require_api_key()
        except openrouter.ChipConfigError as exc:
            print(f"auth/key: skipped ({exc})", file=sys.stderr)
    return spend.print_spend_report(key)


def cmd_ping(args: argparse.Namespace) -> int:
    use_mock = args.mock or config.mock_mode()
    ping_model = config.resolve_model(config.PING_MODEL)
    if use_mock:
        info = mock.mock_auth_key()
        _, payload, routed, ledger = runtime.complete(
            [{"role": "user", "content": "ping"}],
            model=ping_model,
            max_tokens=5,
            use_mock=True,
            command="ping",
        )
        usage_m = info.get("usage_monthly")
        print("OK (mock)")
        print(f"model={routed}")
        print(f"usage_monthly={usage_m}")
        print(f"reply={openrouter.extract_assistant_text(payload)[:120]}")
        if args.spend:
            return cmd_spend(args)
        return 0

    try:
        key = openrouter.require_api_key()
    except openrouter.ChipConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        info = openrouter.auth_key_info(key)
    except openrouter.ChipConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    messages = [{"role": "user", "content": "Reply with exactly: ok"}]
    try:
        text, payload, routed, ledger = runtime.complete(
            messages,
            model=ping_model,
            max_tokens=8,
            use_mock=False,
            command="ping",
        )
    except openrouter.ChipConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4

    usage = info.get("usage_monthly")
    if usage is None:
        usage = info.get("usage")
    print("OK")
    print(f"model={routed}")
    print(f"usage_monthly={usage}")
    print(f"reply={text[:120]}")
    _print_spend_summary(ledger)
    store.log_message("ping", "system", "ping", model=routed, extra={"usage_monthly": usage})
    if args.spend:
        return cmd_spend(args)
    return 0


def _persona_system(name: str) -> str:
    personas = config.load_personas()
    entry = personas.get(name) or personas.get("default") or config.DEFAULT_PERSONAS["default"]
    return entry.get("system") or config.DEFAULT_PERSONAS["default"]["system"]


def _resolve_chat_model(args: argparse.Namespace) -> str:
    if getattr(args, "escalate", False):
        return config.resolve_model(None, escalate=True)
    if args.model:
        return config.resolve_model(args.model)
    return config.resolve_model(None)


def _run_chat_turn(
    thread: str,
    persona: str,
    model: str,
    user_line: str,
    *,
    use_mock: bool,
    escalate: bool = False,
) -> tuple[str, dict]:
    history = [
        {"role": "system", "content": _persona_system(persona)},
        {"role": "user", "content": user_line},
    ]
    store.log_message(thread, "user", user_line, agent="user")
    text, _, routed, ledger = runtime.complete(
        history,
        model=model,
        max_tokens=512,
        use_mock=use_mock,
        command="chat",
        escalate=escalate and not model,
    )
    store.log_message(thread, "assistant", text, agent=persona, model=routed)
    return text, ledger


def cmd_chat(args: argparse.Namespace) -> int:
    thread = args.thread or "default"
    persona = args.persona or "default"
    use_mock = getattr(args, "mock", False) or config.mock_mode()
    try:
        model = _resolve_chat_model(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.once:
        try:
            text, ledger = _run_chat_turn(
                thread,
                persona,
                model,
                args.once,
                use_mock=use_mock,
                escalate=getattr(args, "escalate", False),
            )
        except openrouter.ChipConfigError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 3
        print(text)
        print("---")
        _print_spend_summary(ledger)
        return 0

    print(f"Chip Chat REPL (thread={thread}, persona={persona}, model={model})")
    print(f"Data: {config.data_dir()}")
    print("Type /exit to quit.\n")

    history: list[dict[str, str]] = [{"role": "system", "content": _persona_system(persona)}]
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return 0
        if not line:
            continue
        if line.lower() in ("/exit", "/quit"):
            print("bye")
            return 0
        history.append({"role": "user", "content": line})
        store.log_message(thread, "user", line, agent="user")
        try:
            text, _, routed, ledger = runtime.complete(
                history, model=model, max_tokens=512, use_mock=use_mock, command="chat"
            )
        except openrouter.ChipConfigError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            history.pop()
            continue
        history.append({"role": "assistant", "content": text})
        store.log_message(thread, "assistant", text, agent=persona, model=routed)
        print(f"chip> {text}\n")


def cmd_agent_spawn(args: argparse.Namespace) -> int:
    name = args.name
    persona = args.persona or (name if name in config.load_personas() else "default")
    agents = store.load_agents()
    agents[name] = {
        "persona": persona,
        "thread": args.thread or f"agent-{name}",
    }
    store.save_agents(agents)
    print(f"spawned agent {name!r} persona={persona!r} thread={agents[name]['thread']}")
    return 0


def cmd_agent_say(args: argparse.Namespace) -> int:
    name = args.name
    text = args.text
    agents = store.load_agents()
    if name not in agents:
        print(f"ERROR: unknown agent {name!r}; run: chip agent spawn {name}", file=sys.stderr)
        return 2
    meta = agents[name]
    persona = meta.get("persona") or "default"
    thread = meta.get("thread") or f"agent-{name}"
    system = _persona_system(str(persona))
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]
    store.log_message(thread, "user", text, agent=name)
    use_mock = getattr(args, "mock", False) or config.mock_mode()
    try:
        reply, _, routed, ledger = runtime.complete(
            messages,
            model=config.default_model(),
            max_tokens=400,
            use_mock=use_mock,
            command="agent_say",
        )
    except openrouter.ChipConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    store.log_message(thread, "assistant", reply, agent=name, model=routed)
    print(f"{name}> {reply}")
    _print_spend_summary(ledger)
    return 0


def build_room_messages(
    room: str,
    persona: str,
    user_text: str,
    *,
    history_limit: int | None = None,
) -> list[dict[str, str]]:
    """Room law (binding) plus persona, prior transcript, then the new user line."""
    system = law.compose_system(_persona_system(persona), law.load_room_law(room))
    limit = config.room_history_limit(history_limit)
    history = store.load_room_history(room, limit=limit)
    return [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": user_text},
    ]


def _room_demo_turn(
    room: str,
    agent_name: str,
    user_text: str,
    *,
    use_mock: bool,
    history_limit: int | None = None,
) -> str:
    messages = build_room_messages(room, agent_name, user_text, history_limit=history_limit)
    store.log_message(room, "user", user_text, agent=agent_name)
    reply, _, routed, ledger = runtime.complete(
        messages, model=config.default_model(), max_tokens=200, use_mock=use_mock, command="room"
    )
    store.log_message(room, "assistant", reply, agent=agent_name, model=routed)
    print(f"{agent_name}> {reply}")
    _print_spend_summary(ledger)
    return reply


def _validate_agent(name: str | None) -> str | None:
    """Caller-supplied identity only. Returns an error string, or None if valid."""
    if not name or not name.strip():
        return "--as is required (caller-supplied agent; Chip Chat will not invent one)"
    if not _AGENT_NAME.fullmatch(name.strip()):
        return f"--as {name!r} must be a single name (letters, digits, _ or -)"
    return None


def _shot_usage(payload: dict, ledger: dict) -> tuple[int, int, float, float]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    prompt = int(usage.get("prompt_tokens") or ledger.get("last_prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or ledger.get("last_completion_tokens") or 0)
    session = float(ledger.get("session_usd") or 0.0)
    monthly = float(ledger.get("monthly_usd") or 0.0)
    return prompt, completion, session, monthly


def cmd_room_shot(args: argparse.Namespace) -> int:
    """Non-interactive room turn. --json prints one object and nothing else."""
    message = (getattr(args, "message", None) or "").strip()
    agent_err = _validate_agent(getattr(args, "as_agent", None))
    if not message:
        print("ERROR: -m/--message is required", file=sys.stderr)
        return 1
    if agent_err:
        print(f"ERROR: {agent_err}", file=sys.stderr)
        return 1
    limit = getattr(args, "turn_limit", 1)
    limit = 1 if limit is None else int(limit)
    if limit < 1:
        print("ERROR: --turn-limit must be >= 1", file=sys.stderr)
        return 1
    # One -m is one exchange. A limit below 1 is rejected above; this process never loops.

    room = args.name
    agent = str(args.as_agent).strip()
    use_mock = args.mock or config.mock_mode()
    persona = agent if agent in config.load_personas() else "default"
    history_limit = getattr(args, "history_limit", None)
    messages = build_room_messages(room, persona, message, history_limit=history_limit)
    messages[0]["content"] += (
        f"\n\nYou are speaking only as {agent}. "
        "Do not invent other speakers or addresses."
    )
    before = float(spend.load_ledger().get("session_usd") or 0.0)
    store.log_message(room, "user", message)
    try:
        text, payload, routed, ledger = runtime.complete(
            messages,
            model=config.default_model(),
            max_tokens=512,
            use_mock=use_mock,
            command="room",
        )
    except openrouter.ChipConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    prompt_n, completion_n, session_usd, monthly_usd = _shot_usage(payload, ledger)
    call_cost = max(0.0, session_usd - before)
    store.log_message(
        room,
        "assistant",
        text,
        agent=agent,
        model=routed,
        extra={
            "tokens_prompt": prompt_n,
            "tokens_completion": completion_n,
            "cost_usd": call_cost,
        },
    )
    result = {
        "reply": text,
        "tokens_prompt": prompt_n,
        "tokens_completion": completion_n,
        "session_usd": session_usd,
        "monthly_usd": monthly_usd,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        _print_room_header(room)
        print(text)
        print("---")
        _print_spend_summary(ledger)
    return 0


def _print_room_header(room: str) -> None:
    loaded = law.load_room_law(room)
    print(law.law_label(loaded))
    print(f"Data: {config.data_dir()}")


def cmd_room(args: argparse.Namespace) -> int:
    room = args.name
    use_mock = args.mock or config.mock_mode()
    if getattr(args, "message", None) or getattr(args, "json", False):
        return cmd_room_shot(args)
    if args.once:
        persona = args.persona or "default"
        _print_room_header(room)
        history_limit = getattr(args, "history_limit", None)
        messages = build_room_messages(room, persona, args.once, history_limit=history_limit)
        store.log_message(room, "user", args.once, agent="user")
        try:
            text, _, routed, ledger = runtime.complete(
                messages,
                model=config.default_model(),
                max_tokens=400,
                use_mock=use_mock,
                command="room",
            )
        except openrouter.ChipConfigError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 3
        store.log_message(room, "assistant", text, agent=persona, model=routed)
        print(text)
        print("---")
        _print_spend_summary(ledger)
        return 0

    if args.demo or room == "demo":
        room = "demo" if room == "demo" else room
        print(f"Room demo (room={room}, mock={use_mock})")
        _print_room_header(room)
        print()
        try:
            if not use_mock:
                openrouter.require_api_key()
        except openrouter.ChipConfigError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            print("Hint: export CHIP_CHAT_MOCK=1 or pass --mock for offline demo.", file=sys.stderr)
            return 2
        a_text = "We need a one-line CLI smoke test for Chip Chat before UI."
        print(f"user> {a_text}\n")
        history_limit = getattr(args, "history_limit", None)
        b_reply = _room_demo_turn(
            room, "planner", a_text, use_mock=use_mock, history_limit=history_limit
        )
        handoff = f"Planner said: {b_reply}\nRespond in one short paragraph as Builder."
        print()
        _room_demo_turn(room, "builder", handoff, use_mock=use_mock, history_limit=history_limit)
        print("\n(agent A → agent B handoff complete)")
        return 0

    personas = config.load_personas()
    names = args.agents or ["planner", "builder"]
    print(f"Room {room!r} agents={names} (use --demo for scripted handoff, or --once TEXT)")
    print(f"Personas loaded: {', '.join(personas.keys())}")
    _print_room_header(room)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chip", description="Chip Chat — OpenRouter multi-agent CLI")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--mock", action="store_true", help="Offline mock (no OpenRouter calls)")
    sub = parser.add_subparsers(dest="command", required=True)

    ping = sub.add_parser("ping", parents=[common], help="Auth key check + one tiny completion")
    ping.add_argument("--spend", action="store_true", help="Also print spend ledger report")
    ping.set_defaults(func=cmd_ping)

    spend_p = sub.add_parser("spend", parents=[common], help="Spend ledger + auth/key usage (no secrets)")
    spend_p.set_defaults(func=cmd_spend)

    chat = sub.add_parser("chat", parents=[common], help="User ↔ default agent REPL")
    chat.add_argument("--thread", default="default", help="Thread id for JSONL log")
    chat.add_argument("--persona", default="default", help="Persona name from config")
    chat.add_argument("--model", default=None, help="Override model slug")
    chat.add_argument(
        "--escalate",
        action="store_true",
        help=f"Use escalate model ({config.ESCALATE_MODEL})",
    )
    chat.add_argument("--once", metavar="TEXT", help="Single message, non-interactive (automation smoke)")
    chat.set_defaults(func=cmd_chat)

    agent = sub.add_parser("agent", help="Named agents")
    agent_sub = agent.add_subparsers(dest="agent_cmd", required=True)

    spawn = agent_sub.add_parser("spawn", parents=[common], help="Register a named agent")
    spawn.add_argument("name")
    spawn.add_argument("--persona", default=None, help="Persona key (default: name or default)")
    spawn.add_argument("--thread", default=None, help="JSONL thread name")
    spawn.set_defaults(func=cmd_agent_spawn)

    say = agent_sub.add_parser("say", parents=[common], help="Agent speaks (one completion)")
    say.add_argument("name")
    say.add_argument("text")
    say.set_defaults(func=cmd_agent_say)

    room = sub.add_parser("room", parents=[common], help="Multi-agent room")
    room.add_argument("name", nargs="?", default="demo", help="Room name (or 'demo')")
    room.add_argument("--demo", action="store_true", help="Scripted planner → builder handoff")
    room.add_argument("-m", "--message", default=None, help="Non-interactive message (requires --as)")
    room.add_argument(
        "--as",
        dest="as_agent",
        default=None,
        help="Speaker id (persona key from config/chip_chat_personas.json when defined)",
    )
    room.add_argument("--json", action="store_true", help="Print one JSON object on stdout")
    room.add_argument("--turn-limit", dest="turn_limit", type=int, default=1, help="Max exchanges this process (default 1)")
    room.add_argument(
        "--history-limit",
        dest="history_limit",
        type=int,
        default=None,
        help="Prior transcript lines to load (default 10, or CHIP_CHAT_ROOM_HISTORY_LIMIT)",
    )
    room.add_argument("--once", metavar="TEXT", help="One turn in this room (law loaded into the system prompt)")
    room.add_argument("--persona", default="default", help="Persona for --once")
    room.add_argument("--agents", nargs="+", default=None, help="Agent persona names")
    room.set_defaults(func=cmd_room)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 1
    return int(func(args))


if __name__ == "__main__":
    raise SystemExit(main())
