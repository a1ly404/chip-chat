# ENVELOPES — machine-checkable message shapes (Grok-harvest port, 2026-09-26)

Law for every persona: use these fixed shapes instead of inventing process.
Cheap models obey short machine-checkable contracts; they do not invent process well.
Enforcement: discipline + `chip.claims` parser (+ later relay gates). Done discipline:
green CI + per-acceptance-box `verified-as-of` — `claim done` ≠ Done, `log-only` ≠ Done.

## claim (ownership)
```
claim: <persona> <scope> — AC: <acceptance criteria>
```
- FIRST claim on a scope owns it (transcript order = created order). Later claims are duplicates.
- Unlock: `claim done` by owner · confirmed `handoff:` · steal (heartbeat miss AND ping-no-reply).
- One live claim per workstream. Grep transcript (or `chip.claims.scan_claims`) before claiming.

## parked (frozen work)
```
parked: <why> — evidence: <link/probe> — next: <revive condition>
```
- Env broken / waiting on Ally / unpaid smoke → park, do not thrash.
- Dig-only stays dig-only after park; mutate needs a fresh Ally GO.

## learning (knowledge write, NOT Done)
```
learning: <one-line fact>
class: script|GHA|cheap-agent|expensive-agent|wall|refuse
when: <YYYY-MM-DD ~HH:MM PT>
evidence: <PR# / run / probe / path — machine-checkable>
next: <none | promote-to-script | promote-to-GHA | update-skill:<id> | Ally-GO-needed>
```
- One fact per comment. Redact secrets. Repeated learning → promote (script/GHA/skill), don't leave as tribal chat.

## verified-as-of (evidence binding)
```
verified-as-of: <YYYY-MM-DD HH:MM> — <machine-checkable proof>
```
- Bound per acceptance checkbox. Older than the last change window = stale, not fact.
- Done requires green CI + verified-as-of on EVERY box; missing one → reopen.

## Fixer wake payload (Grok round 6, Q21 — the MINIMUM a self-heal fixer gets)

1. Alert fingerprint + probe receipt JSON (machine-checkable fail)
2. Matched runbook id text (steps, rollback, verify)
3. Live `claim:` / path-lock / SKIP_KEYS excerpt
4. Latest 1–2 `learning:` lines for that fingerprint
5. Thrift class + stand-down flag

**Leave out:** Discord scrollback, old sitreps, unrelated RAZ dumps, guessed root cause. Context owed beyond this: port/process map + which profile is live (native vs docker).

## Runbook registry (the graduation gate — `config/chip_chat/runbooks.json`)

- Registry maps runbook id → class: `auto` | `propose` | `blocked`.
- `auto` = idempotent, named target, one-command rollback (restart-named-service, clear-stale-suppress, re-run-health-probe, post-learning).
- `blocked` = never autonomous (remux, plex-restart, credential-flip, media-delete — incident classes).
- **Unknown ids fail-closed to `propose`.** Graduation: propose → (Ally GO + clean history) → auto, one runbook at a time.
- `authorize(fixer, fingerprint, "fix")` + `runbooks.may_autorun(id)` are the two gates before any live recover; probe receipt + verify-twice still required (law id:auto-heal).
- **Wired 2026-09-26** (`chip.tool.authorize_execute`): every executor spawn passes `authorize`; persona runs (`speaker`+`room`+`scope`) need a live claim, human CLI rides the `is_human_or_inject` hot path, `CHIP_TOOL_STRICT=1` fails closed for anonymous executes. Refusals audit as `authz_refused` in `tool_invocations.jsonl`.

## Go-live phases (Grok round 7 — the rollout law)

| Phase | Stages | Mode | Graduate when |
| --- | --- | --- | --- |
| A | ingest → noise floor → fingerprint → ticket/learning draft | **dark** (log-only) | ≥3 quiet days of sane skip/dedup/flap rates on the real feed |
| B | + triage class + runbook id + propose card | **propose-only** | Ally acks each distinct runbook ≥1×; no false storms |
| C | + claim + verify receipts on human-approved runs | human presses execute | 3 clean propose→GO→verify cycles, same runbook id |
| D | graduate ONE runbook to `auto` | auto, that id only | 3× would-have-matched Ally GO + 0 sideways + verify×2 + learning posted |

Dark forever: remux / media / credentials / execute walls / mutate-band L2. Ally ack for EVERY new runbook id — graduation is evidence-based, never calendar-based.
First signal: monitoring-api structured health for `discord-bot` (`/health` / `loops_ok`). First auto candidate: `discord-bot-loop-restart` (currently `propose` in the registry until Phase C evidence).

### Phase A feed — WIRED 2026-09-26 (`scripts/chip_relay/monitor_feed.py`)

- In-container poller (relay gateway thread, `CHIP_FEED_ENABLED=1` in compose): GETs `http://host.docker.internal:48080/health` (discord-bot) + `:48082/health` (monitoring-api) every `CHIP_FEED_INTERVAL_S` (default 300s, min 60).
- Derives alerts → `chip.monitor` noise floor (dedup 30m / flap ≥4/h / dead-letter / skip-keys) → persist state in `CHIP_CHAT_DATA_DIR/monitor_state.json` + latest receipt in `CHIP_CHAT_DATA_DIR/monitoring/latest.json`.
- **Tell-tier (health):** actionable/dead-letter health faults may post one line to `#chip-dev` (`CHIP_WEBHOOK_DEV`) with verify-twice + 4h cooldown — no agent turns, no tool runs from the feed.
- **P2 homelab slice:** read-only wg/downloads/qBit probes (`CHIP_FEED_HOMELAB=1`); Discord wake for those faults requires **`CHIP_FEED_HOMELAB_WAKE=1` (Ally GO)**. Details: `docs/CHIP_MONITOR_P2.md`.
- Env: `CHIP_FEED_TARGETS` (add `name=port` pairs), `CHIP_FEED_HOST` (default `host.docker.internal`).

