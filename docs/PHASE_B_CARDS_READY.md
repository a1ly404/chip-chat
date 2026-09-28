# PHASE_B_CARDS_READY — cards v1 (status change per Ally churn-GO, 2026-09-28)

> **Status movement, paper-trailed:** Ally churn-GO 2026-09-28 ("Phase B cards
> — GO, draft, propose, and enact; Phase A clock still stands"). Original
> constraint text (below) remains true for content: Phase B START still gates
> on the resume bar (razzorstack monitoring-api fix landed + 4h clean window
> + ideally one observation day). What changes: cards are now READY +
> PROPOSED (RAZ-16 one-fact envelope per card), not draft-hidden.
> Cards themselves still never self-post to Discord — proposal receipts live
> on RAZ-16 (the lane's proposal surface); Discord-visible posting reaches
> Ally through the SITREP.

## State summary (verified-as-of 2026-09-28 ~10:15 PT)

| item | state | receipt |
| --- | --- | --- |
| Card 1 (discord-bot-loop-restart) | READY cards v1, registry row exists `propose` | `config/chip_chat/runbooks.json` + this doc |
| Card 2 (monitoring-api-internal-loop) | READY, **HARD blocked-until razzorstack-side fix lands** | clause in Card 2 AC |
| Executor slide (single-service invariant) | intact unchanged | `SERVICE_ALLOWLIST` + `BLOCKED_TARGET_PATTERNS` walls in authorize.py (names only) |
| Standup emitter cron | registered + self-verified | shell receipt in SITREP; expected rc=3 until producers emit receipts |
| Watchdog baseline cron | registered + self-verified rc=2 | logs preserved in `~/appdata/chip-chat/cron/` (cron-write appends) |
| Slice B bodies (5 skills) | drafted under `docs/skill-bodies/` | move manifest below; final move executes on the razzorstack Windows SoT by the owning lane |
| monitoring-api fix | diagnosed read-only + one-shot plan parked | docs/PARKED_FINDINGS.md row this slice |
| Strict flip | NOT flipped — gated at Phase B completion per Ally's own three-phase sequence (this GO conditions it on Phase B, and Phase B has not completed) | n/a — standing |

## Card 1 — `discord-bot-loop-restart` (propose; first graduation candidate)

```
propose: chipchatdev discord-bot-loop-restart — AC:
- [ ] feed shows discord-bot fingerprint (loops_ok=false or /health down) as
      actionable across TWO consecutive ticks (machine-checkable, verify-twice law)
      verified-as-of: <dd-mm HH:MM> — <latest.json receipt path>
- [ ] runbook text reviewed: restart ONE unit (discord-bot) — not compose up
      verified-as-of: <...> — <registry class line>
- [ ] executor dry-run refused everything except the named service
      (SERVICE_ALLOWLIST {discord-bot, monitoring-api}; blocked patterns intact)
      verified-as-of: <...> — <fake probe receipts from test suite>
- [ ] rollback: `docker compose up -d --no-deps discord-bot` — single command,
      named target, idempotent
      verified-as-of: <...> — <command receipt>
- [ ] C cycles completed by human press-button before any auto graduation
      verified-as-of: <...> — <3 clean cycle receipts>
- [ ] no other unit touched (single-service invariant: executor refusing any
      second target in the same window is itself the receipt)
      verified-as-of: <...> — <authorize refusal receipt>
- [ ] card self-post forbidden: cards reach Ally BEFORE posting (PR 55's lane
      standing: silent self-amend can never apply; golden-item 13)
      verified-as-of: n/a-by-design — gate lives on Ally's read, not on a stamp
```
Runbook (registry `propose`): `discord-bot-loop-restart` = `docker compose up -d --no-deps discord-bot`
from the razzorstack compose dir ONLY; timeout bound; no other units touched; NAS/plex
classes remain dark-forever.

Proposal gates (law, per id:auto-heal):
- Ally GO required for EVERY execution until Phase D graduation: a standing
  churn-GO does NOT dissolve the per-execution asks (Phase C is human-press
  by design); this line is verified on every receipt.
- verify-twice on any claim of success (probe receipt post-action + healthy spans).
- 01:00–05:00 mutate band: refused.

## Card 2 — `monitoring-api-internal-loop` (propose; new id; HARD blocked-until)

```
propose: chipchatdev monitoring-api-internal-loop — AC:
- [ ] detection wired: health JSON green AND monitoring-api log window shows
      error-class loop (stale-image ref pattern from sha256:8e5db… incident)
      verified-as-of: <...> — <log grep receipt + module alert row>
- [ ] skill body drop-in (Slice B): razzorstack-side file at pinned SHA, receipt
      schema == docs/skill-templates/internal-loop-degrade.template.md
      verified-as-of: <...> — <linter run + template guard tests>
- [ ] rollback path: re-tag/restart monitoring-api (homelab lane executes)
      verified-as-of: <...> — <one-shot cmd receipt>
- [ ] HARD blocked-until clause: this card stays receipt-frozen unless BOTH
      the razzorstack-side fix lands AND the gate resumes (lane dependency is
      a gate, not a note); card self-post forbidden
      verified-as-of: n/a-by-design — see blocked-until semantics
```
NOTE: this card's fix belongs to the razzorstack lane (2026-09-25 incident root cause);
one-shot fix plan is parked with receipts in this slice. When that lane lands the fix,
the blocked-until clause can be CLEARED-BY-TIMELINE (receipt cross-reference), then the
Phase B gate (clock + 4h clean) counts forward — day counts do NOT backfill.

## Enactment mechanics (what "enact" means under law)

1. Cards proposed on RAZ-16 (one fact per card, envelope format) — DONE this slice.
2. Resume bar per ENVELOPES: fix lands → 4h clean cooldown → (stretch) observation day →
   Phase B begin = first receipt-based quiet day count, never calendar.
3. Executor stays OFF (`CHIP_TOOL_AUTO` default false check; tool allowlist narrow).
4. Strict flip: only at Phase B completion (three-phase soak-hardening sequence), which
   requires evidence receipts — supervised toggle ACCORDING to the card, never a vibe.
5. Phase C: human-press-button; chip lane archives receipts only.
