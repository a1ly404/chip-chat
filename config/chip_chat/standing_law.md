# Standing law bundle (append-only on volume; seeded once)

### id:op-law-1
severity: HARD
owner: Ally
applies_to: *

Load HARD law and HARD constraints before every action; if a proposed action conflicts, output BLOCKED and stop.

### id:op-law-13-pm
severity: HARD
owner: Ally
applies_to: room:pm, persona:pm

Treat remux and media-byte mutation as Homelab-dev-owned; do not remux, delete library files, or bypass remux-kill from PM lane.

### id:op-law-13-dev
severity: HARD
owner: Ally
applies_to: room:dev, persona:dev

Do not delete library media files without explicit Ally GO naming the path.
