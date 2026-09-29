# SKILL BODY — airvpn-port-unhealthy (tier-1 skill-framed runbook, Lyla ruling 2026-09-29T04:2xZ, scope RAZ-335 only)

> Destination: razzorstack `.cursor/skills/airvpn-port-unhealthy.md` (Windows
> SoT clone; move per docs/skill-bodies/MANIFEST.md row; pin the SHA at move
> time). Chip-side draft authored for tonight's widened lane (PR #95).

---
```yaml
skill_id: airvpn-port-unhealthy
skill_version: "<pinned razzorstack git SHA — REQUIRED, contract law>"
lane: chipchatdev
class: propose
anchor_case: live — RAZ-335, AirVPN reserved static port 54543 unreachable at 38.88.124.105
entry: python3 scripts/airvpn_port_health.py --diagnose-only
arc: diagnose → root-cause → tier-1 execute (per-run named GO) → verify (listen outside) → learn
```

## Tier-1 execute allowlist (each per-run named GO from Lyla, tonight)

gluetun container restart · qBittorrent container restart · AirVPN port
forward re-grab (re-run grab script) · firewall rule reload (UFW/docker) ·
qBittorrent port setting update.

## Refuse-and-page (never tier-1)

tunnel/provider cutover · anything touching media · any delete · compose
config edits beyond the port variables · secrets.

## Envelope contract (a GO will not validate without ALL of)

1. `receipt_before_action: <ledger path:ticket>` — receipt-before-action is
   the existing store.append_jsonl ledger row, not a promise.
2. `rollback_plan: "to undo: <X>"` — reversibility named BEFORE the action.
3. `verification_probe` + `verified_as_of:` — post-execute probe MUST confirm
   the port listens FROM OUTSIDE THE TUNNEL. Fix without verified-listen does
   not count as fixed — the probe must prove the PORT, not the TUNNEL
   (probe_source=tunnel_egress masking a direct-link failure is the exact
   historical fleet-fooling class this skill exists to kill).

## Law bindings (enforced at authorize() — no path around)

SERVICE_ALLOWLIST gates every execute; not stand_down and claim_owner ==
speaker and the per-run named GO are all checked at the single chokepoint;
done-gate law holds the Done flip behind verbatim acceptance evidence.

## Learnings protocol (the product of the skill)

Per execution, BOTH appends happen:
1. `tool_learning.jsonl`: attempted action, exit code, verification probe
   result, one-line reflection. Nonzero exit or failed verification →
   auto retro ticket (standing hook).
2. Skill record `data/skill_records/airvpn-port-unhealthy.jsonl` (append-only):
   symptom → suspected root cause → action taken → outcome → what to try
   next time.

## Graduation clause (morning review ONLY)

A few clean execution rows tonight → Lyla's morning review decides promotion
to auto-execute ("runs with permission" → "just runs with learnings").
"claim done" closes the arc only with verified-as-of acceptance. Never
self-promote; silence is not acceptance. Scope: RAZ-335 ONLY — no other
tickets, lanes, or mornings without a new ruling.
