# SKILL TEMPLATE — internal-loop-degrade (Slice B skeleton; Ally prepick 2026-09-27)

> **STATUS: SKELETON — template only.** The razzorstack-side skill body has no GO
> yet (Ally's review is the gate). No `chip_tools.json` changes, no executor
> wiring, no runner — those land in the Slice B PR only after BOTH: razzorstack GO
> + this shape surviving review. Everything below is the pre-agreed dropped-in
> shape: a razzorstack skill body just fills the numbered slots.
>
> Lane: **homelab (razzorstack domain).** Chip chat calls thin, authorize-gated,
> propose-class; **never self-executes** (GOLDEN 16, one-lane-one-writer).

---
```yaml
skill_id: internal-loop-degrade
skill_version: "<pinned razzorstack git SHA — REQUIRED, contract law>"
lane: homelab
class: propose          # registry law: unknown/unproven ids NEVER auto
anchor_case: monitoring-api 404 "No such image sha256:8e5db…" every ~2min
          since 2026-09-25 22:14 PT with /health green
detects: "HTTP 200 + status==healthy/discrepancy with ERROR-CLASS content in
         the service's own logs (stale image ref, crash-loop, GC'd manifest)"
```

## Wake payload slots (filled by the fixer phase, never guessed)

1. Probe receipt JSON (the machine-checkable fail — see stub below)
2. Matched line + error snippet from the service log (bounded excerpt only)
3. Runbook id text + registry class (this template proposes; never executes rouge)
4. Latest 1–2 adjacent `learning:` lines for this fingerprint class
5. Thrift class + stand_down flag (frozen ⇒ stay IDLE; no wake)

## Diagnose procedure (executable razzorstack-side; verify steps are script, not agent)

1. `docker logs --timestamps monitoring-api 2>&1 | grep -E "monitor loop|trace" | tail -15`
2. Sweep for error-class family: `404 Not Found · No such image:` / crash-loop / backoff-loop.
3. Confirm the discrepancy: `/health` returns 200 + status==healthy (the mask).
4. Cross-check the image ref: `docker images --digests` — confirm the ref was GC'd or rebased during compose churn.

## Receipts (the ONLY machine-checkable output accepted)

Probe receipt stub validated byte-shape in pytest `scripts/test_skill_template.py`:
```json
{"skill": {"skill_id": "internal-loop-degrade", "skill_version": "<SHA>"},
 "result": {"exit": 0, "stdout": null, "probe": {"service": "monitoring-api",
 "health_status": "healthy", "log_window": "2026-09-25T22:14:48+00:00..",
 "error_class": "stale-image-ref", "evidence_url": "<log or PR ref>"}},
 "verify_twice": {"first": "<ts>", "second": "<ts>", "agrees": true},
 "rollback": {"runbook": "restart-named-service", "class_in_registry": "propose",
 "blocked_target_check": "service name resolves within SERVICE_ALLOWLIST"},
 "lane": "homelab"}
```

## Failure classes → same noise floor (no handler invented here)
`timed-out · hung · contract-drift (referenced missing at its pinned SHA) · exec-fail`
→ fed to chip.monitor as alert (agent tells Ally once; never silently retried past
verify-twice).

## Correct razzorstack-side fix (for the homelab lane, NOT for this repo)

- The upstream cause: image churn in razzorstack compose GC'd the sha the monitoring-api monitor loop holds. Chip lane documents the receipts only.
- FIX (homelab lane): `docker compose pull <service>` or re-tag the expected image sha inside monitoring-api's config; resume the log-cadence.
- Two consecutive clean log windows required before the feed's 3-day gate resumes (ENVELOPES.md gate bar).

## Rollback
`restart-named-service` is the rollback tier; interactive execution requires
standard break-glass: Ally ack + receipt (PR #567 SoT hooks / RAZ-237 discipline —
the change itself happens razzorstack-side; the chip lane only documents the receipt).

## Done bar
green CI + verified-as-of per acceptance box; `claim done` ≠ Done (RAZ-182/217
machinery now in repo: chip.done_gate.reopen_required() before any Done flip).
