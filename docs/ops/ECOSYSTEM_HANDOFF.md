# Ecosystem handoff — Chip Chat and the Razzorstack homelab

Written 2026-10-02 for the next agent. Linear is the history log. This file is the operating map. Do not treat a green container, a merged PR, or a quiet window as Done.

Live read-only check at about 2026-10-02T03:24Z (WSL `docker info` exit 0, `docker ps -a`). Nothing was restarted, recreated, or reconfigured for this document.

## What the two systems are

**Chip Chat** is an OpenRouter-only multi-agent CLI (`bin/chip`) plus a Discord relay (`bin/chip-relay`) on `#chip-dev`. Repo: [a1ly404/chip-chat](https://github.com/a1ly404/chip-chat). It is not razzorstack, not homelab Open WebUI, and not the old Grok bot.

| Role | Path | Writes |
| --- | --- | --- |
| Pull-only checkout | `~/Tools/chip-chat` when `main` is checked out | Forbidden (`git pull` / read only) |
| Agent worktree | `~/Tools/chip-chat-worktrees/<ticket-or-branch>` | Required for code, docs, commits |
| Hyper-V deploy tree | `C:\Users\17783\chip-chat` | Dirty, old SHA, line-ending noise. Not the build context. Never `git reset` it |

Build images from a clean WSL tree. Run compose from `working_directory` `/mnt/c/Users/17783/chip-chat` with `--no-build`. Never compose from a WSL cwd that makes Docker rewrite paths to `\\wsl.localhost\...\mnt\c\...` (access denied) or UNC into `U:\C\192.168.1.100\Media`.

**Razzorstack** is the homelab repo. Windows source of truth: `C:\Users\17783\HyperV-flip\repos\razzorstack`. WSL `~/Tools/razzorstack` is read-only. Commits need Windows `git.exe` because WSL cannot exec the CRLF pre-commit hooks. Hook Python must be `C:\Users\17783\AppData\Local\Programs\Python\Python313\python.exe`. Git Bash `python3` is the Microsoft Store stub and exits 49. Last diagnostic checkout was branch `fix/discord-native-health-monitor-alerts` at `d757180` ([PR #596](https://github.com/a1ly404/razzorstack/pull/596)), behind `origin/main` `eec00a2`. Do not edit that checkout.

**Lane law.** Chip Chat work stays in chip-chat. Razzorstack findings get parked: a Linear comment plus a row in `docs/PARKED_FINDINGS.md`. Do not self-execute razzorstack from a chip-chat WSL agent. Ally has explicitly GO'd specific razzorstack PRs during this effort. That is not a standing waiver.

Program tracker named in `AGENTS.md` is [RAZ-16](https://linear.app/razzormail/issue/RAZ-16/chip-chat-discord-multi-agent-room-grok-parity-kiss) (In Progress, Ally, project Platform / Automation). The Chip Chat Linear project itself currently lists five unarchived issues (RAZ-338 through RAZ-342). After a ship, post `learning:` on RAZ-16.

## Live check (2026-10-02T03:24Z)

`docker info` succeeded. The engine is up. `com.docker.service` Running is not the same fact; this check used `docker info`.

| Name | Status at check | Note |
| --- | --- | --- |
| chip-chat-chip-relay-1 | Up ~5 min (healthy), image `chip-relay:latest` | Image digest vs git SHA was not read. See stamp note below |
| wireguard | Up ~5 min | No `(healthy)` in `docker ps`. Handshake and `wg0` were not inspected |
| qbittorrent | Up ~5 min (healthy) | Mount source (`razzor-nas-torrents` vs overlay) was not inspected |
| monitoring-api | Up ~5 min, image id `20551d2c353e` | Do not restart it to manufacture a second al-9501 window |
| prowlarr | Up ~5 min (healthy) | Container. Native Prowlarr on port 9696 was down at the earlier diagnostic; both facts stand |
| calibre | Created | Not running |
| lumo-tamer | Exited (1) 3 days | Still exited after the engine came back |

**RustDesk:** not fully checked. A Windows PowerShell query hung and was abandoned. Do not reconfigure RustDesk. Do not run another long PowerShell, `wmic`, `Get-Process`, or log scrape as part of finishing this handoff.

Earlier the same day (2026-10-02) Docker was down: WSL distro `docker-desktop` Stopped, pipes missing, `com.docker.service` still Running. NAS `//192.168.1.100/Media` stayed fine (about 3.8TB free). Engine started again at 2026-10-02T03:20:50Z via Docker Desktop.exe. Most containers came Up within seconds. The table above is the state a few minutes after that start.

Relay image last stamped `BUILD_SHA` `f6fd66a` (merge of [chip-chat #144](https://github.com/a1ly404/chip-chat/pull/144)). `origin/main` at this writing is `c4c43c7` (merge of [#145](https://github.com/a1ly404/chip-chat/pull/145)). Verify whether the running container contains #145. Do not claim it does. #145 dropped the relay bind of `razzor-nas-torrents` because the mount failed with host is down while the engine was dead. That change can be compose-only relative to the `f6fd66a` image.

## Receipts and gates already shipped

The receipt gate requires `claim`, `status`, `evidence`, and `next`. Prose with zero field lines now passes ([chip-chat #143](https://github.com/a1ly404/chip-chat/pull/143), merge `2fb8c60`). Half receipts and unlabeled tables still error. Before that fix, Ally's Ted Lasso question at 2026-10-01T02:38Z was replaced with `[chip-relay] ERROR: receipt schema: missing fields` and the model answer was discarded.

The transcript stores the gated body (`CHIP_STORE_GATED=1` in `scripts/chip_relay/gateway.py` and `scripts/chip_relay/receipt_gate.py`).

Scoped GO: deploy and absorb need `fp=` `lane=` `exp=` with a future timestamp matching the ticket (`scripts/chip/go_scope.py`). Bare `GO` is not enough for those words. Example shape: `GO: al-9501 fp=al-9501 lane=razzorstack exp=<ISO-8601 Z>`.

**Topology (binding — PM hub):** Ally talks only to `pm`; specialists are charters, not a second chat. Same `#chip-dev` webhook, different `username=` for visibility — not agent↔agent coherence (RAZ-337). `config/chip_topology.json` + `docs/plans/topology-verdict.md`. Trio/swarm mothballed; `delegate:` charter-only, cap 3.

Runbook classes in `config/chip_chat/runbooks.json` (unknown ids fail closed to propose):

| Class | Ids |
| --- | --- |
| auto | `restart-named-service`, `clear-stale-suppress`, `re-run-health-probe`, `post-learning` |
| propose | `discord-bot-loop-restart`, `al-9501-dead-image`, `compose-recreate`, `disk-cleanup`, `encode` |
| blocked | `remux`, `plex-restart`, `credential-flip`, `media-delete` |

`encode` is in the registry as propose. The operator list that agents usually recite omitted it; the file is the source of truth.

The refusal drill posts the four-line blocked receipt in `scripts/chip_relay/refusal.py` (`claim` / `status: blocked` / `evidence: none` / soak next-step). `#monitoring-logs` must stay unwired until that path is rehearsed. The first sentence there is the blocked receipt. Do not graduate al-9501 after one quiet window. `SOAK_NOT_BEFORE` in that module is `2026-10-01T02:11:00Z`. A pass is one data point.

Deploy-claim hooks on razzorstack cover `scripts/monitoring_api.py` and `scripts/monitoring_image_label.py`. `--no-verify` skips them. Push audit only on those paths between `github.event.before` and `sha`.

Read-only chip tools added in #144 (`f6fd66a`): `docker-inspect` (qbittorrent, `allow_extra_args` false), `docker-logs` (monitoring-api, tail 80), `wg-show` (`wg show wg0 latest-handshakes`). Relay startup refuses `CHIP_RELAY_AUTO=true` (`scripts/chip_relay/startup.py`). Relay does not auto-run mutations.

Correction against an older mental model: before #144 the narrow set people cite was `pytest`, `git-status`, `task-show`. Current `config/chip_tools.json` also has `git-diff`, `git-log`, `lane-test`, `docker-build`, `vite-build`, `razzorstack-diagnose`, and RAZ-335 tier-1 rows (`gluetun-restart`, `qb-restart`, `airvpn-port-regab`, `fw-rule-reload`, `qb-port-update`). Those tier-1 rows are `audience: global-only`, high risk, and require a skill envelope plus a per-run named GO. They are not a general docker restart tool, and they are not a license to mutate the homelab from the feed.

Windows apply stub [razzorstack #600](https://github.com/a1ly404/razzorstack/pull/600) (`eec00a2`) refuses without scoped GO and does not apply a diff.

Feed wake (#144): faults should receipt into `#chip-dev` with a 4h cooldown (`_POST_COOLDOWN_S = 4 * 3600` in `scripts/chip_relay/monitor_feed.py`). That is in git at `f6fd66a` and later. Confirm the running image actually posts. A container that is Up can still be silent.

## Homelab incidents

**POSIX cron.** `VAR=... cd dir && ./script` assigns the env only to `cd`. Fixed in `bin/chip-standup-morning` ([chip-chat #138](https://github.com/a1ly404/chip-chat/pull/138)): the wrapper sets `CHIP_CHAT_DATA_DIR` and `CHIP_CHAT_STANDUP_SIGNAL_FILE` itself when they are missing. Live cron must call that wrapper. [RAZ-339](https://linear.app/razzormail/issue/RAZ-339/standup-signal-revival-1125-cron-env-prefix) is still Backlog.

**Infisical.** Chipchat prod has only `OPENROUTER_CC_API_KEY`, `DISCORD_CHIP_RELAY_TOKEN`, `CHIP_WEBHOOK_DEV`, `CHIP_WEBHOOK_SELFTEST`, and `DISCORD_V1_TOKEN`. Absent: `CHIP_STANDUP_GITHUB_TOKEN`, `LINEAR_API_KEY`, `CHIP_ALERT_INGEST_TOKEN`. [RAZ-338](https://linear.app/razzormail/issue/RAZ-338/blocked-standup-tokens-absent-from-infisical-chipchat) is assigned to Ally, Todo. Recheck comment 2026-10-01T05:14Z (names only). Do not invent values. Do not mark RAZ-338 Done. Standup and Linear-from-chip stay blind until Ally adds the three secrets.

**al-9501.** The monitoring loop used a dead Docker image id and logged `No such image`, skipping health. Recreate was palliative (a new dead id). Durable fix is `image_label_from_attrs` on live `Config.Image` ([razzorstack #597](https://github.com/a1ly404/razzorstack/pull/597)). One 4h window had 0 `No such image` and 111 healthy passes. The runbook stays propose (`docs/ops/al-9501-dead-image.runbook.md`). Do not restart monitoring-api to fake a second window. [RAZ-341](https://linear.app/razzormail/issue/RAZ-341/al-9501-durable-fix-monitor-must-not-cache-a-dead-image-id) is Backlog with no comments.

**qBittorrent / WireGuard.** Empty `wg_confs` (`No valid tunnel config found`). Downloads sat on the container overlay (~794MB) instead of named volumes `razzor-nas-torrents` and `razzor-nas-media`. Config lives at `~/.config/razzorstack/wireguard/wg0.conf` and a gitignored copy under `docker/wireguard/config/wg_confs/`. Never commit it. [razzorstack #599](https://github.com/a1ly404/razzorstack/pull/599) pinned the volumes and a healthcheck in git, but the live containers were created from `C:\Users\17783\AppData\Local\Temp\compose599-wg-qbit.yml`, not from the checkout. The checkout did not contain #599. Recreate from real main was skipped when the volume returned host is down. At the 2026-10-02T03:24Z check both containers were Up; that does not prove `wg0`, the named volume, or the healthcheck.

**Ted Lasso S04E09.** Sonarr grabs from LimeTorrents had no `.exe` in the title, so release profile "No RAR or archives" (ignores `exe` and `.exe`) did not reject them. Payloads were Windows MZ executables. Files and the FLUX torrent were deleted. Episode 24876 has `hasFile` false. Do not grab a replacement torrent. Sonarr, Radarr, and Plex are native Windows processes (ports 8990, 7878, 32400), not this compose. Prowlarr was down at the diagnostic (9696). A `prowlarr` container showed Up about 9 seconds after Docker started, and it was still Up (healthy) at 03:24Z. Say both facts.

**Path footgun.** Do not mount `M:\` or `//192.168.1.100/Media` through a WSL docker client. Docker Desktop prompts to share `U:\C\192.168.1.100\Media`. Click No. Use existing named volumes.

**Deploy tree.** `C:\Users\17783\chip-chat` is a dirty checkout at an old SHA plus line-ending noise. Never reset it. Never use it as `docker build` context. [RAZ-340](https://linear.app/razzormail/issue/RAZ-340/relay-redeploy-to-e8aa713-so-the-receipt-gate-is-in-prod) records an earlier redeploy to `e8aa713`. That stamp is stale relative to `f6fd66a` / `c4c43c7`. Verify the running image; do not trust the ticket's SHA as current.

**CI flake.** `test_concurrent_writers_flock_keeps_both_entries` can raise `JSONDecodeError`. Rerun the failed job once. Do not treat it as a product bug.

**Persona learnings.** `config/chip_chat/personas/<agent>/learnings.md` is loaded into the room system prompt. `scripts/chip/cli.py` passes `learnings.render_learnings_block(persona)` into the prompt composer. The block is advisory and does not override room law. The loader skips the banner line `not loaded at runtime`. `AGENTS.md` still says the runtime loader is backlog. The code disagrees; trust `cli.py`. `scripts/chip/learnings.py` module docstring still says "runtime wiring pending" even though `cli.py` calls it.

## What Chip Chat still cannot do

The channel is not at Grok-bot parity. Do not mark it done.

1. The feed must actually wake `#chip-dev` on real faults (missing `wg0`, downloads under 2GB, `No such image`, firewalled qBit) and a read tool must fill the evidence line. Confirm this in the running image, not only in git. Code for the 4h cooldown and the probes is on main (`scripts/chip_relay/homelab_probes.py`, `monitor_feed.py`). The last stamped image is `f6fd66a`. Main may be ahead.
2. Writes stay on runbook classes. Propose needs a human GO with `fp` `lane` `exp`. No general docker restart tool. The RAZ-335 tier-1 restart rows are a different, expired-scope contract. Do not reuse them as homelab self-heal.
3. Windows-side apply of a proposed diff after GO, with the deploy-claim hook. Stub #600 does not apply.
4. Secrets from Ally ([RAZ-338](https://linear.app/razzormail/issue/RAZ-338/blocked-standup-tokens-absent-from-infisical-chipchat)) before standup or Linear writes from Chip Chat.
5. Recreate wireguard and qbittorrent from merged compose on `origin/main`, then prove with inspect labels that `config_files` is the repo file, not the Temp yml, and that the healthcheck is baked in. Skip if the volume returns host is down; record that, do not invent a mount.
6. A second quiet window before al-9501 leaves propose. One window of 0 `No such image` and 111 healthy passes is not graduation.
7. `#monitoring-logs` stays quiet until the refusal drill is rehearsed there.
8. RustDesk is a separate stability item. Diagnose only. Do not rebuild it. This handoff did not finish the diagnosis.

## Operator rules agents keep breaking

- Do not compose from WSL paths.
- Do not approve `U:\C\` shares.
- Do not commit from chip-chat `main` or from WSL razzorstack.
- Do not use `--no-verify`.
- Do not print secrets, webhook URLs, API keys, or WireGuard private keys.
- Do not mark Done on a summary. Receipts need inspect output, handshake, compose hash, and a timestamp.
- A container that is Up can still be useless (no `wg0`, overlay disk, dead image id).
- Docker service Running is not the engine. Check `docker info`.
- Sonarr release profiles match the torrent title, not the payload extension.
- Do not grab a replacement Ted Lasso torrent.
- Do not restart monitoring-api to manufacture soak evidence.

## Linear history

Fetched 2026-10-02 via Linear MCP. Archive of Done issues was impossible (no archive tool). Include-archived search on the Chip Chat project still returned only the five open issues below. Never-done allowlist [RAZ-177](https://linear.app/razzormail/issue/RAZ-177/discord-bot-20-on-oracle-observer-of-ally-pc-shadow-cutover) (In Progress, Ally, Ops / Oracle) and [RAZ-186](https://linear.app/razzormail/issue/RAZ-186/ally-pc-wsl-reliability-detect-recover-root-cause-ops-owned) (In Progress, Ally, Household Autonomy) stay open. A 2026-09-29 triage comment on RAZ-16 listed both as human-gate / not eligible for a chip write pass.

Chip Chat project (unarchived):

| Issue | State | What it is for |
| --- | --- | --- |
| [RAZ-338](https://linear.app/razzormail/issue/RAZ-338/blocked-standup-tokens-absent-from-infisical-chipchat) | Todo, Ally | Three Infisical secrets still absent. Do not close |
| [RAZ-339](https://linear.app/razzormail/issue/RAZ-339/standup-signal-revival-1125-cron-env-prefix) | Backlog | 11:25 cron env prefix. Fix is #138; ticket not closed. Live cron must call the wrapper |
| [RAZ-340](https://linear.app/razzormail/issue/RAZ-340/relay-redeploy-to-e8aa713-so-the-receipt-gate-is-in-prod) | Backlog | Receipt-gate image redeploy to `e8aa713` on 2026-09-30. Superseded as a stamp by later merges; verify the running image |
| [RAZ-341](https://linear.app/razzormail/issue/RAZ-341/al-9501-durable-fix-monitor-must-not-cache-a-dead-image-id) | Backlog | Dead image id. No comments. Stays propose |
| [RAZ-342](https://linear.app/razzormail/issue/RAZ-342/cc-audit-2026-09-30-four-findings) | Backlog | 2026-09-30 audit: missing standup keys, cron prefix, relay 21 merges behind (then redeployed to `e8aa713`), al-9501 dead image. Receipts were on draft #137 |

[RAZ-16](https://linear.app/razzormail/issue/RAZ-16/chip-chat-discord-multi-agent-room-grok-parity-kiss) is the tracker `AGENTS.md` names. Recent useful comments, not a full scroll:

- 2026-09-30T21:43Z learning: Infisical m2m works; three standup keys absent; cron prefix bug; prod relay was 21 merges behind; al-9501 recreate was palliative. Receipts on draft #137. Read-only, no razzorstack writes.
- 2026-09-30T19:32Z learning: relay sitrep gate fail-closed at the webhook. Merged #136 squash `e8aa713`. Delegate default-off stays parked until that gate soaks.
- 2026-09-29 lane work (PR #95, RAZ-335 AirVPN diagnose, tier-1 restart rows) is historical scope with a midnight expiry. It is not a current license to restart gluetun or qBit.
- Older comments cover CI rescue (#92), Jev PRs, and triage tables. Read them on the issue before re-deriving a night.

Neighbors that are not Chip Chat project issues but show up in standup and relay searches: [RAZ-329](https://linear.app/razzormail/issue/RAZ-329/chip-chat-morning-standup-sot-deprecate-discord) (In Progress), [RAZ-331](https://linear.app/razzormail/issue/RAZ-331/oracle-silence-probe-chip-chat) (In Progress), [RAZ-332](https://linear.app/razzormail/issue/RAZ-332/disable-standup-discord-loops-http-deprecation) (In Progress), [RAZ-336](https://linear.app/razzormail/issue/RAZ-336/raz-shadow-native-airvpn-wireguard-single-server-then-cut-over-qb-off) (Backlog, AirVPN cutover). Do not execute those from this lane without a named GO.

## Do this next

1. Finish a RustDesk diagnosis without a long PowerShell scrape. Status here is not fully checked. Do not reconfigure or rebuild it. Docker engine was up at 2026-10-02T03:24Z (`docker info` exit 0); re-check `docker info` before any compose. `lumo-tamer` is still Exited (1) and `calibre` is still Created. Leave them unless a ticket names them.
2. Read the running `chip-relay` image stamp. Compare it to `f6fd66a` and to main `c4c43c7`. Say whether #145 is in the image or only in compose.
3. On a real fault (missing `wg0`, downloads under 2GB, `No such image`, firewalled qBit), confirm `#chip-dev` gets a four-line receipt whose evidence line came from `docker-inspect`, `docker-logs`, or `wg-show`. If the image is behind, that confirmation waits on a clean rebuild, not on a restart of monitoring-api.
4. Recreate wireguard and qbittorrent only from merged compose on `origin/main`, with scoped GO. Prove `config_files` is the repo file and the healthcheck is baked in. If the volume says host is down, stop and receipt that.
5. Leave [RAZ-338](https://linear.app/razzormail/issue/RAZ-338/blocked-standup-tokens-absent-from-infisical-chipchat) on Ally. Do not invent `CHIP_STANDUP_GITHUB_TOKEN`, `LINEAR_API_KEY`, or `CHIP_ALERT_INGEST_TOKEN`.
6. Collect a second quiet al-9501 window from existing logs. Do not restart monitoring-api to create one. Until that window exists, the class stays propose.
7. Keep `#monitoring-logs` unwired until the four-line refusal has been rehearsed as the first sentence there.
8. Do not treat Windows stub #600 as an apply path. A proposed diff still needs a human GO and a deploy-claim hook that actually applies.
