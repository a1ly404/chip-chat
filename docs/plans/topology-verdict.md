# Topology verdict

verified-as-of: 2026-09-30T19:30:00Z

## Recommendation

At the current flash-model tier, one persona per Ally GO plus a receipt beats agent-to-agent chatter. Specialist lanes stay as charters and path denies, not as a conversation.

Evidence on record:

- Mock collab A/B grades `len(body)` and a synthetic `kill:` line. That is not quality proof (`docs/plans/collab-protocol-plan.md` P9).
- Live RAZ-335 ops A/B was a 4/4 tie, mothballed, spend `$0.0062` of `$0.25` (`docs/ops/post-ab-runbook.md`).
- RAZ-337: three agents, one crontab line, two false completions, one coherence collapse. Anchor razzorstack PR #588 merge `11968de`.

PR merge rate, false-completion rate, wall-clock, and cost-per-task were not measured across a task set. This verdict does not invent those numbers.

Delegate stays available as a backstop with hop cap 3. Default-off for `#chip-dev` waits until the receipt gate and the tool-receipt rule are merged and soaked. If the gate fails during soak, the topology flip stays parked.

## Trade-off

Cross-lane work needs a human or a single explicit handoff, not a room of specialists agreeing. Missed collaboration is cheaper than another false done.

## Explicitly not built

- Disabling the hop cap.
- A new A/B harness.
- Turning delegate off in this document’s commit. The flip is a later phase.
