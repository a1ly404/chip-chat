"""Live Discord gateway. One chip shot per routed message. No retries."""

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

from chip import budget_mode
from chip.spend import ledger_monthly_usd, load_ledger
from chip_relay.config import TOKEN_ENV, RelayConfig, load_config
from chip_relay.dispatch import (
    CHIP_TIMEOUT_S,
    ChipResult,
    Incoming,
    WebhookResult,
    handle_inbound_delegate,
    handle_message,
)
from chip_relay.monitoring_listen import handle_monitoring_alert
from chip_relay.routing import RouteKind, classify_gateway_message
from chip_relay.personas import resolve_persona
from chip_relay.health_server import clear_gateway_ready, set_gateway_ready, start_health_server
from chip_relay.watchdog import (
    HEARTBEAT_INTERVAL_S,
    HeartbeatGate,
    Watchdog,
    heartbeat_line,
    should_post_heartbeat,
)

REPO = Path(__file__).resolve().parents[2]
LAUNCHER = REPO / "bin" / "chip"
RELAY_ERROR_PREFIX = "[chip-relay] ERROR: "


def dispatch_error_visible_in_channel(alert: str) -> bool:
    """In-channel ERROR for empty chip replies, delegate hop cap, and webhook failures."""
    if alert == "empty chip reply":
        return True
    if alert.startswith("delegate hop cap"):
        return True
    return alert.startswith("webhook ")


def format_channel_dispatch_error(alert: str) -> str:
    return f"{RELAY_ERROR_PREFIX}{alert}"


