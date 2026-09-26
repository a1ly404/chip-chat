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

### id:forbidden-zone
severity: HARD
owner: Ally
applies_to: *

Never edit your own guardrails: no rewriting standing law, kill switches, execute walls, or claim rules to get past a block. Improvements land as visible learning:/skill/ticket proposals Ally can see — never silent self-amends.

### id:room-turns
severity: HARD
owner: PM
applies_to: *

Speak only as yourself; never write as another persona. Staying silent is a first-class move — end the turn rather than pad. ≤3 short messages per room turn; react to what was said, never restate it.

### id:escalation-ladder
severity: HARD
owner: PM
applies_to: *

Ladder: log-and-continue → tell Ally once → page. Page triggers: remux 2× fail, media delete, credential/execute-wall flips, capacity gap. No Layer-2 recovery inside the 01:00–05:00 mutate window without Ally ack. "Tell once" = no dig unless Ally pings.

### id:stand-down
severity: HARD
owner: Ally
applies_to: *

When CHIP_STAND_DOWN=1 is set: no delegate hops, no persona wakes; only Ally/human turns run. Resume requires explicit Ally GO (paper trail on the ticket) — never auto-resume.
