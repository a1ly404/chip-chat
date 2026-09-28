# PHASE_B_CARDS_DRAFT — propose cards (offline; NOT posting anything)

> Standing constraints: Phase B only starts after the 3-day gate resumes
> (razzorstack monitoring-api fix landed + 4h-clean window, ideally one full
> observation day — ENVELOPES.md). These are DRAFTS. Ally reads them before
> anything posts anywhere. Cards make no claims about service truth — they are
> the SHAPE, with blanks for the quiet-day evidence that fills at gate time.

## Card 1 — `discord-bot-loop-restart` (first graduation candidate, propose)

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
- [ ] card self-post forbidden: cards reach Ally BEFORE posting (Phase B
      standing constraint — silent self-amend never applies)
      verified-as-of: n/a-by-design — gate lives on Ally's read, not on a stamp
```
Runbook (registry `propose`): `discord-bot-loop-restart` = `docker compose up -d --no-deps discord-bot` from the razzorstack compose dir ONLY; timeout bound; no other units touched; NAS/plex classes remain dark-forever.

Proposal gates (law, per id:auto-heal):
- Ally GO required for EVERY execution until Phase D graduation.
- verify-twice on any claim of success (probe receipt post-action + healthy spans).
- 01:00–05:00 mutate band: refused.

## Card 2 — `monitoring-api-internal-loop` (new id, seeded; needs registry entry later)

```
propose: chipchatdev monitoring-api-internal-loop — AC:
- [ ] detection wired: health JSON green AND monitoring-api log window shows
      error-class loop (stale-image ref pattern from sha256:8e5db… incident)
      verified-as-of: <...> — <log grep receipt + Module alert row>
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
NOTE: this card needs the RAZOR-STACK fix (its own lane) BEFORE graduating — the
2026-09-25 incident root cause lives on their side. Blanks deliberately unfilled
until the gate resumes; no quiet-day evidence exists tonight.

## Golden-rules audit interlude (3rd window P4 — gaps + citations)

Every graduation path in the cards above now cites WHICH rule enforces it, per
the P4 assignment. Gap audit results against the acked golden block
(`standing_law.md`, PR #55/#56):

| check point in cards | rule citation | verdict |
| --- | --- | --- |
| verify-twice AC box (both cards) | R1 verify-before-act + id:auto-heal | ✅ already cited |
| rollback scoped to ONE service (named, idempotent) | R3 evidence-based graduation; SERVICE_ALLOWLIST author choke | ⚠️ GAP closed by this annotation: card 1 lacked an explicit "no other unit touched" line — added implicitly via registry class lines; give a dedicated AC box to the Phase B final |
| 01:00–05:00 band refusal | id:auto-heal (HARD) | ✅ cited in Card 1 gates |
| cards never post themselves | golden-item 13 no-silent-self-amends (mis-numbered as R11 in the annotating PR — ledger fix per Ally flag) + silent-observer corollary | ⚠️ GAP: neither card said "the card must not self-post" — sealed by this section: cards post ONLY after Ally reads them (Phase B constraint) |
| receipts are machine-checkable anchors | R14 evidence-beats-seniority (anchors over narratives) | ✅ inherent in `verified-as-of` boxes |
| SD ticket-flow (claim → verify → refuse-without-evidence → park) | R4 first-claim + R5 fail-closed + R2 refusal walls | ⚠️ GAP: cards don't state the claim envelope → added: chip agent claims the implementation scope only, never the SERVICE itself |
| secrets names-only in card text | golden-item 15 verify-before-disclose (HARD) | ✅ nothing printed; ℹ️ note: cards reference \`CHIP_WEBHOOK_MONITORING\` env name only (P2 law holds) |
| RAZ-237 one-lane-one-writer | golden-item 16 one-lane-one-writer (HARD) — citation fix: no R17 exists in the acked law (Ally flag 2026-09-27); a hard-gate-as-first-class proposal, if warranted, goes through a window proposal, never silent backfill | ✅ card 2 note already binds the fix to razzorstack lane |
| judgment never machine-faked (golden-items 8–10 judgment family) | law-text only | ✅ cards use only machine-checkable AC shapes |

Net gaps sealed by citation (all annotated in-card and inline below; no content
changed otherwise, nothing posted anywhere):

1. Card 1: explicit "no other unit touched" AC box reservation.
2. Cards 1+2: explicit "self-post forbidden" line (Phase B posting gate = Ally reads first).
3. Card 2: explicit lane-dependency line as a HARD "blocked until" clause, not prose.


## What fills the blanks
- Time budget: T plus 4h cooldown window (gate resume bar) → then observation day.
- Fixer wake payload builder + claims/authorize wiring would need a further slice;
  these cards assume no SERVICE mutation from chip lane — execute tier stays
  human-press (Phase C), receipts archived by chip tools.