## handoff (transfer)
```
handoff: <persona> <task> — confirmed by: <persona>
```
- Never silent abandon. Receiver confirms; then (and only then) ownership moves.

## Spec-first edge cases (Grok round 4 — these bite before anything else)

### 1. Stale-claim break (expiry / steal) — parameters **ACKED by Ally 2026-09-27**; HARD rule unchanged
- Heartbeat interval: **ACK** — a claim "misses" after **90 min** with no owner-activity evidence in the room transcript for that scope (any owner turn touching the scope, a progress envelope, or a bare `touch: <scope>` line). 90 min = 3× the relay watchdog heartbeat (30 min) — one real miss tolerates two false-absent ticks.
- What counts as a ping: **ACK** — an in-room **@mention of the owner persona that names the claim scope** (tick-scope keyword or the scope string itself). inject lines count (visible lane). **DMs do NOT count** (no-reading-private law). A ping valid for the steal gate must land **after** the heartbeat miss begins; a ping before the miss proves nothing about post-miss responsiveness.
- Who may steal: **ACK (Grok default)** — PM only, with a paper-trail envelope:
  ```
  reclaim: <persona> <scope> — reason: heartbeat-miss <ts> + ping-no-reply <receipt link>
  ```
  Lyla/automation may *draft* the reclaim card; only PM (or another persona on explicit Ally GO in-line) may post it. Ally always may steal directly.
  **PM-outage amendment (ACK 2026-09-27):** if the dead agent IS the PM and the PM claim exceeds the miss threshold, any observer drafts the steal envelope to #chip-dev **tagged for Ally — do not self-post**. Converts a PM outage from a silent stall into a visible queued action. This path never grants steal authority — `id:claim-unlock` stays out of its reach; only Ally executes it.
- Does park pause the heartbeat? **ACK — NO**: a parked claim still ages. `parked:` is an evidence state, not a liveness shield; parked claims decay visibly, never zombie.
- **Phase C strict-flip parking note (Ally NO-GO 2026-09-27):** `CHIP_TOOL_STRICT=1` stays OFF until the Phase B propose-gate passes; sequence is A soak → B propose cards → strict flip, each with its own GO. Rationale: strict authorize changes agent failure behavior — a Phase C regression must not hide in transition noise.
- Steal rule (HARD law `id:claim-unlock`): steal requires heartbeat miss AND ping-no-reply.

### 2. Scope / path granularity
- One live claim ↔ **one canonical path** (or an explicit `scope:` list) — nothing broader.
- **Forbid ticket-wide claims** ("I claimed RAZ-X ⇒ whole library") — scope must name paths or a bounded artifact list.
- Defer: multi-scope claims, A→B→C handoff chains (each hop needs confirmed `handoff:`; no transitive ownership).

## New-lane onboarding checklist (Grok round 4 — before a persona's first task)

1. **Charter** — owns / never-touches / mutate class (dig-only vs remux vs code).
2. **Room seat** — which room(s); IDLE-unless-@mention default under thrift.
3. **Claim obedience** — AGENT_CLAIM; the agent-id string used in `claim:`.
4. **Triage gate** — linked triage skill (or "PM triages; lane refuses without `triage: actionable`").
5. **Thrift class** — default cheap-agent; escalate only on GO/Urgent.
6. **Channels** — where standups/incidents go (#monitoring-logs vs #plex-fix pattern).
7. **Done bar** — green CI + per-checkbox verified-as-of; `claim done` ≠ Done.
8. **Kill/walls** — remux kill, Ally-only flips, path flock if media.
9. **Smoke** — one dry-run ticket: triage → claim → refuse-without-evidence → park comment.

**Cheap-model differences:** law-md as fixed envelopes only (no judgment fields); allowlisted tools (pytest, git status, maybe git diff); refuse free dig; wake payload = ticket + AC + latest learning/triage only; no durable "vibes" memory writes without a `learning:` shape.

## Mandatory retro (3rd premature Done — Grok round 4)

**Trigger:** third premature Done in the watch window, or second on the same acceptance cluster.
**Anatomy (≤1 page):** Facts (ticket IDs, who flipped Done, timestamps, missing verified-as-of boxes) → Blast radius (what Ally/bots believed finished that wasn't) → Root cause (auto-Done on merge? claim done confused with Linear Done? absent checklist?) → Rule change (ONE concrete AGENT_CLAIM/skill/GHA gate edit — not vibes) → Reopen list (every ticket reopened + unpaid verification owners).
**Attends:** PM + Ally (Lyla if media acceptance). Lanes that flipped Done comment evidence only — not a debate club.
**Lands on:** the program ticket + board thrift ticket as `learning:`; AGENT_CLAIM patch if the rule changed. Never only in chat.
