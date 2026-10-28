"""Extra pre-shot context blocks supplied by a deployment pack.

The public engine ships this no-op. A private pack may replace this module with
one that returns additional ``TEXT`` blocks for the shot: the relay appends each
block to the shot text and logs ``plugin context block injected``.

Contract (stable, keep it tiny):

``plugin_context_blocks(text, environ, author_id="") -> list[str]``

* ``text``: the operator message after persona wake handling.
* ``environ``: the relay environment mapping (flags, allowlists, webhooks).
* ``author_id``: Discord author id, or ``"inject"`` for CLI/inject turns.

Rules for implementers:

* return ``[]`` when the message is not yours to route - this hook runs on every
  human turn;
* render evidence, never promises: a block is quoted by the persona, so it must
  be real state (job receipt, probe result), not a plan;
* keep side effects idempotent - a retried turn must not double-queue work.
"""

from __future__ import annotations

from typing import Mapping


def plugin_context_blocks(
    text: str,
    environ: Mapping[str, str],
    author_id: str = "",
) -> list[str]:
    """No-op default: the public engine has no pack-level context blocks."""
    return []


def suggest_persona(
    text: str,
    environ: Mapping[str, str],
    author_id: str = "",
) -> str | None:
    """Optional pack-level persona route for a message (``None`` = default routing).

    The relay applies the suggestion after wake handling and before its own
    room routing, so a pack can own a narrow ask with a specialist persona.
    Return a persona id from the active pack, or ``None``.
    """
    return None
