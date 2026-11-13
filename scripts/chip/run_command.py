"""Operator ``/run <job> [args]`` chat command -> host job through the job policy (cc-fix #576 part 2).

The only way a chat turn can start a host job. Rules (all enforced here, generic, pack-agnostic):

* The WHOLE message must be one line starting with ``/run`` (no fences, no extra lines). Text that
  merely contains ``/run`` somewhere is not a command, so a pasted or model-written reply quoted
  into a message cannot trigger it.
* The caller states who sent it: ``author_id`` must be in ``operator_ids`` and the message must not
  be synthetic (relay inject / bot / bridge) unless the caller explicitly allows synthetic for a
  one-off operator test. Model output never reaches this function: callers only pass human turns.
* ``<job>`` must be a key of the pack-supplied ``jobs`` table; each entry fixes the job type,
  script and target, and validates every positional arg against its own full-match regex.
  The chat can never supply argv, a target, sudo or a cap.
* Any arg containing a pack ``deny_tokens`` entry (case-insensitive) is refused before policy,
  and the built request is re-checked (target + command) the same way.
* The request then goes through ``host_exec.run_turn_request`` (``CHIP_HOST_EXEC`` gate) ->
  ``policy_exec.run_dev_job`` (PM decision, hard denies, script hash pin, caps, receipts).

The reply is a short receipt summary (decision id, status, exit, seconds) and never includes job
stdout/stderr.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

PREFIX = "/run"
MAX_LEN = 200
MAX_ARGS = 4
_TOKEN = re.compile(r"[A-Za-z0-9._:=@+-]{1,96}")
TAG = "[tool:run-command]"


@dataclass(frozen=True)
class JobSpec:
    """One chat-runnable job. ``build(args)`` returns the full job-policy request dict."""

    name: str
    arg_patterns: Sequence[str]
    build: Callable[[list[str]], dict[str, Any]]
    min_args: int = 0
    usage: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


def is_run_command(text: Any) -> bool:
    """True when the message is shaped like a ``/run`` command (one line, starts with /run)."""
    if not isinstance(text, str):
        return False
    s = text.strip()
    return s == PREFIX or s.startswith(PREFIX + " ") or s.startswith(PREFIX + "\t")


def parse(text: str) -> tuple[str, list[str]] | str:
    """``(job, args)`` for a well-formed command, else a refusal reason string."""
    s = text.strip()
    if len(s) > MAX_LEN:
        return f"command too long (max {MAX_LEN} chars)"
    if "\n" in s or "\r" in s or "`" in s:
        return "one plain line only (no newlines or code fences)"
    parts = s.split()
    if len(parts) < 2:
        return "usage: /run <job> [args]"
    job, args = parts[1].lower(), parts[2:]
    if len(args) > MAX_ARGS:
        return f"too many args (max {MAX_ARGS})"
    for a in [parts[1], *args]:
        if not _TOKEN.fullmatch(a):
            return "args may only use letters, digits and . _ : = @ + -"
        if a.startswith("-") or ".." in a:
            return "args may not start with '-' or contain '..'"
    return job, args


def _denied(values: Iterable[Any], deny_tokens: Iterable[str]) -> str:
    toks = [t.lower() for t in deny_tokens if t]
    for v in values:
        low = str(v).lower()
        for t in toks:
            if t in low:
                return t
    return ""


def summarize(job: str, out: Mapping[str, Any]) -> str:
    rec = out.get("receipt") or {}
    ticket = out.get("ticket") or {}
    did = rec.get("decision_id") or ticket.get("id") or "-"
    verdict = out.get("verdict", "?")
    if rec:
        line = (f"/run {job}: decision {did} · status {rec.get('status', '?')} · exit {rec.get('exit', '-')} · "
                f"{float(rec.get('seconds') or 0):.2f}s")
        if rec.get("reason"):
            line += f" · {str(rec['reason'])[:80]}"
    else:
        why = str(out.get("reason") or "")[:160]
        line = f"/run {job}: {verdict} (decision {did}){' · ' + why if why else ''}"
    return f"{line}\n-# {TAG} turn={out.get('turn_ref', '-')}"


def refuse(job: str, reason: str) -> str:
    return f"/run {job or '?'}: refused · {reason}; nothing ran.\n-# {TAG} refused"


def handle(
    text: Any,
    *,
    author_id: str,
    operator_ids: Iterable[str],
    jobs: Mapping[str, JobSpec],
    room_ref: str,
    room_allowed: bool,
    synthetic: bool = False,
    allow_synthetic: bool = False,
    deny_tokens: Iterable[str] = (),
    env: Mapping[str, str] | None = None,
    runner: Any = None,
    now: Any = None,
    run_turn: Callable[..., dict[str, Any]] | None = None,
) -> str | None:
    """Reply text for a ``/run`` message, or None when ``text`` is not a ``/run`` command."""
    if not is_run_command(text):
        return None
    ops = {str(x).strip() for x in operator_ids if str(x).strip()}
    if synthetic and not allow_synthetic:
        return refuse("", "synthetic/bot/bridge turns cannot run host jobs")
    if not ops or str(author_id or "").strip() not in ops:
        return refuse("", "operator-only")
    if not room_allowed:
        return refuse("", "not allowed in this room")
    parsed = parse(str(text))
    if isinstance(parsed, str):
        return refuse("", parsed)
    job, args = parsed
    spec = jobs.get(job)
    if spec is None:
        return refuse(job, f"unknown job (known: {', '.join(sorted(jobs)) or 'none'})")
    hit = _denied(args, deny_tokens)
    if hit:
        return refuse(job, f"hard deny: '{hit}' is never a chat job target")
    if len(args) < spec.min_args or len(args) > len(spec.arg_patterns):
        return refuse(job, f"usage: /run {job} {spec.usage}".rstrip())
    for a, pat in zip(args, spec.arg_patterns):
        if not re.fullmatch(pat, a):
            return refuse(job, f"bad arg {a!r}; usage: /run {job} {spec.usage}".rstrip())
    try:
        req = dict(spec.build(list(args)))
    except (ValueError, OSError) as exc:
        return refuse(job, f"could not build the job ({type(exc).__name__})")
    hit = _denied([req.get("target", ""), *list(req.get("command") or [])], deny_tokens)
    if hit:
        return refuse(job, f"hard deny: '{hit}' is never a chat job target")
    req["sudo"] = False
    turn_ref = f"{room_ref}:{uuid.uuid4().hex[:10]}"
    if run_turn is None:
        from chip.host_exec import run_turn_request as run_turn
    out = run_turn(req, turn_ref=turn_ref, env=env, now=now, runner=runner)
    return summarize(job, out)
