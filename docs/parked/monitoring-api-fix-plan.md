# PARKED — monitoring-api stale-image fix package (homelab lane executes; 2026-09-28)

> Lane law: razzorstack writes are Windows-SoT-only (RAZ-237 / golden 16); chip
> lane cannot commit or execute compose mutations there. Package is
> state-diagnosed and one-shot-ready; the owning lane (or homelab-dev agent)
> applies it, or Ally explicitly re-lanes. Diagnosis below is read-only.

## Live diagnosis (machine-checkable, verified 2026-09-28 ~10:20 PT)

- `monitoring-api` container: Up, StartedAt 2026-09-26T14:47:20Z, bound to image id
  `sha256:8e5db9708c58…dd71dd16` (`docker inspect monitoring-api --format {{.Image}}`).
- Same id is DEAD in the daemon: `docker image inspect <that-id>` →
  `Error response from daemon: No such image` (re-probed; exit 1).
- Current `discord-bot:latest` resolves to **`0e63a7cb8b39`** (created 2026-09-26T03:54Z)
  — the tag moved after the container was created, and the old image vanished
  (build/prune cycle), leaving the running container pinned to a deleted id.
- Consequence: `monitoring-api`'s own monitor loop (Docker SDK inside the
  container) queries `/images/<id>/json` for its now-dead image id every loop →
  `404 Client Error … No such image: sha256:8e5db…` — observed LIVE every
  ~2 min since 2026-09-25 22:14 PT (docker logs monitoring-api --tail, five windows sampled,
  errors present in all; `/health` stays 200 — the "green but dying" signature,
  MONITOR_CASES case 4).
- Stale set OVER ALL 25 running containers (verified by per-cid probe):
  **exactly one** — `/monitoring-api`. No other container binds a dead image id.

## One-shot fix (executed FROM the Windows SoT clone directory, compose context)

```bash
# from the razzorstack compose directory (Windows SoT lane):
docker compose up -d --force-recreate --no-deps monitoring-api
# then verify (receipts to keep):
docker inspect monitoring-api --format '{{.Image}}'        # expect sha256:0e63a7cb8b39… (current discord-bot:latest)
docker logs monitoring-api --tail 50 2>&1 | grep -c "No such image"   # expect 0 in the post-restart window
```

Expected receipts: image id flips to `0e63a7…`; `No such image` count drops to 0 and stays
0 across one full 4h cooldown window; chip-feed alert class `internal-loop-degrade`
auto-silences when their fix lands (already wired no-flap).

## Law notes

- Chip lane executes NOTHING here (compose/recreate is outside the executor's
  allowlist on purpose; BLOCKED_TARGET_PATTERNS wins).
- This unblocks BOTH: (a) Card 2's HARD blocked-until clause → the Phase B
  resume clock can start counting; (b) feed dead-letter pressure on the
  monitoring-api flap (RAZ-16 learning `498de08f` class).
- pictured alternative NOT recommended: `docker tag` re-pin of the dead id can't work —
  the image content is gone (not just untagged), hence recreate, not rescue.
