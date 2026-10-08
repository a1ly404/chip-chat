"""On-demand one-turn smoke. Refuses to spend when monthly_usd is at the WARN gate.

Live Discord is not invoked unless CHIP_RELAY_SELFTEST=1 and the vault-client env
vars are present. Persona comes from the room ``speakers`` map (not a forced
``--as pm``); set ``CHIP_RELAY_SELFTEST_AUTHOR_ID`` / ``CHIP_RELAY_SELFTEST_AUTHOR_HANDLE``
to a mapped Discord identity. See ``docs/SECRETS.md``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from chip.spend import WARN_MONTHLY_USD

REPO = Path(__file__).resolve().parents[2]
LAUNCHER = REPO / "bin" / "chip"
SELFTEST_ROOM_ENV = "CHIP_RELAY_SELFTEST_ROOM"
SELFTEST_AUTHOR_ID_ENV = "CHIP_RELAY_SELFTEST_AUTHOR_ID"
SELFTEST_AUTHOR_HANDLE_ENV = "CHIP_RELAY_SELFTEST_AUTHOR_HANDLE"


def budget_ok(monthly_usd: float) -> bool:
    return monthly_usd < WARN_MONTHLY_USD


def resolve_selftest_persona(environ: dict[str, str] | None = None) -> tuple[str, str | None]:
    """Return ``(persona, error)`` for the configured selftest author."""
    from chip import pack_values
    from chip_relay.config import load_config
    from chip_relay.personas import resolve_persona_for_dispatch

    env = os.environ if environ is None else environ
    config = load_config()
    room_id = env.get(SELFTEST_ROOM_ENV, "dev").strip() or "dev"
    try:
        room = config.room(room_id)
    except KeyError as exc:
        return "", str(exc)
    author_id = env.get(SELFTEST_AUTHOR_ID_ENV, "").strip()
    author_handle = env.get(SELFTEST_AUTHOR_HANDLE_ENV, "").strip()
    if not author_id and not author_handle:
        return (
            "",
            f"set {SELFTEST_AUTHOR_ID_ENV} or {SELFTEST_AUTHOR_HANDLE_ENV} to a mapped Discord identity "
            f"(see {pack_values.rooms_config_hint()} speakers for room {room_id})",
        )
    persona, alert = resolve_persona_for_dispatch(
        room,
        author_id=author_id,
        author_handle=author_handle,
    )
    if alert:
        return "", alert
    return persona, None


def run_chip_room(room_id: str, persona: str, message: str) -> tuple[int, str]:
    args = [
        str(LAUNCHER),
        "room",
        room_id,
        "-m",
        message,
        "--as",
        persona,
        "--json",
        "--turn-limit",
        "1",
    ]
    proc = subprocess.run(
        args,
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout.strip() or proc.stderr.strip()


def main(argv: list[str] | None = None) -> int:
    del argv
    if os.environ.get("CHIP_RELAY_SELFTEST", "").strip().lower() not in ("1", "true", "yes"):
        print("ERROR: set CHIP_RELAY_SELFTEST=1 to run the one-turn smoke", file=sys.stderr)
        return 1
    from chip import spend

    ledger = spend.load_ledger()
    monthly = spend.ledger_monthly_usd(ledger)
    if not budget_ok(monthly):
        print(
            f"ERROR: monthly_usd={monthly} >= WARN {WARN_MONTHLY_USD}; selftest refused",
            file=sys.stderr,
        )
        return 2
    required = (
        "DISCORD_CHIP_RELAY_TOKEN",
        "CHIP_WEBHOOK_DEV",
        "OPENROUTER_CC_API_KEY",
    )
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        print("ERROR: selftest missing " + ", ".join(missing), file=sys.stderr)
        return 3
    persona, persona_err = resolve_selftest_persona()
    if persona_err:
        print(f"ERROR: {persona_err}", file=sys.stderr)
        return 5
    room_id = os.environ.get(SELFTEST_ROOM_ENV, "dev").strip() or "dev"
    message = os.environ.get("CHIP_RELAY_SELFTEST_MESSAGE", "relay selftest ping").strip()
    code, output = run_chip_room(room_id, persona, message)
    if code != 0:
        print(f"ERROR: chip room exit {code}: {output}", file=sys.stderr)
        return 6
    try:
        payload = json.loads(output.splitlines()[-1])
        reply = str(payload.get("reply", ""))
    except (json.JSONDecodeError, IndexError):
        print(f"ERROR: chip json malformed: {output}", file=sys.stderr)
        return 7
    print(f"selftest ok room={room_id} persona={persona} reply_len={len(reply)} monthly_usd={monthly}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
