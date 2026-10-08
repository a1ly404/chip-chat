# Case intake (engine)

Case intake turns monitoring-style signals into structured cases, optional solver passes, and graduation gates. Behavior is driven by pack surfaces (`case_intake`, `case_taxonomy`, `case_intake_escalation`) and env flags.

## Write root

`write_root` in pack `case_intake.yaml` resolves to the repo root unless `CHIP_CASE_INTAKE_WRITE_ROOT` is set. Case files land under pack-configured paths only.

## Dedupe

Slice 1 uses local cache dedupe (`linear_dedupe: local_cache` in pack config). HTTP ticket search is optional slice 2 when `LINEAR_API_KEY` and network policy allow.

## Paging

Optional Discord ping to a configured automation peer with a one-line summary; rate-limited per case class (`page_once_per_class_s`).

## Flags

- `CASE_INTAKE_ENABLED` — master gate on relay.
- `CASE_INTAKE_CHANNELS` — comma-separated room channel names from pack `rooms.json`.
- `CASE_INTAKE_SOLVER_LIVE` — live solver route (default off until operator GO).

## Models

Primary/fallback model slugs are pack fields in `case_intake.yaml`.
