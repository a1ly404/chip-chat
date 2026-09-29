# RAZ-335 port-death CORRELATION LOG (Lyla ruling 2026-09-29T18:2xZ)

Law: for EVERY observed port-death or recovery, record (a) the live exit IP at the time, (b) the cached/expected exit, (c) whether a gluetun reconnect preceded the death. Rows append-only; clusters at exit-rotation boundaries => the re-attach bug; same-exit-intact deaths => a different failure class.

## Row set (all live-verified; LUMOCHECK-DOWN substitution receipts carry every field)

| # | observed-at (UTC) | (a) live exit IP | (b) cached/expected exit | (c) gluetun reconnect preceding | port state | class inference |
|---|---|---|---|---|---|---|
| 1 | 2026-09-19 (shadow-stack proof) | 38.88.124.105 | same | no (fresh stack) | OPEN & verified end-to-end (nc payload round-trip; class healthy when binding fresh) | baseline |
| 2 | 2026-09-26T05:44Z (gluetun boot) | 38.88.124.105 (live egress constant from start) | cache = 192.30.89.27 (openvpn SERVER-side address, corrupted semantic) | boot-attach only | unknown (monitoring cadence not surfaced) | watch |
| 3 | 2026-09-27-28 window | n/a (monitoring cadence reads port:0 = the qbittorrent-monitor blindspot; the LEASE-follow path is dead-model) | same | YES - 12 re-init cycles logged into gluetun from 09-26 to present (~hourly cadence late on 09-28), all at peer 192.30.89.29 | dead (retro-receipts only) | candidate: exit-stable + port-dead since before THIS window |
| 4 | 2026-09-29T15:23Z–17:24Z (flap sampler, 12×10min) | 38.88.124.105 CONSTANT (no rotation in-window) | cache 192.30.89.27 CONSTANT divergent | NO reconnect fires inside the window | CONSTANT-DEAD (12/12) | NOT the re-attach bug: exit stayed intact the whole window |
| 5 | 2026-09-29T18:00Z (qb-restart under named GO) | 38.88.124.105 | cache 192.30.89.27 | the restart was NOT a tunnel thread; no rotation fired | still dead; netns-listener control = external dial TIMEOUT (rc 124) | THEORY-CLASS FAILURE: Listener-drift is falsified; same-exit + port-dead = airvpn edge delivery-side (case-2 risk per Lyla framing) |

## The ruling outcome from THIS row set

Since every death observation pairs CONSTANT live exit (38.88.124.105) + the SAME peer endpoint across 12 tunnel re-inits (192.30.89.29) + a stale gluetun cache (192.30.89.27's actual info leak from the OPENVPN server-slot, not a tunnel rotation) — the data pattern says **the port died on the same exit, intact** = failure class 2, NOT class 1 (re-attach on exit rotation).

## Gluetun re-init table (evidence for column (c))

- 12 "Initialization Sequence Completed" in gluetun logs (3-day window, grep count). The visible cadence toward the end ~ every 59 minutes (09-28T19:20Z → 00:18Z+).
- Peer Connection Initiated: 'Pisces' at 192.30.89.29:443 (50 log-lines; single-server ovpn config).
- The "cached public IP" bug = gluetun reading the OVPN SERVER IP slot (192.30.89.27) instead of the outbound WAN egress: my live probes through docker exec + WAN probes = the real 38.88.124.105.

