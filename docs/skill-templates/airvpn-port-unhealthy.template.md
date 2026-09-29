# skill: airvpn-port-unhealthy
# family: runbook-diagnose-execute (tier-1 skill-framed, Lyla ruling 2026-09-29T04:2xZ)
# lane: chipchatdev
class: propose
skill_version: "<pinned razzorstack git SHA at move>"
skill_id: airvpn-port-unhealthy
scope: RAZ-335 only (this skill does NOT generalize — new ruling required)
expires_at_midnight_PT: 2026-09-29 hard stop; morning review decides graduation (auto-execute ≥ few clean rows)

## Envelope (each tier-1 execution MUST carry, or the GO will not validate)

```
skill: airvpn-port-unhealthy
ticket: RAZ-335
goal: <one line, the symptom>
action: <one of the tier-1 allowlist, verbatim from this file>
receipt_before_action: <existing ledger path:ticket>
rollback_plan: "to undo: <X>"
verification_probe: python3 scripts/airvpn_port_health.py --diagnose-only
verified_as_of: <ts + observed-listen result>
claim_done: done: RAZ-335 <one-line outcome>   # done bar: claim done flips ONLY on verified_as_of evidence
```

Fix without verified-listen does NOT count as fixed — the probe must prove
the PORT, not the TUNNEL (probe_source=tunnel_egress is exactly the
transport-success-masks-direct-link-failure trap; see RAZ-335).

## Tier-1 execute allowlist (each per-run named GO from Lyla tonight)

- gluetun container restart
- qBittorrent container restart
- AirVPN port forward re-grab (re-run grab script)
- firewall rule reload (UFW / docker)
- qBittorrent port setting update

## Refuse-and-page (NEVER tier-1)

- tunnel / provider cutover
- anything touching media
- any delete
- compose config edits beyond the port variables
- secrets

## Law bindings (non-negotiable, enforced elsewhere in the stack)

SERVICE_ALLOWLIST gates every execute; not stand_down and claim_owner ==
speaker and per-run named GO all checked at authorize() — no path around
the chokepoint. Silent churn is banned: every action leaves a receipt.

## Learnings protocol (the actual product of the skill)

Per execution append BOTH:
1. `tool_learning.jsonl` row: attempted action, exit code, verification
   probe result, one-line reflection (nonzero exit → auto retro ticket).
2. Skill record (append-only, below): symptom → suspected root cause →
   action taken → outcome → what to try next time.

Claim-closing: Done flips only after verified-as-of acceptance evidence;
no claim without artifacts.

## Graduation clause (morning review only)

"runs with permission" → "just runs with learnings" happens ONLY on a
morning ruling after a few clean execution rows tonight. Never self-
promote; silence is not acceptance.
