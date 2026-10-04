# Monitor probe contract spec — post-cutover paths & volumes (SPEC ONLY)

**Status:** SPEC draft v1 (2026-10-04). This document contains **no code change
and no implementation**. Implementation is a follow-up after Ally GO.

**Scope when implemented:** `scripts/chip_relay/homelab_probes.py` only — the
`downloads-mount` probe and the `wg-killswitch` probe. No changes to
`monitor_feed.py` / `_targets()` (per-target URL law already landed in #163),
no compose changes, no cron, no wake gates.

---

## 1. Live receipts this spec is grounded on (2026-10-04, read-only)

**qbittorrent (post-recovery, Up healthy):**
- `docker inspect qbittorrent` Mounts: `razzorstack_qbittorrent_config ->
  /config`, `razzorstack_downloads -> /downloads`.
- `razzorstack_downloads` volume inspect: `driver=local`, CIFS
  `device=//192.168.1.100/Media/Torrents` (NAS **Nancy**,
  `username=ally`, `vers=3.0`).
- `razzor-nas-torrents` volume inspect: same NAS device
  `//192.168.1.100/Media/Torrents`, differing mount options only.
- `docker exec qbittorrent ls /downloads`: library listings present;
  `df -h /downloads`: `//192.168.1.100/Media/Torrents  21T  18T  3.8T  83%`.

**wireguard netns (read-only exec receipts):**
- `docker exec wireguard ip -4 route show default` →
  `default via 172.18.0.1 dev eth0`.
- v4 kill-switch chain present (`WGKS_OUT`), v6 present (`WGKS6_OUT`); the chip
  probe's `-C` assertions target exactly `-o eth0 -j DROP` inside those chains.

**Chip probe dead-letters recorded at tick 2026-10-04T20:22:00Z:**
- `downloads-mount -> dead-letter | tool:docker-inspect.downloads-wrong-mount`
- `wg-killswitch -> dead-letter | tool:wg-route.not-wg0`

**Compose history anchor:** `ff71d93 fix: pin qBittorrent NAS volumes and
WireGuard wg0` established the post-cutover NAS-CIFS download contract
(RAZ-237 era): the torrent DATA lives on the Nancy NAS;
`razzor-nas-torrents` and `razzorstack_downloads` are two volume NAMES bound
to the same NAS path.

---

## 2. `downloads-mount` probe — new contract

Current law (stale): the probe compares the qbt `/downloads` mount's volume
name/source against the single pin `TORRENT_VOLUME = "razzor-nas-torrents"`
and faults `tool:docker-inspect.downloads-wrong-mount` otherwise. Tonight this
fired against the CORRECT post-cutover CIFS volume
(`razzorstack_downloads`), a false fault.

Spec change:

1. `TORRENT_VOLUME` is superseded by `TORRENT_VOLUME_ALLOWLIST: frozenset =
   {"razzor-nas-torrents", "razzorstack_downloads"}`.
2. `mount_alert()` accepts the `/downloads` mount when **either**:
   - the mount's volume Name **or** Source contains one of the allowlist
     names, **or**
   - the mount resolves to a CIFS device path containing `/Media/Torrents`
     (the Nancy NAS host share). Exact-match rule proposed with receipts:
     assert `"//192.168.1.100"` in the resolved device and `"Media/Torrents"`
     in it (NAS-target identity rather than name guessing).
3. qbt container state != running → `_blocked(...)` receipt
   (`tool:docker-inspect.qbt-not-running`), NOT a wrong-mount fault. (A stopped
   client is a transport-class state, not a mount misconfiguration.)
4. `downloads-free` (disk-space check) keeps its current semantics; the volume
   NAME is irrelevant to it.

Fingerprint note: after implementation the `tool:docker-inspect.
downloads-wrong-mount` fingerprint retires; the next tick observes the new
key (dedup/cooldown tables treat it as a new fingerprint — expected, and to
be noted in the implementation PR).

## 3. `wg-killswitch` probe — new contract

Current law (stale): `killswitch_alert()` asserts
`default route == dev wg0` FIRST, faulting `tool:wg-route.not-wg0` and
**short-circuiting** (returns before the iptables assertions are ever run).
Post-cutover, the wireguard container's default route is eth0 by design and
protection is enforced by `WGKS_OUT`/`WGKS6_OUT` `-o eth0 -j DROP`
(cur-comm receipt above). So the probe's route assertion fails on a WELL-
protected netns tonight, and never reaches the drop-rule checks.

Spec change:

1. Remove the unconditional wg0 route assertion as a fault path.
2. New kill-switch law (evidence-ordered):
   - **v4 drop rule** (`iptables -C WGKS_OUT -o eth0 -j DROP`) and
     **v6 drop rule** (`ip6tables -C WGKS6_OUT -o eth0 -j DROP`) are the
     authoritative assertions, matching the current compose-era law
     (`b091479 fix(vpn): fail health unless eth0 kill switch is installed`,
     `4cc9eba fix(vpn): give qBittorrent iptables so the kill switch check can
     run`, `c2e6ef8 fix: drop WireGuard eth0 egress unless the tunnel is up`).
   - The wg0-default route becomes a SECONDARY receipt only: when BOTH drop
     rules are green and the route shows `dev eth0`, verdict is healthy
     (protected egress); when BOTH drop rules are missing, fault
     `tool:wg-killswitch.no-drop-rules` (new label; carries an unverified
     state: egress is unprotected).
   - Per-rule detail: each missing Drop rule faults individually
     (`tool:wg-iptables.missing-drop` / `-tool:wg-ip6tables.missing-drop`,
     unchanged labels).
3. Transport-block handling (`_unreachable`) keeps today's semantics.

Fingerprint note: `tool:wg-route.not-wg0` retires with implementation.

---

## 4. Tests to update/add at implementation time (enumerated in advance)

- `mount_alert` accepts `razzorstack_downloads` named volume (new test).
- `mount_alert` accepts a NAS-path device mount (new test).
- `mount_alert` emits `tool:docker-inspect.qbt-not-running` when the qbt
  inspect shows a non-running state (new test).
- `killswitch`: eth0 default + GREEN drop rules → no fault (new test;
  tonight's exact state).
- `killswitch`: wg0-default route → no fault (regression of the old contract).
- `killswitch`: BOTH drop rules missing → `tool:wg-killswitch.no-drop-rules`
  (new label; test).
- Existing tests asserting `downloads-wrong-mount` for any
  `razzorstack_downloads`-like mount, or a not-wg0 fault with green drop
  rules, are updated/removed.

## 5. Explicit non-goals (unchanged by this spec)

- No changes to the monitoring-api target/URL law (landed in #163).
- No Discord wake flips (CHIP_FEED_HOMELAB_WAKE stays off).
- No qbittorrent/discord-bot/qbittorrent-monitor container starts (Ally
  holds those).
- No razzorstack compose changes (the probe contract keeps consuming the
  compose state as-is).
