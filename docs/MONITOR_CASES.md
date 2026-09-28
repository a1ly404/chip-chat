# Monitoring case taxonomy — chip-feed → skill-domain mapping

Slice A (Ally GO 2026-09-27). The index the monitoring skills hang off. Derived
from live soak receipts (PRs #48–#53, prod 3ff803d): each alert class observed
or predicted on the real feed, mapped to its runbook lane and skill home.

Lane law: skills live in **razzorstack** (homelab domain, opsdev only for
Oracle-specific errors); chip chat calls them through thin, authorize-gated
runners (Slice B). Chip chat never executes razzorstack mutations directly.

## Case classes

| # | case id | fingerprint shape | observed | lane / skill home | runbook (registry) | Ally ack? |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `transport-blip` | `<key> transport: <URLError/TimeoutError>` one tick only | yes (soak 2026-09-27, monitoring-api NAT blip) | chip-feed internal (no skill — dedup + verify-twice already handle) | none (recover itself) | n/a |
| 2 | `status-drift` | `<key> status=<value≠expected>` | yes (synthetic; predicted for real drift) | homelab | `re-run-health-probe` (auto candidate), escalate to case 4 on persistence | propose → auto after graduation |
| 3 | `gold-signal-down` | `discord-bot status=ok loops_ok=False` | no (predicted — first signal per ENVELOPES) | homelab | `discord-bot-loop-restart` (propose — first graduation candidate) | yes until Phase D |
| 4 | `internal-loop-degrade` | probe JSON green BUT service logs show error loop (stale image ref / crash-loop) — detect via `.log_api` window (Slice B) | yes — monitoring-api `404 No such image sha256:8e5db…` every ~2min since 2026-09-25 22:14 PT | homelab (razzorstack compose) | new skill `monitoring-api-internal-loop` (Slice B); diagnose → restart/re-tag | yes (propose) |
| 5 | `suppression-saturation` | monitor dead-letter verdict on any key | no (predicted at DEADLETTER_AFTER=10) | chip | `clear-stale-suppress` (auto) + human-reopen receipt | warn-only (posts once per 4h) |
| 6 | `skill-runner-fault` | `<skill_id>` timed-out / contract-drift / hang (Slice B) | no (future) | chip (runner dead-man) | new `skill-runner-restart` (propose) | yes |

## Text-source signature classes (added per Ally 2026-09-27 — advisor + PGS + standup-stall)

Real chip-feed-detectable signatures from the 2026-09-27 morning wall. They key
on STRUCTURED numbers in a text-source receipt — never prose scraping (a
prose-only source counts as `source=silent` under the emission contract).

1. **`advisor-timeout-banner`** (advisor tooling is razzorstack's; chip DETECTS only): a source's receipt contains an explicit bounded-fail banner, e.g. `Command ['python3', 'scripts/disk_pressure_advisor.py', '--force'] timed out after 180 seconds` → alert `{key: "stage1-advisor", detail: "timeout=180s"}`. Diagnosing the hang parks to homelab lane. Observed 2026-09-27.
2. **`pgs-zero-production`** (observed 2026-09-27: `ran=true AND ok==0 AND skipped==0 AND error==0`,.embedding a soft-health lie): scheduler-says-healthy while the delta is zero — the standing "health green, loop idle" signature. One zero-night = log-only (dedup coalesces); **two consecutive zero-nights = tell-Ally-once**.
3. **`standup-stall`** — absorbed from the emission spec (Chip Chat builds the emitter): `source=silent` stalls carry `payload_age_h`; never five `unknown` lines. **correlated-dark law: ≥2 lanes silent in one window = ONE finding** (shared root cause upstream), tell-Ally-once; single-lane silence logs-and-continues. Phase C gate credit counts ONLY `source=live` receipts (fail-closed per golden 5).

## Verification bar per case (fixture for the 3-day gate resume math)

- The gate counts quiet days only when the feed is **proven** quiet: after the
  razzorstack fix lands, require (a) monitor-loop 404s absent for one full 4h
  cooldown window, (b) zero `monitoring-api`-class actionable/dedup across that
  window — stretch target one full observation day.
- Verify-twice trade-off is permanent: genuine outages alert ≤1 interval late
  ((~5 min at 300s cadence). That is the noise floor working, not a regression.

## Slice B contract stub (design-only here; implemented in razzorstack lane)

```
skill invocation contract (pinned):
  runner passes: skill_id, skill_version (razzorstack git SHA), expected receipt schema
  runner pre-check: skill file exists at pinned path/SHA → else refuse + learning receipt
  receipt carries: skill_id, skill_version, exit, stdout/stderr, probe JSON
  failure classes: timed-out|hang|contract-drift|exec-fail → same noise floor
  success receipts: NOT trusted until re-verified (probe-based, verify-twice)
  runner SLA: hard subprocess timeout (tool.py default) + declared stall marker
```
