# PLEX_FIX_SKILL — W-006 law-window executor (Plex-external, 24h, self-disarming)

## Authority and expiry

- **Authorized:** Ally GO 2026-10-04 **21:39 PT** (watch log `W-006`, watch at
  `gate_watch_2026-10-05`).
- **What it authorizes:** a RAZ-63 mutability exception, **Plex-external-only**:
  chip may run ONE dedicated skill that attempts to FIX the native
  Plex-on-Windows outage class — `plex.razzormail.com` returning **530** /
  `/identity` failures as seen in the monitoring feed.
- **Expiry:** `config/chip_relay/plex_fix.json` → `expires_iso`
  `2026-10-06T04:45:00Z` (= 2026-10-05 **21:45 PT**). The skill checks this
  timestamp **before any action** on every invocation; after expiry it writes a
  disarm flag and posts **one** `[plex-fix] DISARMED` notice to `#chip-dev`,
  then never acts again. An absent/unparsable expiry fails closed (treated as
  already expired).

## Scope walls (enforced in code, `scripts/chip_relay/plex_fix_skill.py`)

1. **Trigger:** only `plex-external*` keys whose detail carries
   `530 | identity | health-down`. Every other case class keeps the shadow law
   (`alert_ingest` route table otherwise unchanged).
2. **Actions (escalation order):** `docker restart` of the
   `service_allowlist` containers ONLY — `caddy`, `cloudflared` (the
   plex.razzormail.com origin chain). No other containers, volumes, networks,
   or host services.
3. **Native Plex Windows service restart: SKIPPED.** The relay stack has no
   safe host-execution path (the relay holds a docker proxy socket only;
   `homelab-executor` is an image, not a wired runner). The skill says so in
   every tell and records the limitation as a learning. If a safe host-exec
   surface ships later, this skill gains `(b)` only via a new PR.
4. **Caps:** one fix attempt per incident fingerprint; hard cap
   `max_attempts_24h` = **3** per rolling 24h. Cap-reached and repeat-skipped
   decisions are receipted, never silent.
5. **Receipts:** every decision (fix-attempted / fix-failed / disarmed /
   cap-reached / incident-repeat-skipped / disabled / no-allowlist) appends a
   non-shadow row to the alert-dispatch ledger (`chip_state/alert_dispatch.jsonl`)
   — the field the watch grades — plus one learning reflection.
6. **Learnings journal:** append-only JSONL at
   `<data>/skills/plex-fix/learnings.jsonl`, entries
   `{timestamp, situation, diagnosis, action, outcome, lesson}`. Every run
   tail-loads the last 20 entries before deciding; the latest loaded lesson is
   echoed into the dispatch row and the tell, so "the skill got smarter" is
   observable, not asserted.
7. **Tell:** one `[plex-fix]` line per fix attempt to `#chip-dev` via the
   existing webhook post law (no new webhooks). Tell failure never blocks the
   receipt row.

## Env overrides (ops/tests only; the JSON file is the SoT)

- `CHIP_PLEX_FIX_EXPIRES_ISO` — overrides `expires_iso`.
- `CHIP_PLEX_FIX_MAX_ATTEMPTS` — overrides the attempt cap (integer).

## Restore path after the window

Delete nothing; the skill disarms itself. To re-authorize, human updates
`expires_iso` in `config/chip_relay/plex_fix.json` via a new PR citing a new
GO. The learnings journal persists across windows on purpose.