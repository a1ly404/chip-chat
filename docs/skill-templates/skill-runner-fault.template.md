# SKILL TEMPLATE — skill-runner-fault (Slice B skeleton #5; template-only)

> **STATUS: SKELETON — template only** (family law: razzorstack body awaits
> Ally's GO; no `chip_tools.json` changes, no executor wiring, no runner edits).
> Taxonomy case 6 (docs/MONITOR_CASES): the runner dead-man boundary's skill half.

---
```yaml
skill_id: skill-runner-fault
skill_version: "<pinned razzorstack git SHA — REQUIRED, contract law>"
lane: chip           # the runner is chip lane; the faulted TARGET may be any lane
class: propose       # registry law: `skill-runner-restart` stays propose until Phase C proven
anchor_case: predicted (no live observation yet — first real fault feeds the contract)
detects: "<skill_id> runner fault signatures — timed-out / contract-drift / hang —
         with a runner-attached receipt (never agent-fabricated evidence)"
```

## Special law for this class (runner dead-man)

- Evidence receipts are **runner-attached only** (PR #45/#46 law): the agent
  NEVER fabricates fault evidence — a fault claim without the runner's receipt
  JSON attached is refuse-class, screenshot-or-it-didn't-happen (golden 1).
- A faulting runner NEVER wakes recovery by itself: `skill-runner-restart`
  sits in the registry as `propose`, the wake is a *tell* (runner + fault class
  + receipts), and any restart waits for Phase C graduation + Ally ack.
- Fault classes share one fingerprint per (skill_id, fault-class) — timeout=N_seconds,
  contract-drift, hang — so the noise floor still dedups; a runner that flaps
  across classes escalates by the LADDER (log/tell-once/page), never by vibes.
- Chip never restarts another lane's services here: `SERVICE_ALLOWLIST`
  resolution stays in `authorize()`; the BLOCKED_TARGET_PATTERNS wall wins
  even if this template's rollback tier would otherwise say go.

## Wake payload slots

1. Runner receipt JSON (exit code, `outcome: timed_out|contract_drift|hang|recovered`,
   wall-clock cost, runner version — runner-attached, never synthesized)
2. Dead-man state: last heartbeat ts + interval, consecutive-miss count
3. Registry row: `skill-runner-restart` (class `propose`) + the faulted skill's own runbook row
4. Latest 1–2 `learning:` lines for this skill_id
5. Thrift class + stand_down flag (frozen ⇒ IDLE — faults are detected while dark)

## Diagnose procedure (script tier first)

1. Re-read the runner receipt (script tier: `chip done-gate --file <receipt>`,
   informational `--scan`): is the outcome field present and runner-attached?
2. Contract-drift → diff the receipt's shape against this template's stub —
   drift is a proposal to the template family, never a live patch (golden 13).
3. timed-out → the faulted skill's own throughput wall (thrift class D)
   becomes the probe question for the next run — never re-run unbounded (rule 2).
4. Never wire a fallback runner in this skill's body: the registry path and
   Ally's ack are the only upgrade (id:auto-heal; 01:00–05:00 band is mute).

## Receipt stub (same JSON contract as the template family)

```json
{"skill": {"skill_id": "skill-runner-fault", "skill_version": "<SHA>"},
 "result": {"exit": 0, "outcome": "timed_out|contract_drift|hang|recovered",
 "evidence": {"attached": true, "source": "runner-receipt",
 "evidence_url": "<chip_state runner receipt path>"}},
 "verify_twice": {"first": "<ts>", "second": "<ts>", "agrees": true},
 "rollback": {"runbook": "skill-runner-restart", "class_in_registry": "propose",
 "blocked_target_check": "faulted target resolves within SERVICE_ALLOWLIST or stays blocked"},
 "lane": "chip"}
```

## Done bar
green CI + verified-as-of per acceptance box; `claim done` ≠ Done
(`chip.done_gate.reopen_required` law + `monitor_watchdog --baseline`
class-shrinkage check; stamps bound IN the acceptance section, else refuse).
