# SKILL BODY — suppression-saturation (Slice B, chip-side draft #5)

> Destination: razzorstack `.cursor/skills/suppression-saturation.md` (Windows
> SoT clone; move per docs/skill-bodies/MANIFEST.md). Pin the SHA at move time.

---
```yaml
skill_id: suppression-saturation
skill_version: "<pinned razzorstack git SHA — REQUIRED, contract law>"
lane: chip
class: propose          # registry `clear-stale-suppress` row is an auto CANDIDATE; skill class stays propose until Phase C-proven
anchor_case: predicted (DEADLETTER_AFTER=10; first living receipt upgrades this body)
detects: "monitor dead-letter verdict on any key — the key stopped reaching the
         noise floor's verdict set entirely (dead-letter after 10 strikes)"
```

## Special law

- Warn-only tier: saturation posts once per 4h per saturated set (cooldown carries it):
  the floor itself failed, volume discipline is this skill's own wall.
- Human-reopen is NEVER automated (`clear-stale-suppress` tier ships a
  human_reopen receipt; id:auto-heal + golden 13 — a suppressor clearing itself
  is the silent-self-amend class).
- State loss fails closed: garbled `monitor_state.json` reports UNKNOWN and proposes;
  never claims the floor is clean (golden 5).
- SKIP_KEYS / BLOCKED_TARGET_PATTERNS / SERVICE_ALLOWLIST walls stay walled even here —
  saturation never widens any wall.

## Wake payload slots

1. Dead-letter receipt JSON (key, fingerprint, strike count, window)
2. `monitor_state.json` excerpt for the key (class history)
3. Registry row: `clear-stale-suppress` (auto-candidate; skill tier propose)
4. Latest 1–2 `learning:` lines for this key/fingerprint
5. Thrift class + stand_down flag (frozen ⇒ IDLE)

## Diagnose procedure (script tier first)

1. Confirm the key's verdict class IS dead-letter (never a dedup/flap row misread)
2. Classify suppression history: stable repeats vs fingerprint fork right before
   saturation (the latter is its own tell to Ally)
3. Propose the clear with the human_reopen receipt; SKIP_KEY-adjacent traffic
   noted in the proposal, never silently behaved differently
4. Never clear suppression state in-body: state writes go through registry + ack

## Receipt stub

```json
{"skill": {"skill_id": "suppression-saturation", "skill_version": "<SHA>"},
 "result": {"exit": 0, "key": "<saturated key>", "dead_letter_after": 10,
 "action": "proposed-clear|kept-suppressed", "evidence_url": "<monitor_state path>"}},
 "verify_twice": {"first": "<ts>", "second": "<ts>", "agrees": true},
 "rollback": {"runbook": "clear-stale-suppress", "class_in_registry": "propose",
 "blocked_target_check": "SKIP_KEYS + BLOCKED_TARGET_PATTERNS + SERVICE_ALLOWLIST stay walled"},
 "human_reopen": {"required": true, "receipt_posted": false},
 "lane": "chip"}
```

## Done bar
green CI + verified-as-of per acceptance box; `claim done` ≠ Done
(`chip.done_gate.reopen_required` law — stamps bound IN the section, else
refuse: RAZ-217's literal incident class).
