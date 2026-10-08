# Envelopes (task contracts)

Envelopes are structured task posts: scope, acceptance, evidence type, owner persona, and hop budget. PM and specialists use them to avoid bare “please fix” handoffs.

## Channels

- **Dev room (`#dev`):** primary agent/dev traffic, delegate handoffs, GO receipts.
- **Monitoring channel (`#monitoring`):** tell-tier health lines from the optional monitor feed (verify-twice + cooldown; no agent turns from the feed alone).

## Claims and steals

- Scoped claims name paths or bounded artifacts — no ticket-wide wildcards.
- Only the operator (or explicit GO) may execute destructive steals; automation may draft envelopes tagged for operator review.

## Graduation

Runbook ids graduate only after operator acknowledgment and clean history; unknown patterns stay fail-closed until evidenced.
