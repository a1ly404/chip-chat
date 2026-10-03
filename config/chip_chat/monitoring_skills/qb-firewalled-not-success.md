# qBittorrent firewalled — WireGuard egress fault

`connection_status=firewalled` means the **WireGuard egress path** is wrong (port-forward / edge), not a successful probe.

Law:
- Firewalled is always a **fault** (homelab feed already flags it).
- Do not restart gluetun (no such container) or qbittorrent from chip-chat.
- After soak: homelab lane fixes edge forward / listener on the **wireguard** stack.
