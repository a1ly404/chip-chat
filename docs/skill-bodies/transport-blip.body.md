# SKILL BODY — transport-blip (Slice B, chip-side draft #2)

> Destination: razzorstack `.cursor/skills/transport-blip.md` (Windows SoT
> clone; move per docs/skill-bodies/MANIFEST.md). Pin the SHA at move time.

---
```yaml
skill_id: transport-blip
skill_version: "<pinned razzorstack git SHA — REQUIRED, contract law>"
lane: homelab
class: propose
anchor_case: transient monitoring-api probe timeout via host-gateway NAT (2026-09-27 soak tick #1)
detects: "probe transport failure(s) — URLError/TimeoutError/ConnectionError —
         that RECOVER within the alarm cooldown (dedup/flap counters feed back)"
```

## Special law

- Transients get NO Discord post on first sighting (verify-twice law; enforced by
  `monitor_feed.post_alerts` single-tick suppression).
- Repeated blips (>=3 within one hour) escalate through `chip.monitor`'s flap
  threshold — the boundary is machine-checkable, never agent vibes.
- Recovery is the SKILL's own success condition; a transport tier fixture may
  never widen the SERVICE_ALLOWLIST (walls unchanged).

## Wake payload slots

1. Probe receipt JSON (transport receipt with attempts + effective timeout)
2. `chip.monitor` counter excerpt for the fingerprint (count / window)
3. Runbook id: `re-run-health-probe` (registry `auto`) — re-probe FIRST, wake only if CONFIRMED
4. Latest 1–2 adjacent `learning:` lines for this fingerprint
5. Thrift class + stand_down flag (frozen ⇒ IDLE)

## Diagnose procedure (script tier first)

1. Direct re-probe inside the relay container (runbook `re-run-health-probe`)
2. Re-probe OK → transient confirmed → `learning:` comment, NO other action
3. Re-probe fails → escalate toward `status-drift` / `internal-loop-degrade`
4. Never fix DNS/NAT infrastructure from this skill (opsdev, blocked patterns)

## Receipt stub

```json
{"skill": {"skill_id": "transport-blip", "skill_version": "<SHA>"},
 "result": {"exit": 0, "probe": {"service": "monitoring-api", "attempt": 2,
 "timeout_s": 5, "outcome": "transient-confirmed|persistent",
 "evidence_url": "<latest.json tick receipt>"}},
 "verify_twice": {"first": "<ts>", "second": "<ts>", "agrees": true},
 "rollback": {"runbook": "re-run-health-probe", "class_in_registry": "auto",
 "blocked_target_check": "SERVICE_ALLOWLIST resolution, else blocked"},
 "lane": "homelab"}
```

## Done bar
green CI + verified-as-of per acceptance box; `claim done` ≠ Done
(`chip.done_gate.reopen_required` law).
