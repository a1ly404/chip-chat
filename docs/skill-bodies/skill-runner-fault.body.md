# SKILL BODY — skill-runner-fault (Slice B, chip-side draft #4)

> Destination: razzorstack `.cursor/skills/skill-runner-fault.md` (Windows SoT
> clone; move per docs/skill-bodies/MANIFEST.md). Pin the SHA at move time.

---
```yaml
skill_id: skill-runner-fault
skill_version: "<pinned razzorstack git SHA — REQUIRED, contract law>"
lane: chip
class: propose
anchor_case: predicted (no live observation yet — first real fault feeds this body)
detects: "<skill_id> runner fault signatures — timed-out / contract-drift / hang —
         with a runner-attached receipt (never agent-fabricated evidence)"
```

## Special law (runner dead-man)

- Evidence receipts are runner-attached ONLY; an agent-claimed fault with no
  runner receipt attached is refuse-class (golden 1, screenshot-or-it-didn't-happen).
- A faulting runner NEVER wakes recovery by itself; `skill-runner-restart` stays
  registry `propose`; faults are told, restarts wait for Phase C graduation + Ally ack
  (id:auto-heal).
- Fault classes share one fingerprint per (skill_id, fault-class) — timed-out=N_seconds,
  contract-drift, hang — so the noise floor dedups; class-crossing flaps escalate
  by the ladder (log / tell-once / page), never vibes.

## Wake payload slots

1. Runner receipt JSON (exit code, outcome, wall-clock cost, runner version)
2. Dead-man state: last heartbeat ts + interval, consecutive-miss count
3. Registry row: `skill-runner-restart` (propose) + the faulted skill's runbook row
4. Latest 1–2 `learning:` lines for this skill_id
5. Thrift class + stand_down flag (frozen ⇒ IDLE)

## Diagnose procedure (script tier first)

1. Read the runner receipt; is the outcome field PRESENT and runner-attached?
2. Contract-drift → diff the receipt shape against the family stub; a shape drift is
   a proposal to the template family, never a live patch (golden 13)
3. Timed-out → the next run's throughput wall question (thrift class D), never a
   free re-run (rule 2 budget walls)
4. Never wire a fallback runner; the registry path + Ally ack is the only upgrade

## Receipt stub

```json
{"skill": {"skill_id": "skill-runner-fault", "skill_version": "<SHA>"},
 "result": {"exit": 0, "outcome": "timed_out|contract_drift|hang|recovered",
 "evidence": {"attached": true, "source": "runner-receipt", "evidence_url": "<path>"}},
 "verify_twice": {"first": "<ts>", "second": "<ts>", "agrees": true},
 "rollback": {"runbook": "skill-runner-restart", "class_in_registry": "propose",
 "blocked_target_check": "SERVICE_ALLOWLIST or stays blocked"},
 "lane": "chip"}
```

## Done bar
green CI + verified-as-of per acceptance box; `claim done` ≠ Done
(`chip.done_gate.reopen_required` law).
