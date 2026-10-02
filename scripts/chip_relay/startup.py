"""Startup checks only. Does not post, listen, or complete a chat."""

from __future__ import annotations

import argparse
import os
import sys

from chip import openrouter
from chip_relay.config import TOKEN_ENV, RelayConfig, load_config
from chip_relay.infisical_rest import missing_boot
from chip_relay.listener import ListenerNotWired, ListenerStub
from chip_relay.personas import speakers_maps_effective, speakers_unconfigured_message
from chip_relay.routing import INJECT_AUTHOR_ID_ENV


def check_startup(
    config: RelayConfig,
    *,
    key_check,
    discord_check=None,
    webhook_check=None,
    environ: dict[str, str] | None = None,
) -> list[str]:
    """Return human-readable errors. Empty means the skeleton may exit 0."""
    env = os.environ if environ is None else environ
    errors: list[str] = []
    for name in missing_boot(env):
        errors.append(f"missing boot cred {name}")
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
        if binding.speakers_declared and not speakers_maps_effective(binding):
            errors.append(speakers_unconfigured_message(binding))
        if not binding.webhook_env or binding.webhook_env not in env:
            errors.append(f"room {binding.id} webhook env {binding.webhook_env} is unset")
        else:
            url = env.get(binding.webhook_env, "").strip()
            if not url:
                errors.append(f"room {binding.id} webhook env {binding.webhook_env} is empty")
            elif webhook_check is not None:
                status = webhook_check(url)
                if status >= 400:
                    errors.append(f"webhook {binding.webhook_env} unreachable HTTP {status}")
    token = env.get(TOKEN_ENV, "").strip()
    if not token:
        errors.append(f"{TOKEN_ENV} is unset")
    elif discord_check is not None:
        try:
            discord_check(token)
        except Exception as exc:
            errors.append(f"{TOKEN_ENV} rejected ({exc.__class__.__name__})")
    if config.auto:
        errors.append("CHIP_RELAY_AUTO is true; this skeleton will not run agent-initiated turns")
    try:
        key_check()
    except openrouter.ChipConfigError as exc:
        errors.append(str(exc))
    return errors


def apply_runtime_secrets() -> int | None:
    """Load Infisical runtime secrets into os.environ. Returns exit code on failure."""
    from chip_relay.infisical_rest import InfisicalAccessError, fetch_runtime_secrets

    try:
        fetched = fetch_runtime_secrets()
    except InfisicalAccessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    infisical_key = fetched.pop("OPENROUTER_CC_API_KEY", "")
    os.environ.update(fetched)
    if infisical_key:
        try:
            openrouter.key_health(infisical_key)
        except openrouter.ChipConfigError:
            print(
                "ERROR: OPENROUTER_CC_API_KEY from Infisical failed key health; keeping boot key",
                file=sys.stderr,
            )
            from chip.config import DOTENV_FILE

            for line in DOTENV_FILE.read_text(encoding="utf-8").splitlines():
                if line.startswith("OPENROUTER_CC_API_KEY="):
                    os.environ["OPENROUTER_CC_API_KEY"] = line.split("=", 1)[1].strip().strip("'").strip('"')
                    break
        else:
            os.environ["OPENROUTER_CC_API_KEY"] = infisical_key
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chip-relay", description="Chip relay startup checks")
    parser.add_argument("--listen", action="store_true", help="Start the live Discord gateway")
    sub = parser.add_subparsers(dest="command")
    inject_p = sub.add_parser(
        "inject",
        help="Run one dispatch as a mapped human author (no Discord MessageCreate)",
    )
    inject_p.add_argument("-m", "--message", required=True, help="Inbound message text")
    inject_p.add_argument("--room", default="dev", help="Room id from rooms.json (default: dev)")
    inject_p.add_argument(
        "--as-author-id",
        default="",
        help=f"Discord snowflake for speakers map (default: env {INJECT_AUTHOR_ID_ENV})",
    )
    inject_p.add_argument(
        "--as-ally",
        action="store_true",
        help="Use Ally human snowflake mapped to pm in chip-dev",
    )
    inject_p.add_argument(
        "--mock",
        action="store_true",
        help="Set CHIP_CHAT_MOCK=1 for this inject (no OpenRouter spend)",
    )
    drill_p = sub.add_parser(
        "refuse-drill",
        help="Post the canned al-9501 refusal and store that same body",
    )
    drill_p.add_argument("--room", default="dev")
    args = parser.parse_args(argv)
    if args.command == "refuse-drill":
        from chip_relay.refusal import post_refusal

        secret_rc = apply_runtime_secrets()
        if secret_rc is not None:
            return secret_rc
        body = post_refusal(room_id=args.room)
        print(body)
        return 0
    if args.command == "inject":
        from chip_relay.inject import run_inject

        if not args.mock:
            secret_rc = apply_runtime_secrets()
            if secret_rc is not None:
                return secret_rc
        return run_inject(
            room_id=args.room,
            message=args.message,
            as_ally=args.as_ally,
            author_id=args.as_author_id,
            mock_chip=args.mock,
        )
    if args.listen:
        secret_rc = apply_runtime_secrets()
        if secret_rc is not None:
            return secret_rc
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
    if args.listen:
        from chip_relay.gateway import run_gateway

        return run_gateway()
    stub = ListenerStub()
    print(
        f"chip-relay checks ok channel={config.channel} rooms={len(config.rooms)} "
        f"auto={str(config.auto).lower()} listen=stub"
    )
    return stub.run(config)
