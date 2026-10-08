"""Per-persona lane roots, global denies, and receipt-before-action writes.

No raw shell. A global deny beats a persona root. Missing pin refuses; there is
no fallback checkout.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip import config
from chip import store
from chip import system_pack

GO_ENV = "CHIP_LANE_GO_TICKET"


def _lanes_file() -> Path:
    path = system_pack.optional_surface("lanes")
    return path if path is not None else Path("/nonexistent/chip-lanes-surface")


def __getattr__(name: str):
    if name == "LANES_FILE":
        return _lanes_file()
    if name == "LANE_PERSONAS":
        personas = load_lanes().get("personas") or {}
        if isinstance(personas, dict) and personas:
            return tuple(sorted(str(k) for k in personas))
        return tuple()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_lanes() -> dict[str, Any]:
    lanes_file = _lanes_file()
    if not lanes_file.is_file():
        return {"personas": {}, "hard_denies": [], "go_denies": []}
    raw = json.loads(lanes_file.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {"personas": {}, "hard_denies": [], "go_denies": []}


def persona_root(persona: str) -> Path | None:
    """Return the primary pinned root, or None when the env pin is unset or missing."""
    return persona_roots(persona)[0] if persona_roots(persona) else None


def persona_roots(persona: str) -> list[Path]:
    """Primary pinned root plus any extras (lane-table rows), filtered to existing dirs."""
    spec = (load_lanes().get("personas") or {}).get(persona.strip().lower())
    if not isinstance(spec, dict):
        return []
    roots: list[Path] = []
    for env_name in ([str(spec.get("root_env") or "").strip()] + [str(e).strip() for e in spec.get("extra_root_envs") or []]):
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            roots.append(path)
    return roots


def persona_tool_allow(persona: str | None) -> frozenset[str] | None:
    """Persona tool ids when the lane row lists them. None keeps the global manifest."""
    if not persona:
        return None
    spec = (load_lanes().get("personas") or {}).get(persona.strip().lower())
    if not isinstance(spec, dict):
        return None
    tools = spec.get("tools")
    if not tools:
        return None
    return frozenset(str(item) for item in tools)


def resolve_lane_path(persona: str, relpath: str) -> tuple[Path, Path] | None:
    """Pick the pinned root (primary or extras) whose containment holds for relpath.

    Existence-preferred: if several roots can contain a relpath, the root where
    the target already exists wins (reads hit the right tree); if no root has it,
    containment falls back to the primary root (write-creation target).
    """
    resolved: list[tuple[Path, Path]] = []
    escape_err: ValueError | None = None
    for root in persona_roots(persona):
        try:
            resolved.append((root, _under_root(root, relpath)))
        except ValueError as err:
            if "escapes" in str(err):
                escape_err = err
            continue
    if not resolved:
        if escape_err is not None:
            raise escape_err  # escape is NOT a missing pin
        return None
    for root, target in resolved:
        if target.is_file() or target.is_dir():
            return root, target
    return resolved[0]


def _norm(text: str) -> str:
    return text.replace("\\", "/").lower()


def deny_reason(blob: str, *, ticket_id: str | None = None) -> str | None:
    """Global denies win over any persona allow. Hard denies never clear."""
    low = _norm(blob)
    lanes = load_lanes()
    for frag in lanes.get("hard_denies") or []:
        if str(frag).lower() in low:
            return f"global deny {frag}"
    go = os.environ.get(GO_ENV, "").strip()
    named = (ticket_id or "").strip()
    for frag in lanes.get("go_denies") or []:
        if str(frag).lower() in low:
            if not named or go != named:
                return f"global deny {frag} (needs GO naming the ticket)"
    return None


def refuse_blob(*parts: str, ticket_id: str | None = None) -> str | None:
    return deny_reason(" ".join(p for p in parts if p), ticket_id=ticket_id)


def receipt_path() -> Path:
    return config.data_dir() / "audit" / "tool_receipts.jsonl"


def retro_path() -> Path:
    return config.data_dir() / "audit" / "retros.jsonl"


def append_receipt(record: dict[str, Any]) -> None:
    store.append_jsonl(receipt_path(), record)


def learning_path() -> Path:
    return config.data_dir() / "audit" / "tool_learning.jsonl"


def decide(
    blob: str,
    persona_rules: list[tuple[str, str]],
    *,
    room_go: str | None = None,
) -> str:
    """Persona rules first. Global denies are appended last so a later allow still loses."""
    ordered = list(persona_rules)
    lanes = load_lanes()
    for frag in lanes.get("hard_denies") or []:
        ordered.append(("deny", str(frag)))
    if room_go is not None and os.environ.get(GO_ENV, "").strip() != room_go.strip():
        for frag in lanes.get("go_denies") or []:
            ordered.append(("deny", str(frag)))
    decision = "allow"
    low = _norm(blob)
    for effect, frag in ordered:
        if str(frag).lower() in low:
            decision = effect
    return decision


def refuse_ledger_mutation(path: Path, op: str) -> None:
    """Truncation and unlink of the receipt log are global denies, not persona allows."""
    blob = f"{op} {path}"
    if decide(blob, [("allow", "tool_receipts.jsonl"), ("allow", op)]) == "deny":
        file_retro(
            attempted=blob,
            why="global deny tool_receipts.jsonl",
            envelope=f"op={op} path={path}",
        )
        raise PermissionError("global deny tool_receipts.jsonl")
    raise PermissionError(f"unsupported ledger op {op}")


def record_outcome(
    *,
    tool_id: str,
    persona: str,
    exit_code: int,
    duration_s: float,
    artifact: str,
    reflection: str,
) -> None:
    store.append_jsonl(
        learning_path(),
        {
            "ts": _ts(),
            "tool_id": tool_id,
            "persona": persona,
            "exit_code": exit_code,
            "duration_s": round(duration_s, 6),
            "artifact_sha256": hashlib.sha256(artifact.encode("utf-8")).hexdigest(),
            "reflection": reflection.replace("\n", " ")[:200],
        },
    )
    if exit_code != 0:
        from chip import task_ledger

        task_ledger.file_retro_ticket(
            claimed=f"{persona} {tool_id} exited {exit_code}",
            verified=reflection.replace("\n", " ")[:200],
            catch="nonzero tool exit belongs on the task ledger before the next sitrep",
            task_id=tool_id,
        )


def _build_lock(persona: str) -> Path:
    return config.data_dir() / "build_locks" / f"{persona.strip().lower()}.lock"


def acquire_build(persona: str) -> None:
    path = _build_lock(persona)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        file_retro(
            attempted=f"build {persona}",
            why=f"build cap: 1 concurrent build for {persona}",
            envelope=f"persona={persona}",
            force=True,
        )
        raise PermissionError(f"build cap: 1 concurrent build for {persona}") from exc
    os.write(fd, persona.encode("utf-8"))
    os.close(fd)


def release_build(persona: str) -> None:
    path = _build_lock(persona)
    if path.is_file():
        path.unlink()


def local_docker_tag(tag: str) -> bool:
    text = tag.strip()
    if not text or "/" in text or ":" not in text:
        return False
    return True


def run_build(
    *,
    persona: str,
    tool_id: str,
    room: str,
    scope: str,
    ticket_id: str,
    runner,
    docker_tag: str | None = None,
) -> int:
    """BUILD tier. Allowed tools run under the claim. Push, deploy, and compose-up need a matching GO."""
    lanes = load_lanes()
    allowed = set(lanes.get("build_allowed") or [])
    needs_go = set(lanes.get("build_needs_go") or [])
    if tool_id in needs_go or tool_id not in allowed:
        go = os.environ.get(GO_ENV, "").strip()
        if tool_id not in needs_go and tool_id not in allowed:
            raise PermissionError(f"unknown build tool {tool_id}")
        if go != (ticket_id or "").strip():
            file_retro(
                attempted=tool_id,
                why="refused without GO naming the ticket",
                envelope=f"persona={persona} tool={tool_id} ticket={ticket_id} room={room} scope={scope}",
                force=True,
            )
            raise PermissionError("refused without GO naming the ticket")
    if tool_id == "docker-build" and not local_docker_tag(docker_tag or ""):
        file_retro(
            attempted="docker-build",
            why="tag is not local-only",
            envelope=f"tag={docker_tag or ''} ticket={ticket_id}",
        )
        raise PermissionError("docker-build requires a local-only tag (name:tag, no registry host)")
    assert_claim(persona=persona, room=room, scope=scope, ticket_id=ticket_id)
    acquire_build(persona)
    import time

    append_receipt(
        {
            "ts": _ts(),
            "phase": "start",
            "op": "build",
            "persona": persona,
            "tool_id": tool_id,
            "ticket_id": ticket_id,
            "room": room,
            "scope": scope,
            "docker_tag": docker_tag or "",
        }
    )
    started = time.perf_counter()
    code = 1
    artifact = ""
    try:
        code, artifact = runner()
        code = int(code)
        artifact = artifact or ""
    except Exception:
        artifact = artifact or "incomplete"
        code = 1
        raise
    finally:
        duration = time.perf_counter() - started
        append_receipt(
            {
                "ts": _ts(),
                "phase": "complete",
                "op": "build",
                "persona": persona,
                "tool_id": tool_id,
                "ticket_id": ticket_id,
                "exit_code": code,
                "sha256": hashlib.sha256(artifact.encode("utf-8")).hexdigest() if artifact and artifact != "incomplete" else "incomplete",
            }
        )
        record_outcome(
            tool_id=tool_id,
            persona=persona,
            exit_code=code,
            duration_s=duration,
            artifact=artifact,
            reflection=f"{persona} {tool_id} exited {code}",
        )
        release_build(persona)
    if code != 0:
        file_retro(
            attempted=tool_id,
            why=f"build exit {code}",
            envelope=f"persona={persona} tool={tool_id} ticket={ticket_id} room={room} scope={scope}",
            force=True,
        )
    return code


_ABOVE_READ = ("write", "docker", "deploy", "compose", "push", "unlink", "truncate", "vite", "build")


def chipchatdev_wake_allowed(task: str, room: str) -> tuple[bool, str]:
    """Read-tier chipchatdev wakes need a live claim. Above-read needs an in-room GO naming the ticket."""
    import re

    from chip import claims

    text = task or ""
    scope_match = re.search(r"\bscope:(\S+)", text, re.IGNORECASE)
    if scope_match is None:
        return False, "chipchatdev wake needs scope:<id>"
    scope = scope_match.group(1)
    low = text.lower()
    above = any(re.search(rf"\b{word}\b", low) for word in _ABOVE_READ)
    if above:
        go_match = re.search(r"\bGO:\s*([A-Za-z0-9-]+)", text, re.IGNORECASE)
        named = go_match.group(1) if go_match else ""
        pinned = os.environ.get(GO_ENV, "").strip()
        if not named or pinned != named:
            return False, "above read needs GO naming the ticket"
        from chip.go_scope import needs_scoped_go, parse_scoped_go

        if needs_scoped_go(text):
            fields, reason = parse_scoped_go(text)
            if fields is None:
                return False, reason
            if fields["fp"] != named:
                return False, "scoped GO fingerprint must match the GO ticket"
        return True, "go names ticket"
    owner = claims.claim_owner(room, scope)
    if owner != "chipchatdev":
        return False, f"claim_owner={owner or 'none'} ≠ chipchatdev"
    return True, "read tier under claim"


def file_retro(*, attempted: str, why: str, envelope: str, force: bool = False) -> None:
    """Live runner only, unless force (failed builds always file the template)."""
    if not force and os.environ.get("CHIP_LANE_RETRO_LIVE", "").strip().lower() not in ("1", "true", "yes"):
        return
    store.append_jsonl(
        retro_path(),
        {"ts": _ts(), "attempted": attempted, "why": why, "envelope": envelope},
    )


def _under_root(root: Path, relpath: str) -> Path:
    rel = Path(relpath)
    if rel.is_absolute():
        raise ValueError("path must be relative to the lane root")
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise ValueError("path escapes the lane root")
    return target


def read_text(
    *,
    persona: str,
    relpath: str,
    ticket_id: str | None = None,
) -> str:
    resolved = resolve_lane_path(persona, relpath)
    if resolved is None:
        raise FileNotFoundError(f"lane pin missing for {persona}")
    root, target = resolved
    reason = deny_reason(str(target), ticket_id=ticket_id)
    if reason:
        file_retro(attempted=f"read {target}", why=reason, envelope=f"persona={persona} path={relpath}")
        raise PermissionError(reason)
    append_receipt(
        {
            "ts": _ts(),
            "phase": "start",
            "op": "read",
            "persona": persona,
            "path": str(target),
            "ticket_id": ticket_id or "",
        }
    )
    data = target.read_text(encoding="utf-8")
    append_receipt(
        {
            "ts": _ts(),
            "phase": "complete",
            "op": "read",
            "persona": persona,
            "path": str(target),
            "sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
            "ticket_id": ticket_id or "",
        }
    )
    return data


def assert_claim(*, persona: str, room: str, scope: str, ticket_id: str) -> None:
    """Writes require a live claim owned by this persona. First claim wins."""
    from chip import authorize

    ok, reason = authorize.authorize(
        persona,
        scope,
        "execute",
        environ=os.environ,
        room=room,
        tool="lane-write",
        manifest=("lane-write", "git-status", "git-diff", "git-log", "pytest"),
    )
    if not ok:
        file_retro(
            attempted=f"write ticket={ticket_id}",
            why=reason,
            envelope=f"persona={persona} room={room} scope={scope} ticket={ticket_id}",
        )
        raise PermissionError(reason)


def write_text(
    *,
    persona: str,
    relpath: str,
    content: str,
    ticket_id: str,
    room: str,
    scope: str,
) -> Path:
    """Append a start receipt, then write. Refuses when the pin, claim, or a deny fails."""
    if not (ticket_id or "").strip():
        raise PermissionError("write requires a ticket id")
    assert_claim(persona=persona, room=room, scope=scope, ticket_id=ticket_id)
    root = persona_root(persona)
    if root is None:
        raise FileNotFoundError(f"lane pin missing for {persona}")
    target = _under_root(root, relpath)
    reason = deny_reason(str(target), ticket_id=ticket_id)
    if reason:
        file_retro(
            attempted=f"write {target}",
            why=reason,
            envelope=f"persona={persona} path={relpath} ticket={ticket_id}",
        )
        raise PermissionError(reason)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    append_receipt(
        {
            "ts": _ts(),
            "phase": "start",
            "op": "write",
            "persona": persona,
            "path": str(target),
            "sha256": digest,
            "ticket_id": ticket_id,
            "intent": "write",
        }
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    append_receipt(
        {
            "ts": _ts(),
            "phase": "complete",
            "op": "write",
            "persona": persona,
            "path": str(target),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "ticket_id": ticket_id,
        }
    )
    return target
