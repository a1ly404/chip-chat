# Parked Findings

Findings from agent lanes that need a **razzorstack** mutation. The lane that found
them cannot self-execute (cross-lane discipline); the homelab lane (Cursor on
Windows SoT) picks these up.

| date | lane | finding | ticket/ref | status |
|---|---|---|---|---|
| 2026-11-15 | opgo (Nancy reclaim agent) | Add gated executor-allowlist scripts `nancy_reclaim_disk_check` (read-only df) and `nancy_reclaim_tier_sweep` (kill-flag + window gated, calls scripts/nancy_reclaim.py --execute --with-tiers with capped max-delete-gb) to the razzorstack homelab-executor allowlist so chip-chat's nancy-reclaim-watch can upgrade from parse-only. Target/authorization note already reserved in chip-chat-razzorstack-plugin config/chip_relay/nancy_reclaim.json (merged PR #589). razzorstack PR #879 provides the tiered script this would call. | RAZ-130 | waiting on homelab lane |
