"""Local inject path: one room turn through relay dispatch without Discord gateway."""

from __future__ import annotations

import os
import sys

from chip.spend import WARN_MONTHLY_USD, ledger_monthly_usd, load_ledger
from chip_relay.config import load_config
from chip_relay.dispatch import handle_message
from chip_relay.gateway import post_webhook, run_chip
from chip_relay.routing import ALLY_INJECT_AUTHOR_ID, INJECT_AUTHOR_ID_ENV, inject_incoming_for_room


def resolve_inject_author_id(
    *,
    as_ally: bool,
    author_id: str,
    environ: dict[str, str] | None = None,
) -> str:
    env = os.environ if environ is None else environ
    if as_ally:
        return ALLY_INJECT_AUTHOR_ID
    wanted = author_id.strip() or env.get(INJECT_AUTHOR_ID_ENV, "").strip()
    return wanted


def run_inject(
    *,
    room_id: str,
    message: str,
    as_ally: bool = False,
    author_id: str = "",
    mock_chip: bool = False,
    environ: dict[str, str] | None = None,
) -> int:
    env = dict(os.environ if environ is None else environ)
    if mock_chip:
        env["CHIP_CHAT_MOCK"] = "1"
    monthly = ledger_monthly_usd(load_ledger())
    if monthly >= WARN_MONTHLY_USD:
        print(f"ERROR: monthly_usd={monthly} >= WARN {WARN_MONTHLY_USD}", file=sys.stderr)
        return 2

    config = load_config()
    try:
        room = config.room(room_id)
    except KeyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    resolved_author = resolve_inject_author_id(as_ally=as_ally, author_id=author_id, environ=env)
    if not resolved_author:
        print(
            f"ERROR: set --as-ally, --as-author-id, or {INJECT_AUTHOR_ID_ENV} for speaker map resolution",
            file=sys.stderr,
        )
        return 4

    incoming = inject_incoming_for_room(room, content=message, author_id=resolved_author)
    result = handle_message(
        incoming,
        config,
        chip=run_chip,
        webhook=post_webhook,
        environ=env,
    )
    if not result.ok:
        print(f"ERROR: {result.alert}", file=sys.stderr)
        return 5
    print(
        f"inject ok room={room_id} author_id={resolved_author} chip_calls={result.chip_calls} "
        f"delegate_hops={result.delegate_hops} orchestrator_ticks={result.orchestrator_ticks} "
        f"monthly_usd={monthly}"
    )
    return 0
