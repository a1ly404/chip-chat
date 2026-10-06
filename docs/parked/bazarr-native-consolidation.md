# PARKED: Bazarr native consolidation (homelab owning-lane)

**Verified live 2026-10-05 ~16:30 PDT (OCgo window). All reads read-only.**

## Ally ruling (2026-10-05 chat)
> "bazarr shouldn't be on docker, it is native ... as the other arr programs which are native"

Native = canonical. Docker instance to retire.

## Verified state (evidence)

| instance | what it is | port | status | db last write |
|---|---|---|---|---|
| **native** | Windows nssm service `Bazarr` (DISPLAY_NAME Bazarr) → python.exe PID 6588 | **0.0.0.0:6767** (HTTP 200) | RUNNING, **active** | `C:\ProgramData\Bazarr\db\bazarr.db` — **2026-10-05 16:25:50 PDT (live, minutes-fresh)** |
| docker | container `bazarr` (lscr.io/linuxserver/bazarr, compose service `bazarr`, volume `bazarr_config`) | 127.0.0.1:**16767** (HTTP 200) | Up 6h, **idle** | volume db frozen at container start 12:21:18 PDT, zero writes since |

Also: sonarr/radarr/plex are native Windows services and have NO docker containers
(monitoring_api.py:304 native-wall notes "sonarr/radarr/plex are named in [the native list]").
Bazarr is the only arr still running as a docker container on the stack.

## The blind spot (why cc read bazarr alerts as "stale false positive")

- monitoring-api re:6767 via docker-network DNS name `bazarr` → resolves to the
  DOCKER container only (docker inspect aliases `['bazarr']`). The monitor never
  probes the native instance.
- chip service-recovery verify-before-act checks docker for `container=bazarr`
  (allowlist member) → healthy → "alert appears stale; no restart sent" (2:41/2:50 posts).
- Net effect: a real native-Bazarr crash pages nobody; chip suppressions mask it.

## Fix sequence (owning lane = homelab / razzorstack Windows SoT)

1. **Retire docker bazarr** (canary order):
   a. `docker stop bazarr` → confirm native UI still 200 on :6767 and subtitle sync works.
   b. Remove compose service `bazarr` (HyperV-flip docker-compose.yml + override)
      + the `bazarr_config` volume claim from `razzor-nas-torrents` sidebars it leaves.
   c. Keep a tar of `bazarr_config` volume as backup for 30 days before deletion.
2. **Monitor re-class** (razzorstack SoT): move `bazarr` into the native-list path
   monitoring_api.py uses for sonarr/radarr/plex (port+PID check, no docker restart);
   remove `bazarr` from AUTO_RESTART_SERVICES.
3. **Chip lane (OCgo, this repo)**: after (1) lands, remove `bazarr` from
   `config/chip_relay/service_recovery.json` `service_allowlist` — native-service
   alerts then hit the missing-container/native wall and are tell-only. NOT before
   (removing it while docker instance still exists would strand a recovery lane).

## Verification steps for the owning lane

- `sc.exe query Bazarr` → RUNNING
- `netstat -ano | findstr :6767` → LISTENING (python 6588)
- `docker ps --filter name=bazarr` → empty after retire
- monitoring-api starts with no `could not restart bazarr` daemon-error rows going forward
- one live chip soak tick with 0 bazarr verdicts
