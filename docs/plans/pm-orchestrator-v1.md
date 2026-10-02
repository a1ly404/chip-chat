# PM orchestrator v1

verified-as-of: 2026-10-02  
binding-config: `config/chip_topology.json` (`pm_orchestrator` block)  
topology law: `docs/plans/topology-verdict.md`

## Problem

Ally should talk only to **pm**, but a single GO often needs a specialist charter hop. Today relay can run **one** delegate follow-up per inbound turn; the specialist posts and **stops**. Ally still had to midwife “are we done?” unless they watched the channel.

We want **Grok Bot–like progress** (pm plans → specialist receipt → pm closes) **without** swarm/trio, agent↔agent reply chains, or raising `MAX_DELEGATE_HOPS`.

## Current vs proposed (pm_hub + orchestrator tick)

| Aspect | Before | pm-orchestrator-v1 |
| --- | --- | --- |
| Human entry | Ally → pm | Unchanged |
| Cross-lane work | `delegate:` one hop per dispatch | Unchanged (cap 3) |
| After specialist receipt | Stops | Optional **one pm orchestrator tick** (flag **default off**) |
| Job tracking | Transcript only | `jobs/<room>/<id>.json` + one-line `pm_job` mirror in `threads/<room>.jsonl` |
| Swarm / collab | Mothballed | Still mothballed |
| `CHIP_RELAY_AUTO` | false | false |

Orchestrator tick is **not** a delegate hop and **not** swarm unlock. Enable with `CHIP_RELAY_PM_ORCHESTRATOR=1` (or future config default change with explicit Ally GO).

## Job board schema

File: `$CHIP_CHAT_DATA_DIR/jobs/<room>/<id>.json`

```json
{
  "schema": 1,
  "id": "abc123",
  "room": "dev",
  "owner_persona": "pm",
  "goal": "...",
  "acceptance": "...",
  "stop": "...",
  "artifacts": [],
  "status": "open|done|blocked|escalated",
  "created_at": "...",
  "updated_at": "..."
}
```

CLI (mock-friendly):

```bash
CHIP_CHAT_MOCK=1 ./bin/chip job create dev --goal "..." --acceptance "..." --stop "..."
./bin/chip job list dev
./bin/chip job show dev <id>
```

Pm may close a job in an orchestrator receipt with `job:done <id>`.

## Orchestrator tick

After a **specialist** delegate receipt is posted, relay may run **one** extra `chip room` as **pm** with an internal prompt prefixed `orchestrator-tick:`. Pm posts a normal four-line receipt (done / blocked / escalate). Relay does **not** parse nested `delegate:` from that tick in the same dispatch.

Metrics (log-only): `data_dir/audit/orchestrator_metrics.jsonl` — `orchestrator_tick`, `hops_per_go`, `usd_per_done`, `false_done_suspect`.

## Escalate Ally (documented; minimal wire)

Escalate to Ally when: spend WARN/hard (existing gates), repeated chip/webhook failure (2×), missing GO for cross-lane work, or irreversible/cross-repo actions. Full auto-escalate posting is backlog; v1 relies on pm `status: blocked` receipts.

## Kill criteria (stop and revert)

- Any production default-on orchestrator tick without soak.
- Orchestrator tick chains specialist↔specialist or parses delegate from specialist follow-ups in the same dispatch.
- Swarm/trio unlocked as a dependency for “feel”.
- False Done rate up vs baseline (watch `false_done_suspect` + RAZ-16 learnings).
- `chip_calls` per Ally GO routinely **>4** (pm + specialist + pm tick + margin) without bounded acceptance.

## Out of scope (this slice)

- Persona `learnings.md` runtime load (stub backlog).
- New high-risk tools (Linear comment / worktree PR) — document `CHIP_TOOL_AUTO` only unless a follow-up widens `chip_tools.json` safely.
- Specialist↔specialist delegate chains or `MAX_DELEGATE_HOPS > 3`.

## Mock proof (inject)

```bash
export CHIP_WEBHOOK_DEV=https://example.invalid/dev
export CHIP_RELAY_PM_ORCHESTRATOR=1
CHIP_CHAT_MOCK=1 ./bin/chip job create dev --goal "orchestrator mock" --acceptance "pm done" --stop "inject" --job-id mock01
CHIP_CHAT_MOCK=1 ./bin/chip-relay inject --as-ally -m "GO: pm delegate chipchatdev hop-ok-chipchatdev" --mock
# expect chip_calls=3 delegate_hops=1 orchestrator_ticks=1 on stdout
```
