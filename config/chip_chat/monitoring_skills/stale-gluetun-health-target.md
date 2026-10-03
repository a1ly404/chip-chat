# Stale VPN probe labels (Gluetun / AirVPN / “VPN (Gluetun)”)

**There is no Gluetun container.** Live VPN is **WireGuard** (`wireguard` container, `wg0`).

Any alert that still names **gluetun**, **AirVPN port-health**, or **VPN (Gluetun)** is a **stale probe label** — not a service-down event.

Law:
- `fix_action`: `record_retired_target` for `gluetun` in chip_state only.
- Never recreate, restart, or health-check a container named gluetun from Chip Chat.
- Point operators at **WireGuard** probes (`wg-show`, handshake stale) and homelab monitor config updates.
