# Chip Chat — error log

Append-only. One entry per error class: id, date, status, symptom, root cause, resolution.

## ERR-001 — persona prefix stacking in Discord replies
- **Date first seen:** 2026-09-26 (#chip-dev log, 10:44–10:57 PM block)
- **Status:** FIXED on `chipchatdev/prefix-stack-fix` (PR pending)
- **Symptom:** replies accumulated persona markers hop over hop —
  `buffrdev: [buffrdev] [buffrdev] [buffrdev] [buffrdev] hop-ok-buffr`.
- **Root cause:** `chip.store.render` prefixes transcript history lines with
  `[agent]` (store.py) so the model/mock sees prefixed context and echoes the
  markers into its reply; the reply is stored, re-rendered with another prefix,
  and the stack grows every hop.
- **Fix:** `chip_relay.dispatch.strip_stacked_persona_prefixes` — runs of 2+
  leading bracketed tokens are stripped before webhook post + transcript record
  (all three post sites). Single leading markers (e.g. `[mock Chip]`) are kept:
  the webhook username already identifies the speaker, but single markers may be
  intentional labels. Regression tests: `scripts/test_chip_relay_prefix_stacking.py`.

## ERR-002 — inject subprocess skipped Infisical (empty webhook)
- **Date first seen:** 2026-09-26 (#chip-dev log, 10:20 PM, tip `0b7574c5`)
- **Status:** RESOLVED on tip (no code change needed)
- **Symptom:** `chip-relay` posted "inject subprocess skips Infisical (listen-only)
  so webhook was empty until this post."
- **Root cause:** on tip `0b7574c5` the inject path did not load runtime secrets.
- **Resolution:** PR #32 ("fix(chip-relay): load Infisical for inject") —
  `chip_relay/startup.py` now calls `apply_runtime_secrets()` before
  `run_inject` (verified in code, startup.py inject branch). Docs (AGENTS.md §4)
  already describe the fixed behavior; the 10:20 PM report predates the fix.
