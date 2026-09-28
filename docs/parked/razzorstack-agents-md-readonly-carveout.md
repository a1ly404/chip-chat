# PARKED — razzorstack AGENTS.md read-only carve-out sentence (fix-package, ready to apply)

> Lane: razzorstack (Windows SoT). OCgo authored the text; the Cursor lane commits it.
> Backdated legitimacy: the monitoring-api root cause (2026-09-28) came exactly from
> this pattern — read-only diagnosis originating in chip chat, fix-package routing to
> the owning lane, applied without a single repo write by the diagnosing agent.

## What to change (exact diff, two hunks)

**Hunk 1** — `AGENTS.md` repo root, the "Cross-lane discipline" block (after the
Findings-park sentence, ~line 54):

```diff
+**Read-only carve-out (approved Lyla 2026-09-28, RAZ-16 receipt chain):** read-only
+razzorstack access from chip lane is permitted — open/read/`git show`/`gh` reads only.
+No worktree mutations, no forks, no commits, no pushes. All findings route via
+parked-findings or the fix-package pattern for the owning lane to apply.
```

**Hunk 2** — same file, "OCgo" line (~line 59) to remove the ambiguity between
"do not *open*" (which would forbid the read-only diagnosis that proved itself) and
"do not *commit*":

```diff
-**OCgo:** Work only in chip-chat. Do not open or commit in razzorstack (any path). Homelab lane is Cursor on Windows SoT.
+**OCgo:** Work only in chip-chat. Do not commit, push, or mutate razzorstack worktrees (any path); read-only access is per the carve-out above. Homelab lane is Cursor on Windows SoT.
```

## Justification (receipt chain)

- **Precedent (the receipts-backed argument that pre-empts "this weakens lane law"):**
  the monitoring-api stale-image root cause (2026-09-28) was produced ENTIRELY from
  read-only probes against the Windows SoT — no repo mutation, no commit — and the
  owning lane applied the package successfully the same day (container recreated,
  health green, zero stale-binding since). Codifying the carve-out doesn't create a
  new capability; it legalizes the working pattern that already delivered.
- The lane's intent (RAZ-237, one-lane-one-writer) is about WRITE identity — commits
  originate at the SoT clone. Reads were always the working pattern (same receipt).
- Ambiguity cost is real: OCgo hesitated on every pre-condition check that requires
  reads (RAZ-332 flip checklist item 1 = read-only script inspection on the SoT).
- Codify it once, in their file, so dev-bot triage (Phase 3) inherits the permission
  as law rather than as tribal "well reads were fine last time".

## Verification when applied

1. `git diff` on AGENTS.md shows exactly the two hunks (nothing else).
2. Lyla's approval quote is in the Linear receipt chain (RAZ-16 Phase-2-accept post; the header cites the ruling date).
3. The chip-chat journal's coordination note links back here.
