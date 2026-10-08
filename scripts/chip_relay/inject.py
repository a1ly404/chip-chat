"""Local inject path: one room turn through relay dispatch without Discord gateway."""

from __future__ import annotations

import os
import sys

from chip.spend import WARN_MONTHLY_USD, ledger_monthly_usd, load_ledger
from chip_relay.config import load_config
from chip_relay.dispatch import handle_message
from chip_relay.gateway import post_webhook, run_chip
from chip_relay.routing import INJECT_AUTHOR_ID_ENV, inject_incoming_for_room, operator_inject_author_id


def _legacy_compat() -> None:
    from chip_relay.plugins import install_legacy_compat_if_ready

    install_legacy_compat_if_ready()


def resolve_inject_author_id(
    *,
    as_operator: bool = False,
    author_id: str,
    environ: dict[str, str] | None = None,
) -> str:
    _legacy_compat()
    env = os.environ if environ is None else environ
    if as_operator:
        return operator_inject_author_id(env)
    wanted = author_id.strip() or env.get(INJECT_AUTHOR_ID_ENV, "").strip()
    return wanted


def run_inject(
    *,
    room_id: str,
    message: str,
    as_operator: bool = False,
    author_id: str = "",
    mock_chip: bool = False,
    environ: dict[str, str] | None = None,
) -> int:
    _legacy_compat()
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

    resolved_author = resolve_inject_author_id(
        as_operator=as_operator, author_id=author_id, environ=env
    )
    if not resolved_author:
        print(
            f"ERROR: set --as-operator, --as-author-id, or {INJECT_AUTHOR_ID_ENV} for speaker map resolution",
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
