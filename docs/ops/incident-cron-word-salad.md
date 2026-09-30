# Incident: cron word-salad

Linear: RAZ-337. verified-as-of: 2026-09-30T19:30:00Z

## Timeline

- AirVPN port-health cron (`airvpn_port_health.py --notify` every 15 minutes) was the `#monitoring-logs` alert source. No gluetun container was present.
- Agents treated a dense channel post as a receipt. Two false-completion signals and one coherence collapse preceded the real change: commenting one WSL crontab line.
- razzorstack PR #588 records that crontab edit as a docs receipt. Merge commit `11968de329dba0a4a1a31a7015b38a01e0d01274`. https://github.com/a1ly404/razzorstack/pull/588
- Chip-chat response is the receipt gate on the relay post path, not a razzorstack compose change.

## Verbatim degraded output

The ocgo word-salad body is not in this checkout, GitHub PR #588, or the local agent transcripts. It is not reconstructed here. Paste the channel archive under this heading when it is available.

## What would have caught it

A post without `claim` / `status` / `evidence` / `next`, a `done` with no evidence, or an execution claim without `tool:<id>` is replaced with `[chip-relay] ERROR: receipt schema: …` before Discord.
