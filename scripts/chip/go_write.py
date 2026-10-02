"""GO-token scoped write path — spec stub (not wired).

Design: docs/plans/GO_TOKEN_WRITE_PATH.md
Priority: P2 homelab live deploy (#148) before enabling writes.

Do not import this module from relay, feed, or CLI until GO_WRITE_WIRED is True.
"""

from __future__ import annotations

GO_WRITE_WIRED = False


class GoWriteNotWiredError(RuntimeError):
    """Raised while GO_WRITE_WIRED is False."""


def format_propose_receipt(
    *,
    runbook_id: str,
    fingerprint: str,
    lane: str,
    ticket: str,
    evidence: str,
    exp_hint: str,
) -> str:
    """Four-line propose card for #chip-dev (spec shape only)."""
    if not GO_WRITE_WIRED:
        raise GoWriteNotWiredError("format_propose_receipt not wired; see GO_TOKEN_WRITE_PATH.md")
    raise GoWriteNotWiredError("unreachable")


def try_apply_scoped_go(
    go_text: str,
    *,
    persona: str,
    room: str,
    pending_fingerprint: str | None = None,
    pending_lane: str | None = None,
    runbook_id: str | None = None,
) -> tuple[str, str]:
    """Parse Ally GO and run mapped named tool, or return (verdict, four_line_receipt)."""
    if not GO_WRITE_WIRED:
        raise GoWriteNotWiredError("try_apply_scoped_go not wired; see GO_TOKEN_WRITE_PATH.md")
    raise GoWriteNotWiredError("unreachable")
