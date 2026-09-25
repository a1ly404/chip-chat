"""Live Discord gateway. One chip shot per human message. No retries."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from chip.spend import WARN_MONTHLY_USD, load_ledger
from chip_relay.config import TOKEN_ENV, RelayConfig, load_config
from chip_relay.dispatch import CHIP_TIMEOUT_S, ChipResult, Incoming, WebhookResult, handle_message
from chip_relay.personas import resolve_persona
from chip_relay.watchdog import HEARTBEAT_INTERVAL_S, Watchdog, heartbeat_line

REPO = Path(__file__).resolve().parents[2]
LAUNCHER = REPO / "bin" / "chip"


def run_chip(args: list[str], timeout: int) -> ChipResult:
    try:
        proc = subprocess.run(
            [str(LAUNCHER), *args],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ChipResult(returncode=-1, stdout="", timed_out=True)
    return ChipResult(returncode=proc.returncode, stdout=proc.stdout)


def post_webhook(url: str, username: str, content: str) -> WebhookResult:
    body = json.dumps({"content": content[:1900], "username": username[:80]}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return WebhookResult(status=resp.status)
    except urllib.error.HTTPError as exc:
        return WebhookResult(status=exc.code)


def run_gateway() -> int:
    import discord

    config = load_config()
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        print(f"ERROR: {TOKEN_ENV} is unset", file=sys.stderr)
        return 1
    monthly = float(load_ledger().get("monthly_usd") or 0.0)
    if monthly >= WARN_MONTHLY_USD:
        print(f"ERROR: monthly_usd={monthly} >= WARN {WARN_MONTHLY_USD}", file=sys.stderr)
        return 2

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    client = discord.Client(intents=intents)
    dog = Watchdog()
    stats = {"ok": 0, "fail": 0, "last_error": "", "last_message_at": 0.0}

    def monthly_now() -> float:
        return float(load_ledger().get("monthly_usd") or 0.0)

    async def post_heartbeat() -> None:
        channel = client.get_channel(int(config.heartbeat_channel_id))
        ok = stats["fail"] == 0 or stats["ok"] > 0 and not stats["last_error"]
        rate = 1.0 if (stats["ok"] + stats["fail"]) == 0 else stats["ok"] / (stats["ok"] + stats["fail"])
        age = int(time.time() - stats["last_message_at"]) if stats["last_message_at"] else -1
        line = heartbeat_line(
            last_message_age_s=age,
            success_rate=rate,
            last_error=stats["last_error"],
            monthly_usd=monthly_now(),
        )
        try:
            if channel is None:
                raise RuntimeError("heartbeat channel missing")
            await channel.send(line)
            verdict = dog.observe(True)
        except Exception as exc:
            stats["last_error"] = exc.__class__.__name__
            verdict = dog.observe(False)
            print(f"heartbeat {verdict} {exc.__class__.__name__}", flush=True)
            return
        print(f"heartbeat {verdict}", flush=True)

    async def heartbeat_loop() -> None:
        await post_heartbeat()
        while not dog.stopped:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            if dog.stopped:
                return
            await post_heartbeat()

    @client.event
    async def on_ready() -> None:
        print("gateway ready", flush=True)
        asyncio.create_task(heartbeat_loop())

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.webhook_id or message.author.id == client.user.id:
            return
        try:
            room = config.room_for_channel_id(message.channel.id)
        except KeyError:
            return
        incoming = Incoming(
            channel=room.discord_channel,
            content=message.content or "",
            author=str(message.author.name),
            author_id=str(message.author.id),
        )

        def _chip(args: list[str], timeout: int) -> ChipResult:
            return run_chip(args, timeout)

        result = await asyncio.to_thread(
            handle_message,
            incoming,
            config,
            chip=_chip,
            webhook=post_webhook,
            environ=os.environ,
        )
        stats["last_message_at"] = time.time()
        if result.ok:
            stats["ok"] += 1
            stats["last_error"] = ""
            persona = resolve_persona(
                room,
                author_id=incoming.author_id,
                author_handle=incoming.author,
            )
            print(f"processed channel={room.id} persona={persona}", flush=True)
        else:
            stats["fail"] += 1
            stats["last_error"] = result.alert
            print(f"ERROR: {result.alert}", file=sys.stderr, flush=True)

    client.run(token)
    return 0
