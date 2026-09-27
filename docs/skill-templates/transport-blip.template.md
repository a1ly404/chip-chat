# SKILL TEMPLATE — transport-blip (Slice B skeleton #2; template-only)

> **STATUS: SKELETON — template only** (same law as internal-loop-degrade:
> razzorstack body awaits GO; no `chip_tools.json` changes, no wiring).
> Highest-volume / cheapest class in the feed receipts — the generalization
> proof for the template family: if this one and internal-loop-degrade share
> the receipt contract cleanly, remaining taxonomy cases (2,3,5) are drops.

---
```yaml
skill_id: transport-blip
skill_version: "<pinned razzorstack git SHA — REQUIRED, contract law>"
lane: homelab
class: propose          # registry law: cheapest class, still propose until proven
anchor_case: transient monitoring-api probe timeout via host-gateway NAT
             (2026-09-27 soak tick #1; recurred 10:40PM/4:15AM PT pairs)
detects: "probe transport failure(s) — URLError/TimeoutError/ConnectionError —
         that RECOVER within the alarm cooldown (fed back by dedup/flap counters)"
```

## Special law for this class (differs from internal-loop-degrade)

- Transients get NO Discord post on first sighting (verify-twice law: single-tick
  blips stay silent — chip-feed already enforces this in `monitor_feed.post_alerts`).
- The skill tier here is the **escalated wake**: repeated transport blips across a
  window (≥3 within an hour) upgrade the class — that boundary lives in
  `chip.monitor` (flap threshold), never in agent vibes.

## Wake payload slots

1. Probe receipt JSON (transport receipt with attempts + effective timeout)
2. `chip.monitor` counter excerpt for the fingerprint (count / times window)
3. Runbook id: `re-run-health-probe` (registry class `auto`) — re-probe first,
   wake the operator only if the re-probe CONFIRMS the failure (verify-twice)
4. Latest 1–2 adjacent `learning:` lines for this fingerprint
5. Thrift class + stand_down flag (frozen ⇒ IDLE)

## Diagnose procedure (script tier first)

1. `docker exec chip-chat-chip-relay-1 sh -c "python3 -c 'import urllib.request; urllib.request.urlopen(\"http://host.docker.internal:48082/health\", timeout=5)'"` — direct re-probe (runbook `re-run-health-probe`).
2. If re-probe succeeds → transient confirmed → learning: comment, NO further action.
3. If re-probe fails → defer class escalates toward `status-drift`/`internal-loop-degrade` template.
4. Never fix transport-layer infra from this skill alone (DNS/NAT changes are opsdev).

## Receipt stub (same JSON contract as the loop-degrade template)

```json
{"skill": {"skill_id": "transport-blip", "skill_version": "<SHA>"},
 "result": {"exit": 0, "probe": {"service": "monitoring-api",
 "attempt": 2, "timeout_s": 5, "outcome": "transient-confirmed|persistent",
 "evidence_url": "<latest.json tick receipt>"}},
 "verify_twice": {"first": "<ts>", "second": "<ts>", "agrees": true},
 "rollback": {"runbook": "re-run-health-probe", "class_in_registry": "auto",
 "blocked_target_check": "service name resolves within SERVICE_ALLOWLIST"},
 "lane": "homelab"}
```

## Done bar
green CI + verified-as-of per acceptance box; `claim done` ≠ Done
(`chip.done_gate.may_flip_done()` before any Done flip).
