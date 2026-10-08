"""Live case intake: parse → taxonomy → cache → echo. Alert-channel feed owner."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from chip import config as chip_config
from chip import openrouter
from chip.config import data_dir
from chip.store import append_jsonl
from chip_relay.case_intake import (
    OperatorPager,
    CaseFile,
    IntakeResult,
    Receipt,
    StrikeBook,
    correlate_dark,
    echo_for_correction,
    enabled,
    load_limits,
    load_taxonomy,
    local_dedupe,
    match_taxonomy,
    parse_freeform,
    write_dead_letter,
)
from chip_relay.case_intake_coverage import (
    check_feed_halt,
    class_killed,
    record_outcome,
)
from chip_relay.case_intake_escalation import handle_novel_shape
from chip_relay.case_intake_failure_capture import capture as capture_fixture
from chip_relay.case_intake_graduation import check_graduation
from chip_relay.case_intake_solver import attempt_solve
from chip_relay.case_intake_spend_wire import (
    apply_post_parse_spend,
    log_intake_spend,
    monthly_gate_for_parse,
)
from chip_relay.config import RoomBinding
from chip_relay.dispatch import DispatchResult, Incoming, WebhookResult, _fail

_STATE: dict[str, Any] | None = None
_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _skip_spend(environ: dict[str, str]) -> bool:
    return environ.get("CASE_INTAKE_SKIP_SPEND_GATE", "1").strip() in {"1", "true", "yes"}


def intake_channels(limits: dict[str, Any], environ: dict[str, str]) -> set[str]:
    raw = environ.get("CASE_INTAKE_CHANNELS") or str(limits.get("intake_channels") or "")
    return {c.strip() for c in raw.split(",") if c.strip()}


def channel_intake_active(channel: str, environ: dict[str, str]) -> bool:
    if not enabled(environ):
        return False
    from chip_relay.case_intake_graduation import intake_disabled_by_graduation

    if intake_disabled_by_graduation(environ):
        return False
    limits = load_limits()
    return channel.strip() in intake_channels(limits, environ)


def monitoring_feed_active(room: RoomBinding, environ: dict[str, str]) -> bool:
    from chip_relay.monitoring_listen import room_is_monitoring

    return enabled(environ) and room_is_monitoring(room) and channel_intake_active(room.discord_channel, environ)


def _state_root() -> Path:
    root = data_dir() / "case_intake"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _load_state() -> dict[str, Any]:
    global _STATE
    if _STATE is not None:
        return _STATE
    path = _state_root() / "state.json"
    if path.is_file():
        try:
            _STATE = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            _STATE = {}
    else:
        _STATE = {}
    _STATE.setdefault("recent_cases", [])
    _STATE.setdefault("echo_pending", {})
    _STATE.setdefault("pager_last", {})
    return _STATE


def _save_state(state: dict[str, Any]) -> None:
    path = _state_root() / "state.json"
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _strike() -> StrikeBook:
    book = StrikeBook()
    state = _load_state()
    for cls, until in (state.get("strike_until") or {}).items():
        book.blocked_until[str(cls)] = float(until)
    return book


def _pager() -> OperatorPager:
    p = OperatorPager()
    for cls, ts in (_load_state().get("pager_last") or {}).items():
        p.last[str(cls)] = float(ts)
    return p


def _operator_ids(room: RoomBinding, environ: dict[str, str]) -> set[str]:
    from chip import system_pack

    default = system_pack.operator_discord_id(environ) or ""
    raw = system_pack.env_get("CHIP_OPERATOR_DISCORD_IDS", environ, default)
    ids = {x.strip() for x in raw.split(",") if x.strip()}
    for did, persona in room.speakers_by_id.items():
        if persona == "pm":
            ids.add(did)
    return ids


def _router_log() -> Path:
    path = Path(chip_config.REPO_ROOT) / "docs" / "logs" / "router_failures.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _receipt_log() -> Path:
    return _state_root() / "parse_receipts.jsonl"


def _log_receipt(row: dict[str, Any]) -> None:
    append_jsonl(_receipt_log(), row)


def _open_cache_path() -> Path:
    return Path(chip_config.REPO_ROOT) / "config" / "case_open_cache.json"


def _load_open_cache() -> list[dict[str, Any]]:
    path = _open_cache_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return list(data.get("cases") or [])


def _append_open_cache(case: CaseFile) -> None:
    path = _open_cache_path()
    rows = _load_open_cache()
    rows.append(case.as_dict())
    path.write_text(json.dumps({"cases": rows}, indent=2) + "\n", encoding="utf-8")


SYNTHETIC_MARKER = re.compile(r"\bsynthetic\s*[:=]\s*true\b", re.IGNORECASE)
ADAPTER_TO_LADDER_ENV = "CASE_INTAKE_ADAPTER_TO_LADDER"


def adapter_continues_to_intake(text: str, environ: dict[str, str]) -> bool:
    """Synthetic smokes continue into solver/ladder; real adapter alerts echo-only unless operator flips the flag."""
    if SYNTHETIC_MARKER.search(text or ""):
        return True
    return environ.get(ADAPTER_TO_LADDER_ENV, "0").strip().lower() in {"1", "true", "yes"}


def _case_file_path(case_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", case_id) or "unknown"
    return _state_root() / "cases" / f"{safe}.json"


def extract_case_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fence = _JSON_FENCE.search(stripped)
    if fence:
        stripped = fence.group(1).strip()
    if stripped.upper().startswith("PENDING_CLARIFY"):
        return {"marker": "PENDING_CLARIFY", "missing": ["id", "description", "repro_context", "evidence_path"]}
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid json: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("parser output not object")
    return parsed


def live_parser(msg: str, model: str) -> dict[str, Any]:
    key = openrouter.require_api_key()
    personas = chip_config.load_personas()
    system = (personas.get("dispatcher") or {}).get("system") or "Output JSON case file only."
    try:
        payload = openrouter.chat_completion(
            key,
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": msg},
            ],
            max_tokens=768,
        )
    except openrouter.ChipConfigError as exc:
        if "HTTP " in str(exc):
            raise RuntimeError(f"non-200 {exc}") from exc
        raise
    text = openrouter.extract_assistant_text(payload)
    if not text.strip():
        raise RuntimeError(f"non-200 empty content model={model}")
    parsed = extract_case_json(text)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    parsed["_tokens"] = int(usage.get("total_tokens") or 0)
    parsed["_response_sha"] = hashlib.sha256(text.encode()).hexdigest()[:16]
    return parsed


def _format_echo(case: CaseFile, *, tax: str, deduped: bool, correlated: bool, monitoring: bool) -> str:
    body = json.dumps(case.as_dict(), indent=2)
    flags = []
    if deduped:
        flags.append("deduped=local_cache")
    if correlated:
        flags.append("correlated_dark=collapsed")
    flag_line = f" ({', '.join(flags)})" if flags else ""
    head = "case-intake monitoring" if monitoring else "case-intake echo"
    tail = "" if monitoring else " — reply with JSON corrections (operator or originator only)"
    return (
        f"{head}{tail}\n"
        f"taxonomy: {tax}{flag_line}\n"
        "solver: mock — no live route\n"
        f"{body}"
    )


def _post_pager(
    payload: dict[str, Any],
    *,
    environ: dict[str, str],
    limits: dict[str, Any],
) -> bool:
    env_name = str(limits.get("pager_webhook_env") or "DISCORD_MONITORING_WEBHOOK_URL")
    url = environ.get(env_name, "").strip()
    if not url:
        chip_config.load_dotenv()
        url = os.environ.get(env_name, "").strip()
    if not url:
        return False
    from chip import system_pack

    ping_id = (
        environ.get("DISCORD_PING_USER_ID") or system_pack.operator_discord_id(environ) or ""
    ).strip()
    content = f"<@{ping_id}> case-intake: {payload.get('summary', '')}\n{payload.get('link', '')}"
    body = json.dumps({"content": content[:1900]}).encode()
    import urllib.request

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "chip-relay-case-intake"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _strike_class(case: CaseFile | None, fallback: str = "intake-router") -> str:
    if case and case.fingerprint_class:
        return case.fingerprint_class
    return fallback


def handle_monitoring_intake(
    content: str,
    room: RoomBinding,
    *,
    webhook: Callable[[str, str, str], WebhookResult],
    environ: dict[str, str],
    author_id: str = "",
    author: str = "monitoring",
    webhook_id: str | None = None,
    message_id: str = "",
) -> DispatchResult:
    incoming = Incoming(
        channel=room.discord_channel,
        content=content,
        author=author,
        author_id=author_id,
    )
    return _process_alert(
        incoming,
        room,
        webhook=webhook,
        environ=environ,
        monitoring=True,
        webhook_id=webhook_id,
        message_id=message_id,
    )


def handle_case_intake(
    incoming: Incoming,
    room: RoomBinding,
    *,
    webhook: Callable[[str, str, str], WebhookResult],
    environ: dict[str, str],
) -> DispatchResult:
    return _process_alert(incoming, room, webhook=webhook, environ=environ, monitoring=False)


def _run_ladder_dispatch(case: CaseFile, *, environ: dict[str, str], case_path: Path) -> dict[str, Any] | None:
    """Read-only verdict ladder behind ``CASE_INTAKE_LADDER`` (default 0). Never executes actions."""
    from chip_relay import case_intake_ladder as ladder

    if not ladder_enabled_env(environ):
        return None
    mock = environ.get("CHIP_CHAT_MOCK", "").strip() in {"1", "true", "yes"}
    stage_fn = ladder.make_mock_stage_fn() if mock else ladder.make_openrouter_stage_fn()
    body = case.as_dict()
    out = ladder.run_ladder(
        body,
        stage_fn=stage_fn,
        data_root=data_dir(),
        repo_root=chip_config.REPO_ROOT,
        case_path=case_path,
        evidence=str(body.get("repro_context") or ""),
        synthetic=bool(body.get("synthetic")),
    )
    out.pop("rows", None)
    return out


def ladder_enabled_env(environ: dict[str, str]) -> bool:
    from chip_relay.case_intake_ladder import ladder_enabled

    return ladder_enabled(environ)


def _process_alert(
    incoming: Incoming,
    room: RoomBinding,
    *,
    webhook: Callable[[str, str, str], WebhookResult],
    environ: dict[str, str],
    monitoring: bool,
    webhook_id: str | None = None,
    message_id: str = "",
) -> DispatchResult:
    logs: list[str] = []
    halted, halt_reason = check_feed_halt(environ)
    if halted:
        return _fail(logs, f"case-intake feed halted: {halt_reason}")

    limits = load_limits()
    tax_classes = load_taxonomy()
    now = time.time()
    state = _load_state()
    strike = _strike()
    incoming_text = incoming.content.strip()

    # Slice 6 graduation watcher: fires at 200 clean + armed flag.
    # Idempotent; disabled state persists on the coverage volume. A fired
    # summary posts once to the primary relay room; future alerts fail closed via
    # channel_intake_active -> intake_disabled_by_graduation.
    grad = check_graduation(
        environ,
        post_summary=lambda text: webhook(
            environ.get("CHIP_WEBHOOK_DEV", "") or environ.get(room.webhook_env, ""),
            "dispatcher",
            text,
        ).status < 400,
        now=now,
    )
    if grad.get("fired"):
        logs.append(f"case-intake graduation fired: {grad}")

    adapter_result: IntakeResult | None = None
    if monitoring:
        from chip_relay.monitoring_alert_adapter import try_adapter

        adapted = try_adapter(
            incoming_text,
            webhook_id=webhook_id,
            channel=incoming.channel,
            message_id=message_id,
        )
        if adapted.kind == "handled" and adapted.case is not None:
            case = adapted.case
            _log_receipt(
                {
                    "ts": now,
                    "model": "adapter:monitoring-bot",
                    "tokens": 0,
                    "blocked": False,
                    "input_sha": hashlib.sha256(incoming_text.encode()).hexdigest()[:16],
                }
            )
            record_outcome("adapter_handled", failure_kind="none", case_class=case.fingerprint_class or "intake-router")
            log_intake_spend(
                environ=environ,
                channel=incoming.channel,
                case_id=case.id,
                model="adapter:monitoring-bot",
                tokens=0,
                blocked=False,
                outcome="adapter_handled",
                now=now,
            )
            logs.append("adapter handled")
            if adapter_continues_to_intake(incoming_text, environ):
                adapter_result = IntakeResult(
                    kind="case",
                    case=case,
                    receipt=Receipt(model="adapter:monitoring-bot", tokens=0, ts=now),
                )
            else:
                echo_body = _format_echo(
                    case,
                    tax=match_taxonomy(case, tax_classes),
                    deduped=False,
                    correlated=False,
                    monitoring=True,
                )
                posted = webhook(environ.get(room.webhook_env, ""), "dispatcher", echo_body)
                return DispatchResult(ok=posted.status < 400, webhook_calls=1, logs=logs)
        elif adapted.kind == "ignored":
            logs.append(f"adapter ignored: {adapted.note}")
            return DispatchResult(ok=True, logs=logs)
        elif adapted.kind == "failed_closed":
            record_outcome("adapter_failed_closed", failure_kind="closed")
            logs.append(f"adapter failed_closed: {adapted.note}")

    if not monitoring:
        echo_key = incoming.channel
        pending_echo = (state.get("echo_pending") or {}).get(echo_key)
        if pending_echo:
            opened = float(pending_echo.get("opened_at") or 0)
            elapsed = now - opened
            verdict = echo_for_correction(
                CaseFile(**{k: pending_echo["case"][k] for k in ("id", "description", "repro_context", "evidence_path")}),
                actor=incoming.author_id or incoming.author,
                originator=str(pending_echo.get("originator") or ""),
                operator_ids=_operator_ids(room, environ),
                elapsed_s=elapsed,
                limits=limits,
                correction=incoming_text,
            )
            if verdict == "accepted":
                state["echo_pending"].pop(echo_key, None)
                _save_state(state)
                record_outcome("parsed_clean", failure_kind="closed")
                posted = webhook(environ.get(room.webhook_env, ""), "dispatcher", "case-intake: correction accepted (mock solver)")
                return DispatchResult(ok=posted.status < 400, webhook_calls=1, logs=logs)
            if verdict == "ignored":
                return DispatchResult(ok=True, logs=["echo correction ignored"])
            if verdict == "park":
                state["echo_pending"].pop(echo_key, None)
                _save_state(state)
                record_outcome("failed_closed", failure_kind="closed")
                return DispatchResult(ok=True, alert="case-intake parked", logs=logs)

    strike_class = "intake-router"
    if class_killed(strike_class):
        record_outcome("failed_closed", failure_kind="closed", case_class=strike_class)
        return _fail(logs, f"case class killed: {strike_class}")

    if not strike.allowed(strike_class, now=now):
        record_outcome("failed_closed", failure_kind="closed", case_class=strike_class)
        return _fail(logs, "one-strike: intake blocked for case class")

    receipt_logged = False

    def _fail_open(detail: str, exc: BaseException | None = None) -> DispatchResult:
        nonlocal receipt_logged
        if not receipt_logged:
            _log_receipt({"ts": now, "failure": "open", "detail": detail, "input_sha": hashlib.sha256(incoming_text.encode()).hexdigest()[:16]})
            receipt_logged = True
        fid = capture_fixture(
            raw_input=incoming_text,
            outcome="failed_open",
            failure_kind="open",
            detail=detail,
            fingerprint_class=strike_class,
        )
        record_outcome("failed_open", failure_kind="open", case_class=strike_class, fixture_id=fid)
        if exc:
            logs.append(traceback.format_exc())
        return _fail(logs, detail)

    try:
        if incoming_text.lower().startswith("delegate:"):
            return _fail(logs, "case-intake skips delegate lines")

        if adapter_result is not None:
            result = adapter_result
        else:
            result = parse_freeform(
                incoming_text,
                parser=live_parser,
                limits=limits,
                monthly_usd=monthly_gate_for_parse(environ),
                skip_spend_gate=_skip_spend(environ),
                now=now,
            )
        if result.receipt and adapter_result is None:
            _log_receipt(
                {
                    "ts": now,
                    "model": result.receipt.model,
                    "tokens": result.receipt.tokens,
                    "blocked": result.receipt.blocked,
                    "input_sha": hashlib.sha256(incoming_text.encode()).hexdigest()[:16],
                }
            )
            receipt_logged = True
    except Exception as exc:  # noqa: BLE001
        strike.record_failure(strike_class, now=now, limits=limits, log_path=_router_log(), detail=str(exc))
        state.setdefault("strike_until", {})[strike_class] = strike.blocked_until[strike_class]
        _save_state(state)
        fid = capture_fixture(raw_input=incoming_text, outcome="failed_closed", failure_kind="closed", detail=str(exc), fingerprint_class=strike_class)
        record_outcome("failed_closed", failure_kind="closed", case_class=strike_class, fixture_id=fid)
        return _fail(logs, f"case-intake parse failed: {exc}")

    spend_stop = apply_post_parse_spend(environ, incoming.channel, incoming_text, result, now=now)
    if spend_stop == "blocked":
        fid = capture_fixture(
            raw_input=incoming_text,
            outcome="failed_closed",
            failure_kind="closed",
            detail="budget_hard",
            fingerprint_class=strike_class,
        )
        record_outcome("failed_closed", failure_kind="closed", case_class=strike_class, fixture_id=fid)
        return _fail(logs, "case-intake spend gate: budget hard cap")

    if result.kind == "dead_letter":
        strike.record_failure(strike_class, now=now, limits=limits, log_path=_router_log(), detail=result.note)
        state.setdefault("strike_until", {})[strike_class] = strike.blocked_until[strike_class]
        write_dead_letter("parse-fail", {"note": result.note, "msg": incoming_text[:500]}, ts=now)
        _save_state(state)
        fid = capture_fixture(raw_input=incoming_text, outcome="failed_closed", failure_kind="closed", detail=result.note or "", fingerprint_class=strike_class)
        record_outcome("failed_closed", failure_kind="closed", case_class=strike_class, fixture_id=fid)
        return _fail(logs, f"case-intake dead-letter: {result.note}")

    if result.kind == "pending":
        fid = capture_fixture(
            raw_input=incoming_text,
            outcome="pending_clarify",
            failure_kind="closed",
            detail=str(result.pending.missing if result.pending else []),
            fingerprint_class=strike_class,
        )
        record_outcome("llm_pending_clarify", failure_kind="closed", fixture_id=fid)
        posted = webhook(
            environ.get(room.webhook_env, ""),
            "dispatcher",
            f"case-intake PENDING_CLARIFY missing={result.pending.missing if result.pending else []}",
        )
        return DispatchResult(ok=posted.status < 400, webhook_calls=1, logs=logs)

    if result.kind != "case" or result.case is None:
        return _fail_open(f"case-intake unexpected kind={result.kind}")

    case = result.case
    if SYNTHETIC_MARKER.search(incoming_text):
        case.synthetic = True
    strike_class = _strike_class(case)
    if class_killed(strike_class):
        record_outcome("failed_closed", failure_kind="closed", case_class=strike_class)
        return _fail(logs, f"case class killed: {strike_class}")

    tax = match_taxonomy(case, tax_classes)
    cache_hit = local_dedupe(case, _load_open_cache())
    deduped = cache_hit is not None

    recent: list[CaseFile] = []
    for row in state.get("recent_cases") or []:
        rec = row.get("receipt") or {}
        recent.append(
            CaseFile(
                id=row["id"],
                description=row["description"],
                repro_context=row["repro_context"],
                evidence_path=row["evidence_path"],
                fingerprint_class=row.get("fingerprint_class") or "",
                receipt=Receipt(
                    model=str(rec.get("model") or ""),
                    tokens=int(rec.get("tokens") or 0),
                    ts=float(rec.get("ts") or 0),
                )
                if rec
                else None,
            )
        )
    collapsed = correlate_dark(recent + [case], now=now, limits=limits)
    correlated = collapsed is not None and not case.fingerprint_class

    if not deduped and not correlated:
        _append_open_cache(case)
        path = _case_file_path(case.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(case.as_dict(), indent=2) + "\n", encoding="utf-8")
        # Slice 6 solver dispatch: receipts on the case file +
        # JSONL ledger; live route gated on CASE_INTAKE_SOLVER_LIVE (default 0).
        # solver_fn finisher: wired to the SAME model path
        # as this lane (limits primary/fallback); spend rows stay in the
        # intake ledger so beat spend deltas stay true.
        from chip_relay.case_intake_solver_jev import kit_enabled

        def _solver_spend(case_obj: Any, model: str, tokens: int) -> None:
            log_intake_spend(
                environ=environ,
                channel="solver",
                case_id=str(getattr(case_obj, "id", "") or ""),
                model=model,
                tokens=tokens,
                blocked=False,
                outcome="solver_dispatch",
                now=now,
            )

        if kit_enabled(environ):
            from chip_relay.case_intake_solver_jev import make_solver_fn_jev

            solver_fn = make_solver_fn_jev(
                environ=environ,
                limits=limits,
                log_spend=_solver_spend,
                now=now,
                case_path_for=lambda c: _case_file_path(c.id),
            )
        else:
            from chip_relay.case_intake_solver_live import make_solver_fn

            solver_fn = make_solver_fn(environ=environ, limits=limits, log_spend=_solver_spend, now=now)

        solve_row = attempt_solve(
            case,
            limits=limits,
            environ=environ,
            state=state,
            save_state=_save_state,
            data_root=_state_root(),
            case_path=path,
            now=now,
            solver_fn=solver_fn,
        )
        logs.append(f"solver: {solve_row.get('reason')}")
        ladder_row = _run_ladder_dispatch(case, environ=environ, case_path=path)
        if ladder_row is not None:
            logs.append(f"ladder: {ladder_row.get('status')} verdict={ladder_row.get('verdict')}")

    state.setdefault("recent_cases", []).append(case.as_dict())
    state["recent_cases"] = state["recent_cases"][-50:]
    if not monitoring:
        state.setdefault("echo_pending", {})[incoming.channel] = {
            "case": case.as_dict(),
            "opened_at": now,
            "originator": incoming.author_id or incoming.author,
        }
    _save_state(state)

    echo_body = _format_echo(case, tax=tax, deduped=deduped, correlated=correlated, monitoring=monitoring)
    posted = webhook(environ.get(room.webhook_env, ""), "dispatcher", echo_body)

    handle_novel_shape(
        case,
        tax=tax,
        tax_classes=tax_classes,
        deduped=deduped,
        environ=environ,
        limits=limits,
        pager=_pager(),
        post_pager=lambda payload: _post_pager(payload, environ=environ, limits=limits),
        case_path=_case_file_path(case.id),
        data_root=_state_root(),
        state=state,
        save_state=_save_state,
        now=now,
        logs=logs,
    )

    record_outcome("llm_parsed_clean", failure_kind="none", case_class=strike_class)
    logs.append(f"case-intake ok tax={tax} deduped={deduped} correlated={correlated}")
    return DispatchResult(ok=posted.status < 400, alert="case-intake", webhook_calls=1, logs=logs)
