# RAZ-335 tier-1 port health — CLIENT-AREA ESCALATION PATH (parked per Lyla ruling, 2026-09-29T18:1xZ)

verified-as-of: 2026-09-29T18:10Z (all values live-probed earlier today; LUMOCHECK-DOWN substitution receipts carry through).

## Escalation packet (the data the Client-Area lane needs me to hold)

| item | value |
|---|---|
| current tunnel egress (live, external dial source) | 38.88.124.105 — Canada/British Columbia, Vancouver (per the chosen Pisces config, see below) |
| gluetun control-API cached public IP (stale artifact) | 192.30.89.27 — post-reconnect divergence, consult `docs/ops/RAZ-335-t1-port-health-202609291445.md` for the constant |
| tunnel entry config | AirVPN_CA-Vancouver_Pisces_UDP-443-Entry3.ovpn (port 443, single Canada entry) |
| expected forwarded port | **54543 TCP+UDP** (reserved permanently by hand in the airvpn.org/ports panel; account-wide, survives server switches) |
| probe result post-restart (named-GO qb-restart, 18:00Z) | inbound_reachable = False (external netns-listener contact = rc 124 TIMEOUT, no bytes; netns listener confirmed bound) |
| mismatch hypothesis | the Client-Area/edge forwardingassignment for 54543 is no longer wired to the CURRENT exit/tunnel-endpoint pair (the 09-19 shadow-stack nc result = the class is healthy when the binding is fresh; the pre-restart chain proves the tunnel is fine,) |
| what the Client Area SHOULD show | 54543 present in the airvn.org/ports panel; the ACTIVE assignment mapping tunnel-endpoint == the current Pisces Entry3 endpoint (matching 38.88.124.105); no 7-day grace warning |
| what the intervention = | verify the port panel; re-assign/re-bind the forward (or open vendor support if the panel looks healthy but stays dead); ideally also re-request for IPv6, not just IPv4 |

## Script chain for the fixers

Everything is receipted in scripts/airvn_port_grab.py (RAZ-335 lane, PR a1ly404/razzorstack#576; fail-closed): after the Client-Area intervention, a human runs `python3 scripts/airvn_port_grab.py --validate` (in-container netns + external dial + inbound_reachable=True required; else exit 5). The listen program (qBittaorent) rebinding is *not* to be redone without a fresh named GO — it is already re-bound at the current exit; the missing piece is the EDGE binding, not the listener.

## Receipt state (parked)

- PR #576 (airvn typo fixed to airvn_port_grab.py) open.
- RAZ-335 (Linear 63b479a0, 99233ef7) carries the falsification + the exact starting packet for the manual lane.
- No further work before Lyla's Client-Area lane decision.

## Addendum — WireGuard pivot landscape (2026-09-29T21:2xZ, read-only)

- Fleet tunnel = WireGuard now: `wireguard` container (wg0 10.163.143.194/32, route table 51820), new config = AirVPN_CA-Vancouver_**Ginan**_UDP-1637-Entry3.conf; live egress via wg0 = **207.81.55.234** (new exit vs Pisces' 38.88.124.105).
- gluetun STILL runs + its API surface answers ({"port":0,"ports":[]}, publicip cached = 192.30.89.27): the monitoring/alert chain still reads gluetun = stale context; the bot text carries Pisces-era values (reserved 54543 at 38.88.124.105).
- qBittorrent netns = the **wireguard** container (container:ca19539d…) — listener bound wg0+eth0+localhost; `nc 127.0.0.1:54543` = SUCCESS in-netns.
- External dial to 207.81.55.234:54543 (home-WAN vantage, probe-source validated on both stacks) = rc124 TIMEOUT with WG netns-listener alive — same edge-delivery failure class as gluetun's stack: the parked Client-Area item is unchanged by the pivot, PLUS a new open check: WG container's inbound iptables/nft for 54543 (no FIREWALL_VPN_INPUT_PORTS analog present on the surface — gluetun's rule died with the stack swap).
- The load-bearing diagnostic surface moved from gluetun's API to the WG netns: `airvn_port_health` (and any monitoring) needs a WG-shape re-derivation or it keeps alerting a phantom stack.
