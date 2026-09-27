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
```
NOTE: this card needs the RAZOR-STACK fix (its own lane) BEFORE graduating — the
2026-09-25 incident root cause lives on their side. Blanks deliberately unfilled
until the gate resumes; no quiet-day evidence exists tonight.

## What fills the blanks
- Time budget: T plus 4h cooldown window (gate resume bar) → then observation day.
- Fixer wake payload builder + claims/authorize wiring would need a further slice;
  these cards assume no SERVICE mutation from chip lane — execute tier stays
  human-press (Phase C), receipts archived by chip tools.
