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

## handoff (transfer)
```
handoff: <persona> <task> — confirmed by: <persona>
```
- Never silent abandon. Receiver confirms; then (and only then) ownership moves.

## Spec-first edge cases (Grok round 4 — these bite before anything else)

### 1. Stale-claim break (expiry / steal)
- Heartbeat interval: **TODO(Ally)** — the number that defines "miss".
- What counts as a ping: **TODO(Ally)** — @mention in-room? inject? DM?
- Who may steal: Lyla (automation) / PM / Ally — **TODO(Ally)** default: PM only with Ally paper trail.
- Does park pause the heartbeat? **TODO(Ally)** — Grok's default: no (a parked claim still ages).
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
