# Chip relay (skeleton)

Standalone process for a later Discord relay. This change does not connect to Discord and does not send chat completions.

## Bot identity

Use a **dedicated chip-relay Discord application**. Do not reuse the v1 bot token. A shared token is one gateway session, so the relay and the v1 bot cannot listen at the same time, and it would couple this repo to the old bot.

Infisical secret name: `DISCORD_CHIP_RELAY_TOKEN`.

Webhook URLs are also Infisical-only, one per fixed identity:

- `DISCORD_CHIP_RELAY_WEBHOOK_PM`
- `DISCORD_CHIP_RELAY_WEBHOOK_DEV`
- `DISCORD_CHIP_RELAY_WEBHOOK_TESTER`

Do not commit those values. The app is not minted in this PR.

## Fixed rooms

`config/chip_relay/rooms.json` is the only room list. The loader has no create-room API. `turn_limit` is 1. Speaker identity is the room's `as` field, supplied by config, not invented at runtime.

`CHIP_RELAY_AUTO` defaults to false. The skeleton refuses to start agent-initiated turns even if it is set true.

## Startup

`bin/chip-relay` loads config, requires `OPENROUTER_CC_API_KEY`, and calls `GET /api/v1/key`. `--listen` exits with an error because the gateway stub does not connect.

## Harness

Layer 1 and 2 run in CI (`scripts/test_chip_relay_harness.py`). A red mock test blocks the PR. Failures log once and do not retry. A chip call reported past 30 seconds is `slow_call_killed`.

Layer 3 is `bin/chip-selftest`. It refuses to start unless `CHIP_RELAY_SELFTEST=1`, and it exits before any live call when `monthly_usd` is at or above the $5 WARN gate. This skeleton does not post to Discord. The relay PR is not Done until one passing selftest transcript and spend line are pasted in the PR body.

Layer 4 watchdog pages on two consecutive failed heartbeats, then stops.

```bash
python3 -m pytest scripts/test_chip_relay.py scripts/test_chip_relay_harness.py -q
```
