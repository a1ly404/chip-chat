# Host-hook contract

Chip Chat runs inside containers, but a few recovery and supervision duties can only
happen on the **host** (the machine running the container engine). The engine does
**not** implement these. It names them, says what it expects from each, and leaves the
implementation to a deployment plugin.

A plugin declares its implementation in a manifest like
[`plugins/example/host-hooks.yaml`](../plugins/example/host-hooks.yaml). CI validates
that manifest (`scripts/test_host_hooks_manifest.py`).

## Common rules

- **Receipts.** Every hook run appends one JSON line to
  `${CHIP_HOST_RECEIPTS_DIR}/<hook-name>.jsonl`:
  `{"ts": "<ISO-8601 UTC>", "hook": "<name>", "exit": <int>, "detail": "<short text>"}`.
  `CHIP_HOST_RECEIPTS_DIR` is a host path chosen by the plugin.
- **Exit codes.**
  - `0` ok, or nothing to do.
  - `1` the hook ran and found a problem it could not fix.
  - `2` the hook acted (repair or restart performed).
  - `3` refused by a guard (for example, a maintenance window or cutover lock).
  - `64` usage or configuration error.
- **Idempotent.** Running a hook twice in a row must be safe.
- **No secrets in receipts or logs.**
- **Guards.** Hooks that restart anything must honour a plugin-defined lock or
  maintenance file and exit `3` while it is present.

## Hooks

| Hook | Purpose | Cadence / trigger | May restart things |
|------|---------|-------------------|--------------------|
| `host-watchdog` | Check the container engine responds (`docker info` or equivalent); record health. | every 5 min | no |
| `host-detect-engine` | Detect which engine backend is active (desktop VM, native, Hyper-V and so on) and record it for the other hooks. | every 15 min | no |
| `host-repair-docker` | Bring an unhealthy container engine back (restart the engine service or app). | at logon, plus every 15 min; acts only when `host-watchdog` reports unhealthy | yes (engine) |
| `host-restart-stack` | Run `docker compose up -d` for the Chip Chat stack after boot or after a repair. | at boot or logon; may be kept disabled by a guard | yes (containers) |
| `host-scheduler-receipt-check` | Read-only check that the in-container scheduler worker wrote a fresh receipt for every job in its last cycle. | hourly | no |

### Inputs and env (all hooks)

| Variable | Meaning |
|----------|---------|
| `CHIP_HOST_RECEIPTS_DIR` | Where hook receipts go (host path). |
| `CHIP_COMPOSE_DIR` | Directory holding the stack's compose file (`host-restart-stack` only). |
| `CHIP_DOCKER_BIN` | Absolute path to the docker CLI; hooks must not rely on `PATH`. |
| `CHIP_HOST_LOCK_FILE` | When present, hooks that may restart things exit `3`. |
| `CHIP_SCHEDULER_MAX_AGE_MIN` | `host-scheduler-receipt-check`: max receipt age before it exits `1` (default 90). |

## OS mapping

| OS | Mechanism | Example |
|----|-----------|---------|
| Linux | cron line or systemd timer | `*/5 * * * * /opt/chip-hooks/host-watchdog.sh` or `chip-host-watchdog.timer` (`OnCalendar=*:0/5`) |
| macOS | launchd agent | `~/Library/LaunchAgents/org.example.chip.host-watchdog.plist` with `StartInterval` 300 |
| Windows | Task Scheduler task | Task `Chip-host-watchdog`, time trigger repeating every 5 min, plus a logon trigger so it survives a reboot |

Reboot survival: always pair an interval trigger with a boot or logon trigger
(`@reboot`, `RunAtLoad`, a logon trigger) or use a timer that persists (`Persistent=true`).

## In-container scheduler

Recurring Chip Chat jobs (standup, done-gate watchdog, case-intake beat) are **not**
host hooks; they belong to a scheduler worker inside the stack (for example supercronic
reusing the relay image). [`docs/examples/scheduler.crontab`](examples/scheduler.crontab)
shows the expected format. Each job writes a receipt to
`${CHIP_CHAT_DATA_DIR}/scheduler/receipts/<job>.jsonl` with the same fields as above.
