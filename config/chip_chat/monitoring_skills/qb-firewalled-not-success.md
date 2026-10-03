# qb-firewalled-not-success

When qBittorrent reports `firewalled` (or homelab feed key `qb-connection`), the listener is **not** healthy.

Law:
- Never mark the probe green while `firewalled` is true.
- Do not restart VPN containers (wireguard/gluetun) from Chip Chat; triage only.
- Escalate with a propose receipt — homelab lane owns port-forward / edge fixes.
