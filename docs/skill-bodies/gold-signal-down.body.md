# SKILL BODY — gold-signal-down (Slice B, chip-side draft #3)

> Destination: razzorstack `.cursor/skills/gold-signal-down.md` (Windows SoT
> clone; move per docs/skill-bodies/MANIFEST.md). Pin the SHA at move time.

---
```yaml
skill_id: gold-signal-down
skill_version: "<pinned razzorstack git SHA — REQUIRED, contract law>"
lane: homelab
class: propose
anchor_case: predicted — first-signal law from ENVELOPES (no live observation yet)
detects: "discord-bot /health status != ok OR standup_schedule.loops_ok false —
         the gold signal never flaps silently (ENVELOPES phase table)"
```

## Special law

- A gold-signal DOWN state is actionable on FIRST confirmed tick pair (verify-twice);
  the feed already posts it once per 4h per fingerprint.
- The recovery runbook row here is `discord-bot-loop-restart` (registry `propose`,
  card 1 of docs/PHASE_B_CARDS_READY.md): one named unit, one command, idempotent,
  single-service invariant enforced by the executor allowlist.
- Rollback tier = Tier 3 only: the executor refuses everything but the named
  service; NAS/plex classes stay dark-forever even here.

## Wake payload slots

1. Probe receipt JSON for discord-bot (full body, gold signal nested under standup_schedule)
2. Prior ticks with loops_ok transitions (machine-checkable history)
3. Registry row `discord-bot-loop-restart` text (propose class current)
4. Latest 1–2 `learning:` lines for discord-bot
5. Thrift class + stand_down flag (frozen ⇒ IDLE)

## Diagnose procedure (script tier first)

1. Parse the gold signal from the probe receipt; if missing, that field's absence IS
   the finding (absence of the signal is a finding, never a guess)
2. Window check: two consecutive actionable ticks → propose path opens
3. Propose with Card 1 receipt shape; chip lane posts the proposal, the OPERATOR
   presses the Phase C button (human-press law until Phase D)
4. Never wake an always-on babysit loop for this (golden 10; machine-checkable
   anomaly only)

## Receipt stub

```json
{"skill": {"skill_id": "gold-signal-down", "skill_version": "<SHA>"},
 "result": {"exit": 0, "probe": {"service": "discord-bot", "status": "ok",
 "loops_ok": false, "standup_schedule": {"loops_ok": false}},
 "evidence_url": "<latest.json tick receipt>"}},
 "verify_twice": {"first": "<ts>", "second": "<ts>", "agrees": true},
 "rollback": {"runbook": "discord-bot-loop-restart", "class_in_registry": "propose",
 "blocked_target_check": "SERVICE_ALLOWLIST {discord-bot}; BLOCKED_TARGET_PATTERNS"},
 "phase_c_human_press": true,
 "lane": "homelab"}
```

## Done bar
green CI + verified-as-of per acceptance box; `claim done` ≠ Done
(`chip.done_gate.reopen_required` law).
