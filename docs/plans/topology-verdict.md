# Topology verdict

verified-as-of: 2026-09-30T19:30:00Z  
binding-config: `config/chip_topology.json` (enforced in `scripts/chip/topology.py`)

## Binding ruling (Ally) — PM hub, not “solo vs trio”

At the current flash-model tier, the **interface** is:

1. **Ally talks only to `pm`.** PM does the work for that GO (or frames it) and posts the **receipt under webhook `username=pm`** — the human does not wake specialists directly for normal GO traffic.
2. **Specialists** (`chipchatdev`, `homelabdev`, `opsdev`, `buffrdev`) are **charters + path denies** — lane limits on hands, not a second conversation and not agents chatting each other to Done.
3. **`delegate:`** only when the charter says another lane owns those files. **One hop at a time**, hard stop at **3** per human/inject chain. The specialist runs its slice, posts **its own** four-line receipt on the **same `#chip-dev` webhook** with `username=<specialist>`, then **stops**. No reply chains between agents (no PM↔specialist ping-pong, no specialist↔specialist threads).
4. **Visible room ≠ multi-agent coherence.** One shared channel with distinct webhook usernames is for **Ally visibility**; **agent↔agent chains** are what failed (RAZ-337: false completions + coherence collapse). Mothballed trio/swarm collab is the same failure class as ambient agent agreement.

**Trade-off:** A missed cross-lane handoff is cheaper than another false Done.

## Evidence on record

- Mock collab A/B grades `len(body)` and a synthetic `kill:` line. That is not quality proof (`docs/plans/collab-protocol-plan.md` P9).
- Live RAZ-335 ops A/B was a **4/4 tie**, **mothballed**, spend `$0.0062` of `$0.25` (`docs/ops/post-ab-runbook.md`).
- **RAZ-337:** three agents, one crontab line, two false completions, one coherence collapse. Anchor razzorstack PR #588 merge `11968de`.

PR merge rate, false-completion rate, wall-clock, and cost-per-task were not measured across a task set. This verdict does not invent those numbers.

## What runs in `#chip-dev` (default)

| Step | Who | What |
| --- | --- | --- |
| Inbound | Ally → **`pm`** | Speaker map / inject maps Ally to `pm`; PM is the human-facing persona. |
| Primary shot | **`pm`** | One `chip room` completion (`turn_limit` 1); PM posts receipt as **`pm`**. |
| Cross-lane bytes | **`delegate:`** (optional) | Only when another charter owns the paths. One relay follow-up per hop. |
| Specialist hop | Target specialist | One completion; receipt as **`username=<specialist>`** on the **same** room webhook URL; **no further delegate** in that hop unless charter requires another lane (counts toward cap 3). |
| Forbidden default | Trio / swarm / agent threads | Multi-persona collab boards, `collab_ab`, ops A/B trio — **mothballed** (see below). |

Relay does **not** auto-fan out to multiple personas on one GO (`relay.auto_multi_persona_fanout` is **false**).

## Delegate backstop (mechanics)

- Hop cap **3** per handoff chain (resets each human/inject turn): `scripts/chip_relay/delegate.py` (`MAX_DELEGATE_HOPS`), config `delegate.max_hops_per_chain`.
- Policy: `delegate.charter_cross_lane_only`, `delegate.no_agent_to_agent_reply_chains`, `delegate.specialist_posts_own_receipt_then_stops` in `config/chip_topology.json`.
- Receipt gate still applies per outbound post (`docs/CHIP_RELAY.md`).

## Mothballed (not runnable without explicit future Ally GO)

While `swarm.mothballed` is true, runtime refuses:

| Path | Module / entry |
| --- | --- |
| Multi-persona collab board | `chip.collab.begin_brainstorm` with ≥ 2 participants |
| Offline trio A/B harness | `chip.collab_ab.run_ab` |
| RAZ-335 ops trio / both arms | `python3 -m chip.ops_session_a_b --arm trio` or `--arm both` |

**Unlock (tests / future Ally GO only):** `CHIP_TOPOLOGY_SWARM_GO=1` plus existing GO ticket law. Not production default.

## Explicitly not built / not reversed here

- Disabling the hop cap or raising it above 3.
- A new A/B harness or trio-as-default relay behavior.
- Turning `delegate:` off in this slice.
- Receipt gate default-off flip — still waits until receipt gate + tool-receipt rule are merged and soaked.

## Related docs

- `#chip-dev` law: `config/chip_chat/channels/dev.md`, `config/chip_chat/channels/pm.md`
- Operator manual: `AGENTS.md`, `docs/CHIP_CHAT.md`, `docs/CHIP_RELAY.md`
- Post–RAZ-335 mothball: `docs/ops/post-ab-runbook.md`
- Collab protocol (historical): `docs/plans/collab-protocol-plan.md`
