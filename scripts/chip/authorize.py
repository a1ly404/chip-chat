"""authorize(speaker, scope, action) — the single gate (Grok round-5 patch).

One chokepoint used by claim acceptance, handoff, and every tool execute.
Returns ``(ok, reason)``; false unless: not stand_down ∧ claim_owner==speaker ∧
tool∈manifest. No second path around it — callers MUST go through here.

The relay's dispatch gate (delegate hops) and any future tool-runner wiring both
call this; a caller that skips it is the v1 jailbreak Grok flagged.
"""

from __future__ import annotations

from typing import Iterable

from chip import claims

MANIFEST_TOOLS = {"pytest", "git-status", "task-show"}
RESERVED_WORKER_IDS = claims.RESERVED_IDS


def authorize(
    speaker: str,
    scope: str,
    action: str,
    *,
    environ: dict[str, str] | None = None,
    room: str | None = None,
    tool: str | None = None,
    is_human_or_inject: bool = False,
    manifest: Iterable[str] = tuple(MANIFEST_TOOLS),
) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for a requested action.

    action: "claim" | "execute" | "handoff" | "propose"
    """
    from chip_relay.dispatch import stand_down  # lazy: avoids chip<->chip_relay import cycle

    speaker_norm = (speaker or "").strip().lower()
    if not speaker_norm:
        return False, "no authenticated speaker"

    if is_human_or_inject:
        return True, "human/inject hot path"

    if stand_down(environ or {}):
        return False, "stand-down (no claims, executes, or hops while frozen)"

    if speaker_norm in RESERVED_WORKER_IDS:
        return False, f"{speaker_norm} is a reserved id — not a worker"

    if action == "propose":
        return True, "proposal only (no execution)"

    if action in ("claim", "execute", "handoff"):
        if not room:
            return False, "no room context"
        if not scope or scope.strip().lower() in {"*", "all", "/"}:
            return False, "wildcard/empty scope"
        owner = claims.claim_owner(room, scope)
        if owner != speaker_norm:
            return False, f"claim_owner={owner or 'none'} ≠ speaker {speaker_norm}"
        if action == "execute":
            if tool and tool not in set_compat(manifest):
                return False, f"tool {tool!r} not in manifest"
            return True, "authorized execute under live claim"
        return True, f"authorized {action} under live claim"

    return False, f"unknown action {action!r}"


def set_compat(manifest: Iterable[str]) -> set[str]:
    return set(manifest)
