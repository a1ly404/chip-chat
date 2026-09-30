# Receipt readability gate

verified-as-of: 2026-09-30T19:30:00Z

## Recommendation

Enforce a four-line sitrep at the relay post boundary (`gate_channel_body` immediately before each agent `webhook(...)` call in `scripts/chip_relay/dispatch.py`). The prompt may request the shape. The webhook is the control.

Required lines:

- `claim:` one sentence
- `status:` `done` | `blocked` | `refused`
- `evidence:` `tool:<id>` or `sha:<hex>` or a GitHub pull URL or `RAZ-<id>` or `none`
- `next:` one action or `none`

`status: done` with `evidence: none` is rejected. A body that is mostly an unlabeled pipe table and lacks the four fields is rejected. Empty or unknown status is rejected. The channel receives `[chip-relay] ERROR: receipt schema: <reason>` instead of the model text. The raw reply stays in the chip transcript.

## Trade-off

A true sitrep that omits a field shows the ERROR in `#chip-dev` until the persona prompt matches the schema. That is accepted. Humans are not the validator.

## Explicitly not built

- A second model that rewrites jargon into prose.
- Gating `#monitoring-logs` monitor-feed lines (those are already structured alerts, not persona sitreps).
- Scoring “readability” with another LLM.
