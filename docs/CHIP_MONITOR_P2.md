# Chip monitor feed — P2 homelab wake (#chip-dev rehearsal)

**Status:** code on `main`; **production Discord wake for homelab faults requires Ally GO** (`CHIP_FEED_HOMELAB_WAKE=1`). `#monitoring-logs` stays unwired.

P1 (stamp `6b81e67`, PR #147) added read-only tool argv in `config/chip_tools.json`: `docker-inspect`, `docker-logs`, `wg-show`. P2 runs those checks inside the relay feed and posts a **four-line receipt** to `#chip-dev` when a fault persists (verify-twice + 4h cooldown). The feed does **not** run write tools or arbitrary shell.

## What triggers a homelab receipt

| Check | Probe path | Fault examples | Evidence prefix |
| --- | --- | --- | --- |
| WireGuard handshake | `wg-show` → `docker exec wireguard wg show wg0 latest-handshakes` | missing/stale (>180s) | `tool:wg-show.*` |
| `/downloads` on `razzor-nas-torrents` | `docker-inspect` qbittorrent `Mounts` + optional `statvfs` on `CHIP_FEED_DOWNLOADS` | wrong volume, overlay, &lt;2 GiB free | `tool:docker-inspect.*` / `tool:downloads-df.*` |
| qBittorrent reachable / not firewalled | `CHIP_FEED_QB_URL` JSON `connection_status` while container `State.Status=running` | `firewalled` | `tool:qb-connection.*` |

`docker-logs` on `monitoring-api` still surfaces `No such image` as `tool:docker-logs.no-such-image`. **`al-9501-dead-image` stays `propose`** — receipts name the fault; compose-recreate is not auto-run.

Unreachable docker/wg/qB API → **`status: blocked`** on the receipt (no crash loop).

## Wake law (Discord)

- **Channel:** `CHIP_WEBHOOK_DEV` only (`#chip-dev`). Never `#monitoring-logs` in this slice.
- **Gates:** `CHIP_FEED_ENABLED=1`, stand-down off, verify-twice (two consecutive ticks with the same fingerprint), 4h per-fingerprint cooldown.
- **Homelab GO:** `CHIP_FEED_HOMELAB_WAKE=1` — without this, probes may run and `monitoring/latest.json` updates, but **no homelab receipt is posted**.
- **No LLM:** posts are system webhook lines (`username=chip-relay`) or the attached four-line body. No `chip room` turn from the feed.

## Environment

| Variable | Default | Role |
| --- | --- | --- |
| `CHIP_FEED_ENABLED` | off in bare metal; `1` in compose | Run feed loop |
| `CHIP_FEED_HOMELAB` | off | Run read-only homelab probes each tick |
| `CHIP_FEED_HOMELAB_WAKE` | **off** | Ally GO — allow homelab faults to post to `#chip-dev` |
| `CHIP_FEED_DOWNLOADS` | `/downloads` | Path for free-space check; absent → blocked `downloads-df` (no NAS bind required in compose) |
| `CHIP_FEED_QB_URL` | unset | qBittorrent API URL for `connection_status`; unset skips firewalled check |
| `CHIP_WEBHOOK_DEV` | Infisical | Target webhook for tell-tier posts |

Health probes (`monitoring-api`, `discord-bot`) use the same tell-tier path but **do not** require `CHIP_FEED_HOMELAB_WAKE`.

## Enable after Ally GO (Hyper-V deploy)

1. Rebuild relay image from a clean tree at or after `6b81e67` (Docker CLI + sock mount). See `docs/DOCKER.md`.
2. Confirm `.env` / Infisical still supply `CHIP_WEBHOOK_DEV` (not monitoring-logs).
3. Set on the deploy host (operator `.env` or compose `environment`):

   ```text
   CHIP_FEED_ENABLED=1
   CHIP_FEED_HOMELAB=1
   CHIP_FEED_HOMELAB_WAKE=1
   CHIP_FEED_QB_URL=http://host.docker.internal:<qb-port>/api/v2/transfer/info
   ```

   Adjust `CHIP_FEED_QB_URL` to the homelab-reachable qBittorrent API (read-only GET).

4. `docker compose up -d --no-build --force-recreate chip-relay` (or `./scripts/chip_relay_up.sh` if stuck in `Created`).
5. **Rehearse:** induce or wait for a real fault; expect one four-line receipt on `#chip-dev` after two feed ticks (~10 min at 300s interval). Evidence line must cite a `tool:*` slug from the table above.
6. **Writes:** `next:` lines point at **propose** runbooks only (`compose-recreate`, `disk-cleanup`, `al-9501-dead-image`). Scoped GO still needs `fp=` `lane=` `exp=` via `chip.go_scope.propose_go_marker`.

## Tests

```bash
python3 -m pytest scripts/test_homelab_feed.py scripts/test_chip_relay_monitor_feed.py -q
```

## Out of scope (P2)

- Infisical secrets for standup/Linear (RAZ-338 — Ally).
- Auto-execute runbooks or tier-1 restart tools from the feed.
- Wiring `#monitoring-logs` (refusal drill first — see `docs/ops/ECOSYSTEM_HANDOFF.md`).
