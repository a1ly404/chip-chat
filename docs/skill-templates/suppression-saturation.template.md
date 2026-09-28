# SKILL TEMPLATE — suppression-saturation (Slice B skeleton #6; template-only)

> **STATUS: SKELETON — template only** (family law: razzorstack body awaits
> Ally's GO; no `chip_tools.json` changes, no executor wiring).
> Taxonomy case 5 (docs/MONITOR_CASES): predicted at DEADLETTER_AFTER=10 —
> saturation means the noise floor itself went quiet, so this class MUST be
> louder than the rule it watches (antifragile inversion of the alert ladder).

---
```yaml
skill_id: suppression-saturation
skill_version: "<pinned razzorstack git SHA — REQUIRED, contract law>"
lane: chip           # suppression state lives in chip.monitor's own state file
class: propose       # registry row `clear-stale-suppress` is an auto CANDIDATE; stays propose until Phase C-proven
anchor_case: predicted (DEADLETTER_AFTER=10; first real saturation living receipt upgrades this file)
detects: "monitor dead-letter verdict on any key — the key stopped reaching the
         noise floor's verdict set entirely (10+ strikes / dedup window exhausted)"
```

## Special law for this class

- **Warn-only tier:** saturation posts ONCE per 4h per saturated set
  (the existing cooldown carries it) — never per-strike; the floor is the
  thing that failed, so volume discipline is this skill's own load-bearing wall.
- **Human-reopen is NEVER automated:** clearing a dead-lettered key
  (`clear-stale-suppress` tier) ships with a **human-reopen receipt** — the
  agent records what was suppressed and why, the operator (or a Phase C-graduated
  runbook, eventually) approves the reopen. `id:auto-heal`: live recover needs
  Ally ack until graduated — a suppressor clearing itself is exactly the
  silent self-amend class (golden 13).
- Fail-closed on state loss: if `monitor_state.json` is missing/garbled, the
  skill reports UNKNOWN state and proposes, never claims the floor is clean
  (golden 5 — garbage in, no clean-out).
- Never touches the keys the walls carved out (plex/sonarr/radarr skip-keys,
  BLOCKED_TARGET_PATTERNS, and the SERVICE_ALLOWLIST boundary stay walled OFF
  even here — saturation never widens the walls).

## Wake payload slots

1. Dead-letter receipt JSON (key, fingerprint, strike count vs DEADLETTER_AFTER=10, window)
2. `monitor_state.json` excerpt for the key (fingerprint class history)
3. Registry row: `clear-stale-suppress` (registry `auto`-candidate; template class stays `propose` until proven)
4. Latest 1–2 `learning:` lines for this key/fingerprint
5. Thrift class + stand_down flag (frozen ⇒ IDLE; saturation is DETECTED dark)

## Diagnose procedure (script tier first)

1. Read the dead-letter receipt + `monitor_state.json` excerpt (script tier):
   confirm the key's verdict class is genuinely `dead-letter` (never a
   dedup/flap row misread as one).
2. Classify the suppression history: low-variance repeated tick vs a
   fingerprint that CHANGED shape right before saturation (the latter is
   flap-by-fingerprint-fork — its own tell to Ally).
3. Propose the clear with the human-reopen receipt; if the key was
   SKIP_KEYS-adjacent in traffic patterns (never actually on SKIP_KEYS),
   say so in the proposal, don't silently behave differently.
4. Never clear suppression state from inside this skill body: the state write
   goes through the registry path + ack, or it doesn't happen.

## Receipt stub (same JSON contract as the template family)

```json
{"skill": {"skill_id": "suppression-saturation", "skill_version": "<SHA>"},
 "result": {"exit": 0, "key": "<saturated key>",
 "dead_letter_after": 10, "action": "proposed-clear|kept-suppressed",
 "evidence_url": "<monitor_state.json path>"},
 "verify_twice": {"first": "<ts>", "second": "<ts>", "agrees": true},
 "rollback": {"runbook": "clear-stale-suppress", "class_in_registry": "propose",
 "blocked_target_check": "SKIP_KEYS + BLOCKED_TARGET_PATTERNS + SERVICE_ALLOWLIST walls stay walled regardless of saturation"},
 "human_reopen": {"required": true, "receipt_posted": false},
 "lane": "chip"}
```

## Done bar
green CI + verified-as-of per acceptance box; `claim done` ≠ Done
(`chip.done_gate.reopen_required` law; stamps bound IN the acceptance
section, else refuse — RAZ-217's literal incident class).