def run_chip(args: list[str], timeout: int) -> ChipResult:
    try:
        env = os.environ.copy()
        env["CHIP_STORE_GATED"] = "1"
        proc = subprocess.run(
            [str(LAUNCHER), *args],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return ChipResult(returncode=-1, stdout="", timed_out=True)
    return ChipResult(returncode=proc.returncode, stdout=proc.stdout)


def post_webhook(url: str, username: str, content: str) -> WebhookResult:
    body = json.dumps({"content": content[:1900], "username": username[:80]}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "chip-relay"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return WebhookResult(status=resp.status)
    except urllib.error.HTTPError as exc:
        return WebhookResult(status=exc.code)


def run_gateway() -> int:
    import discord

    from chip_relay import plugins as relay_plugins

    relay_plugins.ensure_loaded()
    clear_gateway_ready()
    start_health_server()
    from chip_relay.monitor_feed import feed_enabled, start_feed_polling

    if feed_enabled():
        thread = start_feed_polling()
        if thread is not None:
            print("[chip-relay] monitor feed running (dark: log-only)", flush=True)

    config = load_config()
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        print(f"ERROR: {TOKEN_ENV} is unset", file=sys.stderr)
        return 1
    monthly = ledger_monthly_usd(load_ledger())
    mode = budget_mode.mode_for(monthly)
    # Thrift/stopped no longer block startup: status + read-only Q&A stay up and
    # the per-call gates (chip.spend / chip.budget_mode) refuse what is over budget.
    print(f"[chip-relay] budget mode={mode} monthly_usd={monthly:.2f}", flush=True)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    client = discord.Client(intents=intents)
    dog = Watchdog()
    heartbeat_gate = HeartbeatGate(baseline_monthly_usd=monthly)
    stats = {"ok": 0, "fail": 0, "last_error": "", "last_message_at": 0.0}
    seen_message_ids: set[int] = set()
    max_seen_ids = 2048

    def monthly_now() -> float:
        return ledger_monthly_usd(load_ledger())

    def post_budget_notice() -> None:
        url = (os.environ.get("CHIP_WEBHOOK_DEV") or "").strip()
        if not url:
            return

        def _post(line: str) -> bool:
            result = post_webhook(url, "chip-relay", line[:1900])
            return 200 <= int(getattr(result, "status", 0)) < 300

        try:
            posted = budget_mode.maybe_post_notice(_post)
        except Exception as exc:  # noqa: BLE001 — notices must never crash the gateway
            print(f"budget notice failed {exc.__class__.__name__}", flush=True)
            return
        if posted:
            print(f"budget notice posted mode={posted} -> dev channel", flush=True)

    async def post_heartbeat() -> None:
        await asyncio.to_thread(post_budget_notice)
        now = time.time()
        current_monthly = monthly_now()
        if not should_post_heartbeat(
            now=now,
            last_post_at=heartbeat_gate.last_post_at,
            baseline_monthly_usd=heartbeat_gate.baseline_monthly_usd,
            current_monthly_usd=current_monthly,
            last_error=stats["last_error"],
            watchdog_consecutive_failures=dog.consecutive_failures,
            watchdog_would_page=dog.would_page_on_next_failure(),
        ):
            print("heartbeat skipped", flush=True)
            return
        channel = client.get_channel(int(config.heartbeat_channel_id))
        rate = 1.0 if (stats["ok"] + stats["fail"]) == 0 else stats["ok"] / (stats["ok"] + stats["fail"])
        age = int(now - stats["last_message_at"]) if stats["last_message_at"] else -1
        line = heartbeat_line(
            last_message_age_s=age,
            success_rate=rate,
            last_error=stats["last_error"],
            monthly_usd=current_monthly,
        )
        try:
            if channel is None:
                raise RuntimeError("heartbeat channel missing")
            await channel.send(line)
            heartbeat_gate.note_post(current_monthly, now)
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
        set_gateway_ready()
        print("gateway ready", flush=True)
        asyncio.create_task(heartbeat_loop())

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.id in seen_message_ids:
            return
        try:
            room = config.room_for_channel_id(message.channel.id)
        except KeyError:
            return
        decision = classify_gateway_message(
            message,
            relay_bot_user_id=client.user.id,
            room=room,
            environ=os.environ,
        )
        if decision is None:
            return
        seen_message_ids.add(message.id)
        if len(seen_message_ids) > max_seen_ids:
            seen_message_ids.clear()
            seen_message_ids.add(message.id)

        def _chip(args: list[str], timeout: int) -> ChipResult:
            return run_chip(args, timeout)

        def _dispatch() -> object:
            if decision.kind == RouteKind.WEBHOOK_DELEGATE:
                return handle_inbound_delegate(
                    room,
                    from_persona=decision.from_persona,
                    content=decision.content,
                    chip=_chip,
                    webhook=post_webhook,
                    environ=os.environ,
                )
            if decision.kind == RouteKind.MONITORING_ALERT:
                return handle_monitoring_alert(
                    room,
                    content=decision.content,
                    channel_id=str(message.channel.id),
                    webhook=post_webhook,
                    environ=os.environ,
                    webhook_id=str(getattr(message, "webhook_id", "") or ""),
                    message_id=str(message.id),
                )
            assert decision.incoming is not None
            return handle_message(
                decision.incoming,
                config,
                chip=_chip,
                webhook=post_webhook,
                environ=os.environ,
            )

        result = await asyncio.to_thread(_dispatch)
        await asyncio.to_thread(post_budget_notice)
        stats["last_message_at"] = time.time()
        if result.ok:
            stats["ok"] += 1
            stats["last_error"] = ""
            if decision.kind == RouteKind.WEBHOOK_DELEGATE:
                print(
                    f"processed channel={room.id} route=webhook_delegate from={decision.from_persona}",
                    flush=True,
                )
            elif decision.kind == RouteKind.MONITORING_ALERT:
                print(f"processed channel={room.id} route=monitoring_alert persona=pm", flush=True)
            else:
                incoming = decision.incoming
                assert incoming is not None
                persona = resolve_persona(
                    room,
                    author_id=incoming.author_id,
                    author_handle=incoming.author,
                )
                route = decision.kind.value
                print(f"processed channel={room.id} persona={persona} route={route}", flush=True)
            if result.alert and dispatch_error_visible_in_channel(result.alert):
                try:
                    await message.channel.send(format_channel_dispatch_error(result.alert)[:2000])
                except Exception as exc:
                    print(
                        f"ERROR: failed to post delegate hop cap notice: {exc.__class__.__name__}",
                        file=sys.stderr,
                        flush=True,
                    )
        else:
            stats["fail"] += 1
            stats["last_error"] = result.alert
            print(f"ERROR: {result.alert}", file=sys.stderr, flush=True)
            if dispatch_error_visible_in_channel(result.alert):
                try:
                    await message.channel.send(format_channel_dispatch_error(result.alert)[:2000])
                except Exception as exc:
                    print(
                        f"ERROR: failed to post dispatch error: {exc.__class__.__name__}",
                        file=sys.stderr,
                        flush=True,
                    )

    client.run(token)
    return 0
