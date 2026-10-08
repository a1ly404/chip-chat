"""Startup checks only. Does not post, listen, or complete a chat."""

from __future__ import annotations

import argparse
import os
import sys

from chip import openrouter, pack_values
from chip_relay.config import TOKEN_ENV, RelayConfig, load_config
from chip_relay.monitoring_listen import monitoring_webhook_required
from chip_relay import plugins as relay_plugins
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
    relay_plugins.ensure_loaded(env)
    for name in relay_plugins.missing_boot_names(env):
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
        skip_webhook = getattr(binding, "monitoring_listen", False) and not monitoring_webhook_required(
            binding, env
        )
        if skip_webhook:
            continue
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
    """Load runtime secrets from registered plugin providers. Returns exit code on failure."""
    return relay_plugins.apply_runtime_secrets()


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
        "--as-operator",
        action="store_true",
        help="Use pack operator Discord snowflake mapped to pm in the dev room",
    )
    inject_p.add_argument(
        "--mock",
        action="store_true",
        help="Set CHIP_CHAT_MOCK=1 for this inject (no OpenRouter spend)",
    )
    drill_p = sub.add_parser(
        "refuse-drill",
        help=pack_values.relay_startup_refusal_post_help(),
    )
    drill_p.add_argument("--room", default="dev")
    from chip import system_pack

    for legacy_dest, canonical_dest in system_pack.cli_aliases().items():
        inject_p.add_argument(
            f"--{legacy_dest.replace('_', '-')}",
            dest=canonical_dest,
            action="store_true",
            help=f"Legacy alias for --{canonical_dest.replace('_', '-')}",
        )
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
            as_operator=getattr(args, "as_operator", False),
            author_id=args.as_author_id,
            mock_chip=args.mock,
        )
    if args.listen:
        secret_rc = apply_runtime_secrets()
        if secret_rc is not None:
            return secret_rc
        try:
            from chip_relay.fixture_seed import seed_fixture_dir

            summary = seed_fixture_dir()
            print(
                f"fixture-seed: {summary.get('status')} "
                f"copied={len(summary.get('copied') or [])} "
                f"index_rows_added={summary.get('index_rows_added')}"
            )
        except Exception as exc:  # noqa: BLE001 — seed failure must not block relay boot
            print(f"fixture-seed: failed ({exc.__class__.__name__}: {exc})", file=sys.stderr)
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
