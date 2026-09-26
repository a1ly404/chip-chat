# ENVELOPES — machine-checkable message shapes (Grok-harvest port, 2026-09-26)

Law for every persona: use these fixed shapes instead of inventing process.
Cheap models obey short machine-checkable contracts; they do not invent process well.
Enforcement: discipline + `chip.claims` parser (+ later relay gates). Done discipline:
green CI + per-acceptance-box `verified-as-of` — `claim done` ≠ Done, `log-only` ≠ Done.

## claim (ownership)
```
claim: <persona> <scope> — AC: <acceptance criteria>
```
- FIRST claim on a scope owns it (transcript order = created order). Later claims are duplicates.
- Unlock: `claim done` by owner · confirmed `handoff:` · steal (heartbeat miss AND ping-no-reply).
- One live claim per workstream. Grep transcript (or `chip.claims.scan_claims`) before claiming.

## parked (frozen work)
```
parked: <why> — evidence: <link/probe> — next: <revive condition>
```
- Env broken / waiting on Ally / unpaid smoke → park, do not thrash.
- Dig-only stays dig-only after park; mutate needs a fresh Ally GO.

## learning (knowledge write, NOT Done)
```
learning: <one-line fact>
class: script|GHA|cheap-agent|expensive-agent|wall|refuse
when: <YYYY-MM-DD ~HH:MM PT>
evidence: <PR# / run / probe / path — machine-checkable>
next: <none | promote-to-script | promote-to-GHA | update-skill:<id> | Ally-GO-needed>
```
- One fact per comment. Redact secrets. Repeated learning → promote (script/GHA/skill), don't leave as tribal chat.

## verified-as-of (evidence binding)
```
verified-as-of: <YYYY-MM-DD HH:MM> — <machine-checkable proof>
```
- Bound per acceptance checkbox. Older than the last change window = stale, not fact.
- Done requires green CI + verified-as-of on EVERY box; missing one → reopen.

## handoff (transfer)
```
handoff: <persona> <task> — confirmed by: <persona>
```
- Never silent abandon. Receiver confirms; then (and only then) ownership moves.
