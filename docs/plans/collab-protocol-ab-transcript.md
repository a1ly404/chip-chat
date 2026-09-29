# A/B transcript — health port

Problem: `Should the health port stay 48082?`
Personas: `pm`, `chipchatdev`, `opsdev`.
Harness: `chip.collab_ab.run_ab` (mock). Token figures are `len(body)`, not OpenRouter usage. `time_ms` is the injected clock (`7`).

Live OpenRouter arm: not run. This worktree has no `.env`, and the plan keeps the suite offline. No key was loaded from another repo.

```json
{
  "solo": {
    "hypotheses": 1,
    "evidence": 1,
    "critiques_that_killed": 0,
    "cross_critiques": 0,
    "tokens_by_persona": {"pm": 34},
    "version": 1
  },
  "trio": {
    "hypotheses": 3,
    "evidence": 6,
    "critiques_that_killed": 1,
    "cross_critiques": 3,
    "tokens_by_persona": {"pm": 50, "chipchatdev": 79, "opsdev": 75}
  },
  "evidence": 7,
  "hypotheses": {"solo": 1, "trio": 3},
  "critiques_that_killed": 1,
  "time_ms": 7,
  "tokens_by_persona": {"pm": 84, "chipchatdev": 79, "opsdev": 75},
  "grade": "pass"
}
```
