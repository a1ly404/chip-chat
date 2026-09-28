# SKILL TEMPLATE — gold-signal-down (Slice B skeleton #3; template-only)

> **STATUS: SKELETON — template only** (family law: body awaits razzorstack GO;
> no `chip_tools.json` changes, no executor wiring). Third family member — the
> class ENVELOPES.md originally gated the whole feed on: discord-bot's gold
> signal going dark.
>
> Lane: **homelab**; contract pinned at the razzorstack-side sha; lands as
> `propose` in the registry (the discord-bot-loop-restart candidate identity,
> or the fail-closed `propose` fallback for unregistered ids).

---
```yaml
skill_id: gold-signal-down
skill_version: "<pinned razzorstack git SHA — REQUIRED, contract law>"
lane: homelab
class: propose
anchor_case: discord-bot status=="ok" as green while its standup loops silently die
             (loops_ok=False class, ENVELOPES.md first-signal clause)
detects: "discord-bot /health reports status=="ok" AND standup_schedule.loops_ok
         ==false — the loud-leading-indicator mismatch the feed was built for"
```

## Special law (the reason this class is its own template)

- The gold signal is the **on-purpose** flag — it says "one of the standup
  loops silently died". It never quiet-flaps; one sighting IS the alarm (the
  feed's verify-twice law treats the second tick as confirmation, not as a
  reason to wait).
- The contained class here is the same `discord-bot-loop-restart` rollback
  candidate as `internal-loop-degrade`, but the FIX is bounded: the runbook is
  loop-restart only; never the compose up / native restart legacy paths (those
  are `registry` `propose`, never `auto` — the runbook registry holds the line).

## Wake payload slots

1. Probe receipt (the discord-bot /health JSON body → `loops_ok=false` + timestamp)
2. Feed findables: fingerprint + latest `learning:` lines (the loop-degrade template
   precedent: one `[chip-feed]` line already posted)
3. Runbook id: `discord-bot-loop-restart` (registry = `propose` — the drafted
   graduation candidate, still needs 3 clean human cycles per ENVELOPES)
4. Thrift class + stand_down flag (frozen ⇒ IDLE)

## Diagnose procedure (script tier first)

1. `http://host.docker.internal:48080/health` → confirm `status: ok` AND
   `standup_schedule.loops_ok: false` (both must hold; either alone = not this class).
2. `docker ps` → discord-bot Up minutes? (a slowly degrading loop looks "Up"
   while it standup schedule fails to advance → confirm standalone from
   standup_schedule timestamps when the payload helps).
3. If the runbook class is still `propose` → wake Ally with the propose card;
   no self-execution.

## Receipt stub (same JSON contract as siblings)

```json
{"skill": {"skill_id": "gold-signal-down", "skill_version": "<SHA>"},
 "result": {"exit": 0, "probe": {"service": "discord-bot",
   "health_status": "ok", "loops_ok": false,
   "detects": "gold-signal-down (standup loop dead while /health green)",
   "evidence_url": "<latest.json receipt>"}},
 "verify_twice": {"first": "<ts>", "second": "<ts>", "agrees": true},
 "rollback": {"runbook": "discord-bot-loop-restart", "class_in_registry": "propose",
   "blocked_target_check": "service name resolves within SERVICE_ALLOWLIST"},
 "lane": "homelab"}
```

## Done bar
green CI + verified-as-of per acceptance box; `claim done` ≠ Done
(`chip.done_gate.may_flip_done()` before any Done flip).
