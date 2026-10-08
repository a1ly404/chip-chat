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
        learnings_block=learnings.render_learnings_block(persona),
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
    try:
        text, payload, routed, ledger = runtime.complete(
            messages,
            model=config.model_for_persona(persona),
            max_tokens=config.ROOM_COMPLETION_MAX_TOKENS,
            use_mock=use_mock,
            command="room",
        )
    except openrouter.ChipConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
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
