# Chip Chat morning standup (RAZ-330)

**Parent:** [RAZ-329](https://linear.app/razzormail/issue/RAZ-329) · **Signal contract:** razzorstack `docs/ops/CHIP_CHAT_STANDUP_SIGNAL.md`

## Run (dry-run default)

```bash
export CHIP_CHAT_STANDUP_SIGNAL_FILE=~/appdata/chip-chat/standup-signals.json
export GITHUB_TOKEN=...          # gym-app CI + live-e2e search
export LINEAR_API_KEY=...        # GymLog queue counts
# optional: GYMLOG_ORIGIN=https://gym.razzormail.com

./bin/chip-standup-morning --receipts-dir /tmp/standup-receipts
```

Schedule **~11:25 America/Los_Angeles** (deadline 11:35 PT for soak). Cron is Ally GO; script is safe to dry-run without webhook.

## Collectors (mandatory)

| Collector | Env | Output field |
|-----------|-----|----------------|
| GHA gym-app CI | `GITHUB_TOKEN`, `CHIP_STANDUP_GITHUB_REPO`, `CHIP_STANDUP_GHA_WORKFLOW` | `deploy`, `gate` |
| Site probe | `GYMLOG_ORIGIN` | `gate` |
| Linear queue | `LINEAR_API_KEY` | `autodev_qa` |
| live-e2e issues | `GITHUB_TOKEN` | `alerts` |

Homelab section: optional `CHIP_HOMELAB_STANDUP_JSON` (read-only file).

## Anti-silence

Every run writes `CHIP_CHAT_STANDUP_SIGNAL_FILE` when set. Collector failures still emit `received: true` + `missed_payload: true` + `verified_as_of`.
