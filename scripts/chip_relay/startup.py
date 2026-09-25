"""Startup checks only. Does not post, listen, or complete a chat."""

from __future__ import annotations

import argparse
import os
import sys

from chip import openrouter
from chip_relay.config import TOKEN_ENV, RelayConfig, load_config
from chip_relay.listener import ListenerNotWired, ListenerStub


def check_startup(config: RelayConfig, *, key_check, environ: dict[str, str] | None = None) -> list[str]:
    """Return human-readable errors. Empty means the skeleton may exit 0."""
    env = os.environ if environ is None else environ
    errors: list[str] = []
    if config.turn_limit != 1:
        errors.append(f"turn_limit must be 1 (got {config.turn_limit})")
    if not config.rooms:
        errors.append("no rooms configured")
    seen: set[str] = set()
    for binding in config.rooms:
        if not binding.as_agent.strip():
            errors.append(f"room {binding.id} is missing caller-supplied as")
        if binding.id in seen:
            errors.append(f"duplicate room {binding.id}")
        seen.add(binding.id)
        if not binding.webhook_env or binding.webhook_env not in env:
            errors.append(f"room {binding.id} webhook env {binding.webhook_env} is unset")
        elif env.get(binding.webhook_env, "").strip().startswith("http"):
            pass
        else:
            errors.append(f"room {binding.id} webhook env {binding.webhook_env} is empty")
    token = env.get(TOKEN_ENV, "").strip()
    if not token:
        errors.append(f"{TOKEN_ENV} is unset (Infisical; dedicated chip-relay bot, not the v1 token)")
    if config.auto:
        errors.append("CHIP_RELAY_AUTO is true; this skeleton will not run agent-initiated turns")
    try:
        key_check()
    except openrouter.ChipConfigError as exc:
        errors.append(str(exc))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chip-relay", description="Chip relay startup checks")
    parser.add_argument("--listen", action="store_true", help="Refused until the gateway is wired")
    args = parser.parse_args(argv)
    config = load_config()
    try:
        key = openrouter.require_api_key()
    except openrouter.ChipConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    def _key_check() -> None:
        openrouter.key_health(key)

    errors = check_startup(config, key_check=_key_check)
    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)
    if errors:
        return 1
    stub = ListenerStub()
    if args.listen:
        try:
            stub.connect(config)
        except ListenerNotWired as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 3
    print(
        f"chip-relay checks ok channel={config.channel} rooms={len(config.rooms)} "
        f"auto={str(config.auto).lower()} listen=stub"
    )
    return stub.run(config)
