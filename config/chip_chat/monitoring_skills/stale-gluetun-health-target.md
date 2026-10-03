# stale-gluetun-health-target

Live homelab VPN is **wireguard** + **qbittorrent**. Health monitors that still probe **gluetun** are stale.

Law:
- HTTP 404 / "No such container" for **gluetun** means **update or drop the probe target** — never recreate or restart gluetun from Chip Chat.
- Do not restart **wireguard**, **qbittorrent**, or any VPN container from this skill path (read-only triage only).
- qBittorrent `firewalled` / `connection_status=firewalled` is **not** a successful probe; treat as a separate fault class (`qb-firewalled-not-success`).

Receipt shape: four-line blocked triage with evidence `tool:monitoring-skill.stale-gluetun-health-target`.
