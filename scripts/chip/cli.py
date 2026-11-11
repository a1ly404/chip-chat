"""Chip Chat CLI commands."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from pathlib import Path

from chip import config, constraints, law, learnings, mempalace, mock, openrouter, pack_values, runtime, spend, store, task, task_ledger, tool
from chip import standing_law
from chip.bg_jobs import JobsDisabled as JobsDisabledError
from chip.need_tool import NeedToolError
from chip.tools_manifest import list_tools

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
    return config.model_for_persona(getattr(args, "persona", None))


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


def recall_mined_memory_block(
    room: str,
    agent: str,
    user_text: str,
    *,
    top_k: int | None = None,
    token_budget: int | None = None,
) -> str:
    """Top-K mined memories for prompt assembly (advisory only).

    Returns (block, reads_count) — Phase-1 ledger records memory ops at the
    call site; a recall with zero mined memories still counts one read attempt
    (the op happened; the absence is visible in the prompt block being empty).
    """
    k = config.memory_top_k(top_k)
    budget = config.memory_token_budget(token_budget)
    reads = 0
    if k <= 0 or budget <= 0:
        return "", reads
    memories = mempalace.load_memories(room, agent)
    reads = 1
    if not memories:
        return "", reads
    picked = mempalace.select_top_k(
        memories,
        user_text,
        k=k,
        token_budget=budget,
    )
    return mempalace.format_recall_block(picked), reads


def build_room_messages(
    room: str,
    persona: str,
    user_text: str,
    *,
    history_limit: int | None = None,
    recall_agent: str | None = None,
    memory_top_k: int | None = None,
    memory_token_budget: int | None = None,
    memory_ops_out: dict[str, int] | None = None,
) -> list[dict[str, str]]:
    """Room law (binding) plus persona, prior transcript, then the new user line.

    ``memory_ops_out`` (Phase-1 ledger): when a dict is passed, the memory-op
    observability is recorded there (``reads`` so far) — opt-in per call site,
    so every existing caller keeps itsOLD shape.
    """
    agent_key = recall_agent or persona
    mined, memory_reads = recall_mined_memory_block(
        room,
        agent_key,
        user_text,
        top_k=memory_top_k or config.memory_top_k(),
        token_budget=memory_token_budget,
    )
    boot = standing_law.session_boot_system_prefix(
        room,
        persona,
        advisory_memory_block=mined,
    )
    system = law.compose_system(
        _persona_system(persona),
        law.load_room_law(room),
        session_boot_prefix=boot,
        learnings_block=learnings.advisory_learnings_for_turn(persona),
    )
    limit = config.room_history_limit(history_limit)
    history = store.load_room_history(room, limit=limit)
    if memory_ops_out is not None:
        memory_ops_out["reads"] = int(memory_reads)
    from chip import boards

    board_note = boards.wake_block(room)
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    if board_note:
        messages.append({"role": "system", "content": board_note})
    return [
        *messages,
        *history,
        {"role": "user", "content": user_text},
    ]


def close_room_session(room: str) -> int:
    """Post-process the open session segment into per-agent memory JSONL files.

    Phase-1 ledger: this session's memory WRITE op is also logged to the room
    JSONL as a spoken row (``memory_op_counts.writes/written_lines``) so cost
    discovery for `close` shows up in the same stream as the turns that made it.
    """
    segment = store.session_segment_rows(room)
    records = mempalace.extract_from_transcript(room, segment)
    written = mempalace.append_memories(room, records)
    store.mark_session_closed(room)
    store.log_message(
        room,
        "system",
        f"session close: memory mining wrote {written} line(s) from {len(records)} record(s)",
        extra={
            "memory_op_counts": {
                "reads": 0,
                "writes": len(records),
                "written_lines": written,
            },
        },
    )
    print(f"room {room!r} closed: extracted {len(records)} memories, wrote {written} lines")
    return 0


def cmd_room_close(args: argparse.Namespace) -> int:
    return close_room_session(args.name)


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
        messages,
        model=config.default_model(),
        max_tokens=config.ROOM_COMPLETION_MAX_TOKENS,
        use_mock=use_mock,
        command="room",
    )
    from chip_relay.receipt_gate import body_for_store

    store.log_message(room, "assistant", body_for_store(reply), agent=agent_name, model=routed)
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
    monthly = spend.ledger_monthly_usd(ledger)
    return prompt, completion, session, monthly


def _room_turn_message(args: argparse.Namespace) -> str:
    """User text for a non-interactive room turn (-m/--message or --once TEXT)."""
    return (getattr(args, "message", None) or getattr(args, "once", None) or "").strip()


def _room_uses_shot_path(args: argparse.Namespace) -> bool:
    """Shot path: -m/--message, --json output, or --as with an explicit turn message."""
    if getattr(args, "message", None) or getattr(args, "json", False):
        return True
    if getattr(args, "as_agent", None) and _room_turn_message(args):
        return True
    return False


def cmd_room_shot(args: argparse.Namespace) -> int:
    """Non-interactive room turn. --json prints one object and nothing else."""
    message = _room_turn_message(args)
    agent_err = _validate_agent(getattr(args, "as_agent", None))
    if not message:
        print("ERROR: -m/--message or --once TEXT is required", file=sys.stderr)
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
    from chip import system_pack

    op_name = system_pack.pack_identity_string("operator_name", default="Operator")
    author_role = op_name if agent == op_name else ""
    constraints.try_add_from_message(
        message,
        author_role=author_role,
        source_turn=f"{room}:{agent}",
    )
    blocked = constraints.first_hard_block(message)
    if blocked:
        store.log_message(room, "user", message)
        store.log_message(room, "assistant", blocked, agent=agent)
        if args.json:
            print(json.dumps({"reply": blocked, "blocked": True}, ensure_ascii=False))
        else:
            print(blocked)
        return 0
    history_limit = getattr(args, "history_limit", None)
    memory_ops: dict[str, int] = {}
    messages = build_room_messages(
        room,
        persona,
        message,
        history_limit=history_limit,
        recall_agent=agent,
        memory_ops_out=memory_ops,
    )
    messages[0]["content"] += (
        f"\n\nYou are speaking only as {agent}. "
        "Do not invent other speakers or addresses."
    )
    before = float(spend.load_ledger().get("session_usd") or 0.0)
    store.log_message(room, "user", message)
    from chip import mcp_chat

    try:
        # P1.5 slice 3: MCP tool rounds when CHIP_MCP=1 and tools are allowlisted; otherwise one plain completion.
        turn = mcp_chat.run_chat_turn(
            messages,
            complete=lambda msgs, **kw: runtime.complete(msgs, **kw),
            requested_by=f"room:{room}:{agent}",
            complete_kwargs={
                "model": config.model_for_persona(persona),
                "max_tokens": config.ROOM_COMPLETION_MAX_TOKENS,
                "use_mock": use_mock,
                "command": "room",
            },
        )
    except openrouter.ChipConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    text, payload, routed, ledger = turn["reply"], turn["payload"], turn["routed"], turn["ledger"]
    mcp_receipts = turn["receipts"]
    prompt_n, completion_n, session_usd, monthly_usd = _shot_usage(payload, ledger)
    call_cost = max(0.0, session_usd - before)
    # Phase-1 task ledger (operator lock 2026-09-28): task binding is FIRST-CLASS —
    # the columns land on task rows only; task-less shots keep the old row shape.
    task_extra: dict[str, Any] = {}
    task_id = (getattr(args, "task_id", "") or "").strip()
    task_type = (getattr(args, "task_type", "") or "").strip()
    if task_id:
        turn = task_ledger.TaskCounter(task_ledger.counter_path(config.data_dir())).next_turn(task_id)
        task_extra = task_ledger.counts_for_row(
            task_id,
            task_type or "other",
            turn,
            call_cost,
            memory_ops,
        )
    from chip_relay.receipt_gate import body_for_store

    store.log_message(
        room,
        "assistant",
        body_for_store(text),
        agent=agent,
        model=routed,
        extra={
            "tokens_prompt": prompt_n,
            "tokens_completion": completion_n,
            "cost_usd": call_cost,
            **task_extra,
            **({"mcp_receipts": mcp_receipts} if mcp_receipts else {}),
        },
    )
    result = {
        "reply": text,
        "tokens_prompt": prompt_n,
        "tokens_completion": completion_n,
        "session_usd": session_usd,
        "monthly_usd": monthly_usd,
    }
    if task_extra:
        result["task_ledger"] = task_extra
    if mcp_receipts:
        result["mcp_receipts"] = mcp_receipts
        result["completions"] = turn["completions"]
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
    if getattr(args, "close", False):
        return cmd_room_close(args)
    use_mock = args.mock or config.mock_mode()
    if _room_uses_shot_path(args):
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
                model=config.model_for_persona(persona),
                max_tokens=config.ROOM_COMPLETION_MAX_TOKENS,
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


def cmd_tool_list(args: argparse.Namespace) -> int:
    rows = list_tools()
    if args.json:
        payload = [
            {
                "id": t.id,
                "argv": list(t.argv),
                "risk": t.risk,
                "allow_extra_args": t.allow_extra_args,
                "description": t.description,
            }
            for t in rows
        ]
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if not rows:
        print("No tools in allowlist manifest.")
        return 0
    for t in rows:
        extras = " (+extra args)" if t.allow_extra_args else ""
        print(f"{t.id}\t{t.risk}\t{' '.join(t.argv)}{extras}")
        if t.description:
            print(f"  {t.description}")
    return 0


def cmd_tool_run(args: argparse.Namespace) -> int:
    tool_id = args.tool_id
    extra = list(args.extra_args or [])
    if extra and extra[0] == "--":
        extra = extra[1:]
    try:
        result = tool.run(
            tool_id,
            *extra,
            execute=bool(args.execute),
            timeout_seconds=args.timeout,
            cwd=args.cwd,
            is_human_or_inject=True,  # CLI keyboard = the human/inject hot path (Phase C mode)
        )
    except NeedToolError as exc:
        print(exc.token(), file=sys.stderr)
        return 1
    except tool.ToolNotAllowedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except tool.ToolGatedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        if not result.executed:
            print("DRY-RUN (gated): pass --execute or set CHIP_TOOL_AUTO=1", file=sys.stderr)
        print(f"tool={result.tool_id} exit={result.exit_code} risk={result.risk}")
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    if not result.executed:
        return 3
    return 0 if result.exit_code == 0 else 4


def cmd_task_create(args: argparse.Namespace) -> int:
    args_list = list(args.args or [])
    try:
        envelope = task.create_task(args.tool_id, args=args_list, note=args.note)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(envelope, ensure_ascii=False))
    else:
        print(f"created {envelope['id']} tool={envelope['tool']} status={envelope['status']}")
    return 0


def cmd_taskflow(args: argparse.Namespace) -> int:
    """chip taskflow — Phase-2 circuit CLI (brief|claim|bind|gate), operator-locked.

    Fail-closed faces: palace-gate failure prints a BLOCKED line + rc 4;
    scope conflict rc 5. Nothing falls back silently to the raw task (golden 5).
    """
    from chip import taskflow  # local import keeps module load cheap elsewhere
    if args.sub == "gate":
        try:
            taskflow.preflight()
            print(json.dumps({"gate": "open"}, ensure_ascii=False))
            return 0
        except taskflow.TaskPalaceUnavailable as exc:
            print(json.dumps({"gate": "BLOCKED", "error": str(exc)}, ensure_ascii=False))
            return 4
    if args.sub == "brief":
        try:
            brief = taskflow.build_brief(
                args.task_id, args.agent, args.room, args.goal,
                acceptance=[s.strip() for s in (args.ac or "").split("|") if s.strip()],
                environ=None,  # process env (os.environ) — test overrides propagate
            )
        except taskflow.TaskPalaceUnavailable as exc:
            print(f"BLOCKED: {exc}", file=sys.stderr)
            return 4
        print(brief.text)
        return 0
    if args.sub == "claim":
        try:
            taskflow.claim_if_free(args.room, args.agent, args.scope)
        except taskflow.TaskScopeConflict as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 5
        print("claimed")
        return 0
    if args.sub == "bind":
        try:
            out = taskflow.bind_result(
                args.task_id, args.room, args.agent,
                outcome=args.outcome,
                receipts=[s.strip() for s in (args.receipts or "").split(",") if s.strip()],
                lessons=args.lessons, task_type=args.task_type,
                linear_anchor=args.linear_anchor, environ=None,  # process env
            )
        except taskflow.TaskPalaceUnavailable as exc:
            print(f"BLOCKED: {exc}", file=sys.stderr)
            return 4
        print(out["linear_comment"])
        print(f"memory: {out['palace']['path']}")
        return 0
    return 1


def cmd_task_show(args: argparse.Namespace) -> int:
    try:
        envelope = task.load_task(args.task_id)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(envelope, ensure_ascii=False))
    else:
        print(json.dumps(envelope, indent=2, ensure_ascii=False))
    return 0


def cmd_task_run(args: argparse.Namespace) -> int:
    try:
        envelope = task.run_task(
            args.task_id,
            execute=bool(args.execute),
            timeout_seconds=args.timeout,
            cwd=args.cwd,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    exec_res = envelope.get("execution_result") or {}
    gated = bool(exec_res.get("gated"))
    if args.json:
        print(json.dumps(envelope, ensure_ascii=False))
    else:
        print(f"task {envelope['id']} status={envelope['status']}")
        print(json.dumps(exec_res, indent=2, ensure_ascii=False))
    if gated:
        return 3
    ok = bool(exec_res.get("ok"))
    return 0 if ok else 4


def cmd_job_create(args: argparse.Namespace) -> int:
    from chip import jobs

    doc = jobs.create_job(
        args.room,
        owner_persona=args.owner,
        goal=args.goal,
        acceptance=args.acceptance,
        stop=args.stop,
        job_id=args.job_id or None,
    )
    if args.json:
        print(json.dumps(doc, ensure_ascii=False))
    else:
        print(f"job {doc['id']} room={args.room} status={doc['status']}")
    return 0


def cmd_job_show(args: argparse.Namespace) -> int:
    from chip import jobs

    try:
        doc = jobs.load_job(args.room, args.job_id)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(doc, ensure_ascii=False))
    return 0


def cmd_job_list(args: argparse.Namespace) -> int:
    from chip import jobs

    rows = jobs.list_jobs(args.room, status=args.status)
    print(json.dumps(rows, ensure_ascii=False))
    return 0


def cmd_board_show(args: argparse.Namespace) -> int:
    from chip import boards

    doc = boards.load(args.room)
    print(json.dumps(doc, ensure_ascii=False))
    return 0


def cmd_board_append(args: argparse.Namespace) -> int:
    from chip import boards

    doc = boards.protocol_turn(boards.load(args.room), actor=args.as_persona, body=args.message)
    print(json.dumps({"version": doc["version"], "schema": doc["schema"]}, ensure_ascii=False))
    return 0


def cmd_routine(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from chip import routines

    now = datetime.now(timezone.utc)
    sub = args.routine_cmd
    try:
        if sub == "add":
            doc = routines.add(
                args.routine_id, schedule=args.schedule, kind=args.kind, prompt=args.prompt or "", now=now
            )
            print(json.dumps(doc, ensure_ascii=False))
        elif sub == "list":
            print(json.dumps(routines.list_all(), ensure_ascii=False))
        elif sub in ("enable", "disable"):
            doc = routines.set_enabled(args.routine_id, sub == "enable", now=now)
            print(f"routine {doc['id']} enabled={doc['enabled']} next_run={doc['next_run']}")
        elif sub == "delete":
            if not routines.delete(args.routine_id):
                print(f"ERROR: no routine: {args.routine_id}", file=sys.stderr)
                return 1
            print(f"routine {args.routine_id} deleted (history kept)")
        elif sub == "history":
            print(json.dumps(routines.history(args.routine_id, limit=args.limit), ensure_ascii=False))
        elif sub == "run-due":
            # Offline only: mock runner, or (with --via-jobs) a background job using the mock step.
            runner = routines.mock_runner
            if getattr(args, "via_jobs", False):
                from chip import bg_jobs

                if not bg_jobs.enabled():
                    raise bg_jobs.JobsDisabled(f"{bg_jobs.FLAG} is not enabled; refusing --via-jobs")
                step = bg_jobs.mock_step
                if getattr(args, "model_step", False):
                    # Real model turn for routine jobs and pm-tick jobs (CHIP_JOBS_MODEL_STEP=1).
                    from chip import bg_model_step

                    step = bg_model_step.make_model_step(use_mock=True if getattr(args, "mock", False) else None)
                runner = bg_jobs.routine_runner(step=step)
                from chip import pm_summary, pm_tick

                # kind=pm-tick routines run the PM tick (live inputs -> pick -> run); needs CHIP_PM_TICK=1.
                runner = pm_tick.routine_dispatch(runner, make_tick=lambda: _pm_tick_once(None, step=step))
                # kind=pm-summary routines post the daily summary; needs CHIP_PM_SUMMARY=1 + webhook env.
                runner = pm_summary.routine_dispatch(runner)
            rows = routines.tick(now, runner)
            print(json.dumps(rows, ensure_ascii=False))
        else:  # pragma: no cover - argparse enforces choices
            return 64
    except (routines.RoutinesDisabled, JobsDisabledError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except RuntimeError as exc:  # ModelStepDisabled from --model-step
        if type(exc).__name__ != "ModelStepDisabled":
            raise
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def _pm_tick_once(args: argparse.Namespace | None, *, dry_run: bool = False, step=None) -> dict:
    """Build adapters from args/env and run one PM tick (mock step unless --model-step or ``step``)."""
    from datetime import datetime, timezone
    from pathlib import Path

    from chip import bg_jobs, bg_picker, pm_tick

    step = step or bg_jobs.mock_step
    if args is not None and getattr(args, "model_step", False):
        from chip import bg_model_step

        step = bg_model_step.make_model_step(use_mock=True if getattr(args, "mock", False) else None)
    alerts = None
    if not (args is not None and getattr(args, "no_alerts", False)):
        raw = getattr(args, "alerts", None) if args is not None else None
        alerts = pm_tick.AlertStream(Path(raw) if raw else pm_tick.default_alerts_path())
    extra: list = []
    if args is not None and getattr(args, "linear_input", None):
        extra = bg_picker.read_items(Path(args.linear_input))
    return pm_tick.tick(
        datetime.now(timezone.utc),
        alerts=alerts,
        linear=pm_tick.linear_client_from_env(),
        extra_items=extra,
        step=step,
        dry_run=dry_run,
    )


def cmd_bgjob(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from chip import bg_jobs

    now = datetime.now(timezone.utc)
    sub = args.bgjob_cmd
    try:
        if sub == "enqueue":
            doc = bg_jobs.enqueue(
                args.prompt,
                now=now,
                mode=args.mode,
                max_hops=args.max_hops,
                max_spend_usd=args.max_spend,
                source=args.source,
                job_id=args.job_id or None,
            )
            print(json.dumps(doc, ensure_ascii=False))
        elif sub == "list":
            print(json.dumps(bg_jobs.list_all(status=args.status), ensure_ascii=False))
        elif sub == "show":
            print(json.dumps(bg_jobs.load(args.job_id), ensure_ascii=False, indent=2))
        elif sub == "cancel":
            doc = bg_jobs.cancel(args.job_id, now=now)
            print(f"job {doc['id']} cancelled")
        elif sub == "caps":
            print(json.dumps({"enabled": bg_jobs.enabled(), "caps": bg_jobs.caps(), "today": bg_jobs.daily_usage(now)}))
        elif sub == "run":
            step = bg_jobs.mock_step
            if getattr(args, "model_step", False):
                # Real model turn (CHIP_JOBS_MODEL_STEP=1): spend guard, thrift/stop rules, ledger rows.
                from chip import bg_model_step

                step = bg_model_step.make_model_step(use_mock=True if args.mock else None)
            print(json.dumps(bg_jobs.run_pending(now, step), ensure_ascii=False))
        elif sub == "pick":
            from pathlib import Path

            from chip import bg_picker

            items = bg_picker.read_items(Path(args.input))
            print(json.dumps(bg_picker.pick(items, now=now, dry_run=args.dry_run), ensure_ascii=False))
        elif sub == "tick":
            print(json.dumps(_pm_tick_once(args, dry_run=args.dry_run), ensure_ascii=False))
        elif sub == "summary":
            from datetime import date as _date

            from chip import pm_summary

            day = _date.fromisoformat(args.date) if args.date else now.astimezone(pm_summary.zone()).date()
            if args.post:
                print(json.dumps(pm_summary.post(day, force=args.force), ensure_ascii=False))
            else:
                summary = pm_summary.build(day)
                print(json.dumps(summary, ensure_ascii=False) if args.json else pm_summary.render(summary))
        else:  # pragma: no cover - argparse enforces choices
            return 64
    except bg_jobs.JobsDisabled as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except RuntimeError as exc:  # ModelStepDisabled / PickerDisabled
        if type(exc).__name__ not in {"ModelStepDisabled", "PickerDisabled", "PmTickDisabled", "SummaryDisabled"}:
            raise
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from chip import memory_store

    now = datetime.now(timezone.utc)
    sub = args.memory_cmd
    try:
        if sub == "add":
            rec = memory_store.remember(
                args.scope,
                args.text,
                by=args.by,
                source=args.source,
                now=now,
                kind=args.kind,
                key=args.key or None,
            )
            print(json.dumps(rec, ensure_ascii=False))
        elif sub == "list":
            rows = memory_store.list_records(args.scope, include_superseded=args.all)
            print(json.dumps(rows, ensure_ascii=False))
        elif sub == "search":
            scopes = args.scope or None
            rows = memory_store.recall(args.query, scopes=scopes, top_k=args.top_k, budget_chars=args.budget)
            print(json.dumps(rows, ensure_ascii=False))
        elif sub == "forget":
            if not memory_store.forget(args.record_id, by=args.by, now=now):
                print(f"ERROR: no memory: {args.record_id}", file=sys.stderr)
                return 1
            print(f"memory {args.record_id} forgotten")
        else:  # pragma: no cover - argparse enforces choices
            return 64
    except memory_store.MemoryRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_skill(args: argparse.Namespace) -> int:
    from chip import skills

    sub = args.skill_cmd
    try:
        cat = skills.load_catalog()
        if sub == "list":
            out: dict[str, object] = {"skills": [s.summary() for s in cat.list()]}
            if args.skipped:
                out["skipped"] = cat.skipped
            print(json.dumps(out, ensure_ascii=False))
        elif sub == "show":
            sk = cat.get(args.skill_id)
            if sk is None:
                print(f"ERROR: no skill: {args.skill_id}", file=sys.stderr)
                return 1
            print(json.dumps({**sk.summary(), "body": sk.body}, ensure_ascii=False))
        elif sub == "search":
            rows = skills.search(cat, args.query, top_k=args.top_k)
            print(json.dumps([s.summary() for s in rows], ensure_ascii=False))
        elif sub == "add":
            body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else args.body
            sk = skills.add_skill(
                args.skill_id,
                name=args.name,
                description=args.description,
                body=body or "",
                triggers=[t for t in args.trigger],
                override=args.override,
                replace=args.replace,
                catalog=cat,
            )
            print(json.dumps(sk.summary(), ensure_ascii=False))
        elif sub == "delete":
            if not skills.delete_skill(args.skill_id):
                print(f"ERROR: no user skill: {args.skill_id}", file=sys.stderr)
                return 1
            print(f"skill {args.skill_id} deleted")
        else:  # pragma: no cover - argparse enforces choices
            return 64
    except skills.SkillError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from chip import mcp_client as mc

    sub = args.mcp_cmd
    try:
        if sub == "servers":
            out = {"enabled": mc.enabled(), "servers": [c.summary() for c in mc.load_config().values()]}
            print(json.dumps(out, ensure_ascii=False))
        elif sub == "audit":
            print(json.dumps(mc.read_audit(args.limit), ensure_ascii=False))
        elif sub == "pending":
            print(json.dumps(mc.pending_approvals(), ensure_ascii=False))
        elif sub == "offered":
            from chip import mcp_tools

            tools = mcp_tools.offered_tools()
            print(json.dumps({"enabled": mc.enabled(), "tools": tools, "prompt": mcp_tools.planner_prompt(tools)}, ensure_ascii=False))
        elif sub == "receipts":
            from chip import mcp_tools

            print(json.dumps(mcp_tools.read_receipts(args.limit), ensure_ascii=False))
        elif sub in {"approve", "reject"}:
            rec = mc.decide_approval(args.approval_id, by=args.by, approve=sub == "approve")
            print(json.dumps(rec, ensure_ascii=False))
        elif sub == "tools":
            print(json.dumps(mc.list_tools(args.server), ensure_ascii=False))
        elif sub == "call":
            try:
                arguments = json.loads(args.args) if args.args else {}
            except json.JSONDecodeError as exc:
                print(f"ERROR: --args is not JSON: {exc}", file=sys.stderr)
                return 2
            res = mc.call(args.server, args.tool, arguments, requested_by=args.by, approval_id=args.approval)
            print(json.dumps(res, ensure_ascii=False))
            if res["status"] == "pending":
                return 4
        else:  # pragma: no cover - argparse enforces choices
            return 64
    except mc.McpDisabled as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except (mc.McpRefused, mc.McpConfigError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except mc.McpProtocolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
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

    job_p = sub.add_parser("job", help="PM orchestrator job board")
    job_sub = job_p.add_subparsers(dest="job_cmd", required=True)
    job_create = job_sub.add_parser("create", parents=[common], help="Create a job slice")
    job_create.add_argument("room")
    job_create.add_argument("--owner", default="pm", help="Owner persona (default pm)")
    job_create.add_argument("--goal", required=True)
    job_create.add_argument("--acceptance", required=True)
    job_create.add_argument("--stop", required=True)
    job_create.add_argument("--job-id", default="", help="Optional fixed id (smoke)")
    job_create.add_argument("--json", action="store_true")
    job_create.set_defaults(func=cmd_job_create)
    job_show = job_sub.add_parser("show", parents=[common], help="Show one job JSON")
    job_show.add_argument("room")
    job_show.add_argument("job_id")
    job_show.set_defaults(func=cmd_job_show)
    job_list = job_sub.add_parser("list", parents=[common], help="List jobs in a room")
    job_list.add_argument("room")
    job_list.add_argument("--status", default=None, help="Filter by status")
    job_list.set_defaults(func=cmd_job_list)

    routine_p = sub.add_parser("routine", help="Scheduled routines + run history (CHIP_ROUTINES gates run-due)")
    routine_sub = routine_p.add_subparsers(dest="routine_cmd", required=True)
    r_add = routine_sub.add_parser("add", parents=[common], help="Add a routine")
    r_add.add_argument("routine_id")
    r_add.add_argument("--schedule", required=True, help="every:<N><s|m|h|d> or 'cron:<min hour dom month dow>' (UTC)")
    r_add.add_argument("--kind", required=True, help="Job kind the runner dispatches")
    r_add.add_argument("--prompt", default="")
    routine_sub.add_parser("list", parents=[common], help="List routines")
    for name in ("enable", "disable", "delete"):
        rp = routine_sub.add_parser(name, parents=[common], help=f"{name.capitalize()} a routine")
        rp.add_argument("routine_id")
    r_hist = routine_sub.add_parser("history", parents=[common], help="Show run history (JSONL rows)")
    r_hist.add_argument("routine_id")
    r_hist.add_argument("--limit", type=int, default=None)
    r_due = routine_sub.add_parser("run-due", parents=[common], help="Run due routines once (mock; needs CHIP_ROUTINES=1)")
    r_due.add_argument(
        "--via-jobs", action="store_true", help="Dispatch each due routine as a capped background job (needs CHIP_JOBS=1)"
    )
    r_due.add_argument(
        "--model-step", action="store_true", help="With --via-jobs: run jobs with the real model step (CHIP_JOBS_MODEL_STEP=1)"
    )
    routine_p.set_defaults(func=cmd_routine)

    bg_p = sub.add_parser("bgjob", help="Background agent jobs with hop/spend/daily/concurrency caps (CHIP_JOBS gates run)")
    bg_sub = bg_p.add_subparsers(dest="bgjob_cmd", required=True)
    bg_enq = bg_sub.add_parser("enqueue", parents=[common], help="Queue a background job")
    bg_enq.add_argument("--prompt", required=True)
    bg_enq.add_argument("--mode", choices=("read", "mutate"), default="read")
    bg_enq.add_argument("--max-hops", type=int, default=None)
    bg_enq.add_argument("--max-spend", type=float, default=None, help="Per-job USD ceiling")
    bg_enq.add_argument("--source", default="cli")
    bg_enq.add_argument("--job-id", default="")
    bg_list = bg_sub.add_parser("list", parents=[common], help="List background jobs")
    bg_list.add_argument("--status", default=None)
    for name in ("show", "cancel"):
        bp = bg_sub.add_parser(name, parents=[common], help=f"{name.capitalize()} a background job")
        bp.add_argument("job_id")
    bg_sub.add_parser("caps", parents=[common], help="Effective caps and today's usage")
    bg_run = bg_sub.add_parser("run", parents=[common], help="Run startable queued jobs once (mock step; needs CHIP_JOBS=1)")
    bg_run.add_argument(
        "--model-step", action="store_true", help="Use the real model-turn step (needs CHIP_JOBS_MODEL_STEP=1)"
    )
    bg_pick = bg_sub.add_parser("pick", parents=[common], help="PM picker: enqueue read jobs from alerts/Linear inputs (CHIP_PICKER=1)")
    bg_pick.add_argument("--input", required=True, help="JSON list or JSONL of {kind, id, title, ...}")
    bg_pick.add_argument("--dry-run", action="store_true", help="Show what would be enqueued; writes nothing")
    bg_tick = bg_sub.add_parser(
        "tick", parents=[common], help="PM tick: alert stream + Linear poll -> pick -> run (CHIP_PM_TICK=1)"
    )
    bg_tick.add_argument("--alerts", default=None, help="Alert JSONL path (default CHIP_PM_ALERTS_PATH or relay dispatch log)")
    bg_tick.add_argument("--no-alerts", action="store_true", help="Skip the alert stream adapter")
    bg_tick.add_argument("--linear-input", default=None, help="Extra picker inputs (JSON list / JSONL) instead of a live poll")
    bg_tick.add_argument("--model-step", action="store_true", help="Run jobs with the real model step (CHIP_JOBS_MODEL_STEP=1)")
    bg_tick.add_argument("--dry-run", action="store_true", help="Show picks; writes nothing (cursor included)")
    bg_sum = bg_sub.add_parser(
        "summary", parents=[common], help="Daily PM summary: jobs + why, PR links, refusals, spend, waiting (CHIP_PM_SUMMARY gates --post)"
    )
    bg_sum.add_argument("--date", default=None, help="Local day YYYY-MM-DD (default today in CHIP_PM_SUMMARY_TZ)")
    bg_sum.add_argument("--json", action="store_true", help="Print the summary dict instead of the message")
    bg_sum.add_argument("--post", action="store_true", help="Post to CHIP_PM_SUMMARY_WEBHOOK (+ ping webhook if decisions wait)")
    bg_sum.add_argument("--force", action="store_true", help="Post even if this day was already sent")
    bg_p.set_defaults(func=cmd_bgjob)

    memory_p = sub.add_parser("memory", help="Durable memory: remember/forget with provenance, user/room scopes")
    memory_sub = memory_p.add_subparsers(dest="memory_cmd", required=True)
    m_add = memory_sub.add_parser("add", parents=[common], help="Remember one fact")
    m_add.add_argument("text")
    m_add.add_argument("--scope", default="user", help="'user' (default) or 'room:<name>'")
    m_add.add_argument("--by", default="cli", help="Who is writing (provenance)")
    m_add.add_argument("--source", default="cli", help="Where the fact came from (provenance)")
    m_add.add_argument("--kind", default="fact", choices=["fact", "preference", "decision"])
    m_add.add_argument("--key", default="", help="Stable key; a newer fact with the same key supersedes")
    m_list = memory_sub.add_parser("list", parents=[common], help="List active facts")
    m_list.add_argument("--scope", default=None)
    m_list.add_argument("--all", action="store_true", help="Include superseded facts")
    m_search = memory_sub.add_parser("search", parents=[common], help="Keyword recall across scopes")
    m_search.add_argument("query")
    m_search.add_argument("--scope", action="append", default=[], help="Repeatable; default all scopes")
    m_search.add_argument("--top-k", type=int, default=5)
    m_search.add_argument("--budget", type=int, default=2000, help="Max total characters returned")
    m_forget = memory_sub.add_parser("forget", parents=[common], help="Forget a fact by id")
    m_forget.add_argument("record_id")
    m_forget.add_argument("--by", default="cli")
    memory_p.set_defaults(func=cmd_memory)

    skill_p = sub.add_parser("skill", help="Reusable skills catalog (pack skills read-only, user skills in data dir)")
    skill_sub = skill_p.add_subparsers(dest="skill_cmd", required=True)
    s_list = skill_sub.add_parser("list", parents=[common], help="List skills (pack + user)")
    s_list.add_argument("--skipped", action="store_true", help="Also show invalid/shadowing files and why")
    s_show = skill_sub.add_parser("show", parents=[common], help="Show one skill with its body")
    s_show.add_argument("skill_id")
    s_search = skill_sub.add_parser("search", parents=[common], help="Search skills by name/description/triggers")
    s_search.add_argument("query")
    s_search.add_argument("--top-k", type=int, default=5)
    s_add = skill_sub.add_parser("add", parents=[common], help="Add a user skill")
    s_add.add_argument("skill_id")
    s_add.add_argument("--name", required=True)
    s_add.add_argument("--description", required=True, help="When to use this skill")
    s_add.add_argument("--body", default="")
    s_add.add_argument("--body-file", default="")
    s_add.add_argument("--trigger", action="append", default=[], help="Repeatable trigger phrase")
    s_add.add_argument("--override", action="store_true", help="Allow shadowing a pack skill with the same id")
    s_add.add_argument("--replace", action="store_true", help="Rewrite an existing user skill")
    s_del = skill_sub.add_parser("delete", parents=[common], help="Delete a user skill")
    s_del.add_argument("skill_id")
    skill_p.set_defaults(func=cmd_skill)

    mcp_p = sub.add_parser("mcp", help="MCP stdio client: allowlisted tools with auto/ask/deny approval (CHIP_MCP=1)")
    mcp_sub = mcp_p.add_subparsers(dest="mcp_cmd", required=True)
    mcp_sub.add_parser("servers", parents=[common], help="Show configured servers and tool policy")
    mc_tools = mcp_sub.add_parser("tools", parents=[common], help="List a server's advertised tools with policy level")
    mc_tools.add_argument("server")
    mc_call = mcp_sub.add_parser("call", parents=[common], help="Call a tool (policy-gated, audited)")
    mc_call.add_argument("server")
    mc_call.add_argument("tool")
    mc_call.add_argument("--args", default="", help="JSON object of tool arguments")
    mc_call.add_argument("--approval", default=None, help="Approved approval id for an ask-level tool")
    mc_call.add_argument("--by", default="cli", help="Requester identity recorded in the audit log")
    mcp_sub.add_parser("pending", parents=[common], help="List pending approvals")
    for verb in ("approve", "reject"):
        mc_dec = mcp_sub.add_parser(verb, parents=[common], help=f"{verb.title()} a pending call (not by its requester)")
        mc_dec.add_argument("approval_id")
        mc_dec.add_argument("--by", required=True)
    mcp_sub.add_parser("offered", parents=[common], help="Tools offered to model turns / jobs (auto + ask) and the planner prompt")
    mc_rec = mcp_sub.add_parser("receipts", parents=[common], help="Show recent tool-call receipts from turns and jobs")
    mc_rec.add_argument("--limit", type=int, default=50)
    mc_audit = mcp_sub.add_parser("audit", parents=[common], help="Show recent audit rows")
    mc_audit.add_argument("--limit", type=int, default=50)
    mcp_p.set_defaults(func=cmd_mcp)

    board_p = sub.add_parser("board", help="Room hypothesis board")
    board_sub = board_p.add_subparsers(dest="board_cmd", required=True)
    board_show = board_sub.add_parser("show", parents=[common], help="Print the room board JSON")
    board_show.add_argument("room")
    board_show.set_defaults(func=cmd_board_show)
    board_append = board_sub.add_parser("append", parents=[common], help="Append one hypothesis")
    board_append.add_argument("room")
    board_append.add_argument("--as", dest="as_persona", required=True)
    board_append.add_argument("-m", dest="message", required=True)
    board_append.set_defaults(func=cmd_board_append)

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
        help="Speaker id (persona key from the active pack when defined)",
    )
    room.add_argument("--json", action="store_true", help="Print one JSON object on stdout")
    room.add_argument("--task-id", dest="task_id", default=None,
                      help=pack_values.cli_room_task_id_help())
    room.add_argument("--task-type", dest="task_type", default=None,
                      help=f"task class label (one of {sorted(task_ledger.TASK_TYPES_KNOWN)}; default 'other')")
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
    room.add_argument(
        "--close",
        action="store_true",
        help="Close the room session and mine advisory memories from its JSONL segment",
    )
    room.set_defaults(func=cmd_room)

    tool_p = sub.add_parser("tool", help="Allowlisted task execution tools")
    tool_sub = tool_p.add_subparsers(dest="tool_cmd", required=True)

    tool_list = tool_sub.add_parser("list", parents=[common], help="Show allowlisted tools")
    tool_list.add_argument("--json", action="store_true", help="JSON array of tool specs")
    tool_list.set_defaults(func=cmd_tool_list)

    tool_run = tool_sub.add_parser("run", parents=[common], help="Run one allowlisted tool (gated)")
    tool_run.add_argument("tool_id", help="Tool id from the active pack tool manifest")
    tool_run.add_argument("extra_args", nargs=argparse.REMAINDER, help="Extra argv segments (if allowed)")
    tool_run.add_argument(
        "--execute",
        action="store_true",
        help="Actually spawn the process (default: dry-run unless CHIP_TOOL_AUTO=1)",
    )
    tool_run.add_argument("--timeout", type=float, default=None, help="Override manifest timeout (seconds)")
    tool_run.add_argument("--cwd", default=None, help="Working directory for the subprocess")
    tool_run.add_argument("--json", action="store_true", help="Print one JSON object")
    tool_run.set_defaults(func=cmd_tool_run)

    task_p = sub.add_parser("task", help="Task envelopes (tool_invocations + execution_result)")
    task_sub = task_p.add_subparsers(dest="task_cmd", required=True)

    task_create = task_sub.add_parser("create", parents=[common], help="Create a pending task")
    task_create.add_argument("--tool", dest="tool_id", required=True, help="Allowlisted tool id")
    task_create.add_argument("--args", nargs="*", default=[], help="Extra argv for the tool")
    task_create.add_argument("--note", default=None, help="Optional note")
    task_create.add_argument("--json", action="store_true")
    task_create.set_defaults(func=cmd_task_create)

    task_show = task_sub.add_parser("show", parents=[common], help="Show task envelope")
    task_show.add_argument("task_id")
    task_show.add_argument("--json", action="store_true")
    task_show.set_defaults(func=cmd_task_show)

    task_run = task_sub.add_parser("run", parents=[common], help="Run task tool (gated)")
    task_run.add_argument("task_id")
    task_run.add_argument("--execute", action="store_true", help="Actually execute (see CHIP_TOOL_AUTO)")
    task_run.add_argument("--timeout", type=float, default=None)
    task_run.add_argument("--cwd", default=None)
    task_run.add_argument("--json", action="store_true")
    task_run.set_defaults(func=cmd_task_run)

    tf = sub.add_parser("taskflow", parents=[common], help="Phase-2 circuit CLI (brief|claim|bind|gate)")
    tf_sub = tf.add_subparsers(dest="sub", required=True)
    tf_gate = tf_sub.add_parser("gate", help="0 = open, 4 = palace gate BLOCKED")
    tf_gate.set_defaults(func=cmd_taskflow)
    tf_brief = tf_sub.add_parser("brief", help="emit a task brief for an anchored task")
    tf_brief.add_argument("--task-id", dest="task_id", required=True)
    tf_brief.add_argument("--agent", dest="agent", required=True)
    tf_brief.add_argument("--room", required=True)
    tf_brief.add_argument("--goal", required=True)
    tf_brief.add_argument("--ac", default="", help="acceptance items joined with '|'")
    tf_brief.set_defaults(func=cmd_taskflow)
    tf_claim = tf_sub.add_parser("claim", help="claim a scope (first-claim-wins; rc 5 on conflict)")
    tf_claim.add_argument("--room", required=True)
    tf_claim.add_argument("--agent", dest="agent", required=True)
    tf_claim.add_argument("--scope", required=True)
    tf_claim.set_defaults(func=cmd_taskflow)
    tf_bind = tf_sub.add_parser("bind", help="bind a task result (receipt body + palace write)")
    tf_bind.add_argument("--task-id", dest="task_id", required=True)
    tf_bind.add_argument("--room", required=True)
    tf_bind.add_argument("--agent", dest="agent", required=True)
    tf_bind.add_argument("--outcome", required=True)
    tf_bind.add_argument("--receipts", default="", help=pack_values.cli_taskflow_bind_receipts_help())
    tf_bind.add_argument("--lessons", default="")
    tf_bind.add_argument("--task-type", dest="task_type", default="docs")
    tf_bind.add_argument("--linear-anchor", dest="linear_anchor", default=None)
    tf_bind.set_defaults(func=cmd_taskflow)

    pack_p = sub.add_parser("pack", help="System pack helpers (validate)")
    pack_sub = pack_p.add_subparsers(dest="pack_cmd")
    pack_validate = pack_sub.add_parser(
        "validate",
        help="Pass/fail check for a chip-system-pack/v0 manifest (surfaces, allowlists, no path escape)",
    )
    pack_validate.add_argument(
        "--pack",
        dest="pack_name",
        default=None,
        help="Pack name under systems/ (default: CHIP_SYSTEM_PACK / DEFAULT_PACK / acme-example)",
    )
    pack_validate.add_argument(
        "--pack-dir",
        default=None,
        help="Absolute or relative path to a pack directory containing system.json (overrides --pack)",
    )
    pack_validate.add_argument(
        "--no-leak",
        action="store_true",
        help="Force public no-leak token scan (default on for systems/ and fixtures packs)",
    )
    pack_validate.add_argument("--json", action="store_true", help="Emit ValidationResult JSON")
    pack_validate.add_argument(
        "--repo-root",
        default=None,
        help=(
            "Repository root for repo-anchored surfaces (systems/..., docs/...). "
            "Required when validating an out-of-tree pack whose surfaces reference its own repo; "
            "defaults to this checkout, which is wrong for foreign packs."
        ),
    )
    pack_validate.set_defaults(func=cmd_pack_validate)

    pack_init = pack_sub.add_parser(
        "init",
        help="Scaffold a fresh pack from the generic validate-ok fixture (refuses overwrite)",
    )
    pack_init.add_argument(
        "--pack-dir",
        required=True,
        help="Target pack directory to create (must not contain an existing system.json etc.)",
    )
    pack_init.add_argument("--force", action="store_true", help="Replace existing scaffold files on purpose")
    pack_init.add_argument("--json", action="store_true")
    pack_init.set_defaults(func=cmd_pack_init)

    pack_conf = pack_sub.add_parser(
        "conformance",
        help="Same validator as validate, stricter: every surface must be documented contract",
    )
    pack_conf.add_argument(
        "--pack", dest="pack_name", default=None, help="Pack name under systems/ (or CHIP_SYSTEM_PACK)"
    )
    pack_conf.add_argument("--pack-dir", default=None, help="Pack directory containing system.json")
    pack_conf.add_argument("--repo-root", default=None, help="Repository root when validating an out-of-tree pack")
    pack_conf.add_argument("--json", action="store_true")
    pack_conf.set_defaults(func=cmd_pack_conformance)

    host_exec_p = sub.add_parser(
        "host-exec",
        help="Typed dry-run seam over the host-hook contract (docs/host-hooks.md); real execution is plugin-side",
    )
    host_exec_sub = host_exec_p.add_subparsers(dest="host_exec_cmd")
    host_exec_run = host_exec_sub.add_parser("run", help="Dry-run one allowlisted hook through the manifest adapter")
    host_exec_run.add_argument("hook", help="Host-hook name (must be declared in the manifest)")
    host_exec_run.add_argument(
        "--manifest",
        default=None,
        help="host-hooks manifest path (default: plugins/example/host-hooks.yaml)",
    )
    host_exec_run.add_argument("--json", action="store_true")
    host_exec_run.set_defaults(func=cmd_host_exec_run)

    done_gate_p = sub.add_parser(
        "done-gate",
        help="Done-discipline gate: checked acceptance boxes must carry verified-as-of stamps (offline, no network). Always enforces Done discipline.",
    )
    done_gate_p.add_argument("--file", default=None, help="Markdown file to check (defaults to stdin)")
    done_gate_p.add_argument(
        "--scan",
        action="store_true",
        help="Deprecated no-op; Done gate always assumes declare-Done (kept for scripts)",
    )
    done_gate_p.add_argument("--declare-done", action="store_true", help="Deprecated no-op (always enforced)")
    done_gate_p.add_argument("--json", action="store_true")
    done_gate_p.set_defaults(func=cmd_done_gate)

    sweep_p = sub.add_parser(
        "done-gate-sweep",
        help="Board sweep over a Linear issues JSON export — severity-ranked Done-discipline findings (read-only)",
    )
    sweep_p.add_argument("--issues-file", required=True, help="JSON array of {id,status,statusType,description}")
    sweep_p.add_argument("--json", action="store_true")
    sweep_p.set_defaults(func=cmd_done_gate_sweep)

    return parser



def cmd_pack_validate(args: argparse.Namespace) -> int:
    from chip.pack_validate import format_result, validate_pack

    result = validate_pack(
        pack=getattr(args, "pack_name", None) or None,
        pack_dir=getattr(args, "pack_dir", None) or None,
        root=Path(args.repo_root).expanduser().resolve() if getattr(args, "repo_root", None) else None,
        check_no_leak=True if getattr(args, "no_leak", False) else None,
    )
    print(format_result(result, as_json=bool(args.json)))
    return 0 if result.ok else 2


def cmd_pack_init(args: argparse.Namespace) -> int:
    import json

    from chip.pack_validate import init_pack

    try:
        created = init_pack(args.pack_dir, force=getattr(args, "force", False))
    except (FileExistsError, FileNotFoundError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}))
        else:
            print(f"pack init: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, "created": [str(p) for p in created]}))
    else:
        print(f"pack init: created {len(created)} file(s) under {Path(args.pack_dir).name}")
    return 0


def cmd_pack_conformance(args: argparse.Namespace) -> int:
    from chip.pack_validate import conformance_check, format_result

    result = conformance_check(
        pack=getattr(args, "pack_name", None) or None,
        pack_dir=getattr(args, "pack_dir", None) or None,
        root=Path(args.repo_root).expanduser().resolve() if getattr(args, "repo_root", None) else None,
    )
    print(format_result(result, as_json=bool(args.json)))
    return 0 if result.ok else 2


def cmd_host_exec_run(args: argparse.Namespace) -> int:
    import json

    from chip.host_exec import ManifestAdapter

    manifest_path = Path(args.manifest).expanduser() if args.manifest else Path("plugins") / "example" / "host-hooks.yaml"
    # YAML is optional for engine callers; the shipped example is YAML.
    try:
        import yaml  # type: ignore[import-untyped]  # contract manifests are YAML; importer may not need it
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except ImportError:
        print("host-exec: PyYAML is required to read the manifest (pip install pyyaml)", file=sys.stderr)
        return 64
    if not isinstance(manifest, dict):
        print(f"host-exec: manifest is not an object: {manifest_path}", file=sys.stderr)
        return 64
    try:
        adapter = ManifestAdapter(manifest)
        result = adapter.run(args.hook)
    except (ValueError, NotImplementedError) as exc:
        print(f"host-exec: {exc}", file=sys.stderr)
        return 64
    detail = json.dumps(result.to_dict(), ensure_ascii=False) if args.json else (
        f"host-exec: exit={result.exit} hook={result.hook} detail={result.detail} receipt={result.receipt_path}"
    )
    print(detail)
    return result.exit


def cmd_done_gate(args: argparse.Namespace) -> int:
    import sys

    from chip.done_gate import may_flip_done, parse_done_state

    if args.file:
        path = Path(args.file).expanduser()
        if not path.is_file():
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            return 1
        text = path.read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    scan = parse_done_state(text, declare_done=True)
    allow, reason = may_flip_done(text)
    payload = {"allow_done": allow, "reason": reason, **scan.to_dict()}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if allow else 2
    ok_stats = "DONE-GATE ALLOW" if allow else "DONE-GATE REFUSE"
    print(f"{ok_stats}: {reason}")
    for b in scan.boxes:
        mark = "✅" if (b.checked and b.verified) else "❌"
        print(f"  {mark} [{'x' if b.checked else ' '}] {b.text[:70]} (section={b.section[:30]!r})")
    return 0 if allow else 2


def cmd_done_gate_sweep(args: argparse.Namespace) -> int:
    import sys

    from chip.done_gate import sweep

    path = Path(args.issues_file).expanduser()
    if not path.is_file():
        print(f"ERROR: issues file not found: {path}", file=sys.stderr)
        return 1
    try:
        issues = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: bad issues JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(issues, list):
        print("ERROR: issues file must be a JSON array", file=sys.stderr)
        return 1
    findings = sweep(issues, environ=os.environ)
    if args.json:
        print(json.dumps({"count": len(findings), "findings": findings}, ensure_ascii=False))
        return 0 if not findings else 2
    print(f"DONE-GATE SWEEP: {len(findings)} finding(s)")
    for f in findings:
        print(
            f"  [{f['severity']}] {f['id']} status={f['status']} "
            f"checked={f['checked']} stamps={f['stamps']} — {f['reason']}"
        )
    return 0 if not findings else 2


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
