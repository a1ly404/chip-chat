# JEV_INTEGRATION_PROPOSAL — verifiable spec (Ally review gate; NOT implemented)

> Status: PROPOSAL ONLY. Nothing here is wired; no routing change exists; personas
> stay exactly as the standing spec says (dev Qwen 3.7 Flash / PM GLM 5.3-Flash /
> mock+CI). Merging this document = agreeing the CONTRACT, not the build. Build
> happens in a separate PR after your GO + (per law) the Grok consult.
>
> Key fact this spec verifies first: **Jev runs over the SAME OpenRouter API key**
> chip already uses (`OPENROUTER_CC_API_KEY`) — model slug `typesafe/jev-1.13`,
> endpoint `POST https://openrouter.ai/api/alpha/decisions` (OpenRouter's Decisions
> API), billed to the same OpenRouter account. **No new vendor, no new credential.**

## 1. What Jev is / is not (verifiable from OpenRouter's model page)

| property | fact | verification |
| --- | --- | --- |
| model id | `typesafe/jev-1.13` (pinned; `~typesafe/jev-latest` NEVER used by chip) | `GET openrouter.ai/typesafe/jev-1.13` model page |
| output | typed decisions (`choice`/`noul`/`score`) + confidence; FREE output tokens; NO text/prose possible | API reference + response shape below |
| price | $0.042/M input tokens (output free); response carries `usage.cost` in USD | per-call spend = self-reporting |
| context | 32k tokens (state + questions) | model page |
| auth | same OpenRouter key chip already holds | docs: "any valid OpenRouter API key" |

## 2. Where chip uses it (4 insertion points — all ADVISORY scoring)

| # | spot | question schema (typed) | law carried |
| --- | --- | --- | --- |
| 1 | **Wake-eligibility (golden 8 qualifier)** | `noul`: "does this anomaly qualify for a personalized wake under R8?" — criteria: machine-checkable anomaly fields from the receipt | low confidence ⇒ NO wake (R8: exceptions are machine-checkable, never vibes) |
| 2 | **Runbook-id classifier (fixer wake)** | `choice`: one of the registry runbook ids ∪ `needs-repro` | low confidence ⇒ `needs-repro` (R14 encoded as output schema) |
| 3 | **Text-source signature classifier** (advisor-banner / pgs-zero-prod / standup-stall) | `choice` over the MONITOR_CASES signature classes + `noul noise` | feed dedup/verify-twice still applies to whatever it labels |
| 4 | **Done-gate suspicion pre-check** | `noul`: "does this receipt line actually evidence THIS box?" | STRUCTURAL regex stays the authority; Jev only flags suspicion for human review — never passes/fails alone |

NOT used for: persona replies (it cannot speak), any Law-harness test (those stay
deterministic pytest), any `authorize()`/`monitor.classify` decision (deterministic
code remains the gate — Jev only scores candidates the gates hand it).

## 3. Determinism, fail-closed, and no-second-path (the three laws the spec must satisfy)

- **Determinism/pinning:** slug pinned to `typesafe/jev-1.13`, never the alias; the
  dated snapshot is recorded in every receipt. Thresholds are constants in code
  (`JEV_WAKE_MIN_CONFIDENCE = 0.90`, `JEV_RUNBOOK_MIN_CONFIDENCE = 0.80`, Ally-tunable).
- **Fail-closed:** Jev unreachable / timeout / rate-limited / malformed response ⇒
  callers fall back to the deterministic path (rules/`needs-repro`/no-wake) —
  exactly one retry, then fallback. Jev-down must be indistinguishable from "gray
  zone, human reviews later" — never a blocker, never a hidden free pass.
- **No second path:** `authorize()`, `monitor.classify()`, `done_gate` regexes remain
  the ONLY gates. Jev output can lower a burden of review, never bypass a gate. The
  diff review check: any PR adding a Jev call must prove no gate's verdict can be
  overridden by it (test asserts the deterministic fallback exists per call site).

## 4. Spend + budget (native to the existing gates)

- Every response carries `usage.cost` (USD, self-reporting) — chip logs it into the
  ledger JSONL as jev rows (`model=typesafe/jev-1.13`), aggregated in `chip spend`.
- Same $5 WARN / $10 HARD monthly; same $0.50/$1.00 session walls; projected real
  usage ≈ $0.00x/month at wire scale (input-token metering; output free).
- **Jevons-paradox brake sits in law, not price:** rate of jev calls per window is
  bounded by the R8/R12 counters (wake-eligibility runs only when a receipt is
  already actionable; sweep classification runs once per new fingerprint). No other
  call cadence exists to invent.

## 5. Secrets / tenant-boundary law checks (golden 15 + 16)

- Inputs: structured receipts only (probe JSON, fingerprints, counters, redacted
  text) — health strings, no secret values. The schema REJECTS any key named like a
  secret (`token|key|secret|password|webhook`) from `state` — enforced in the
  request builder with a test (redaction sweep before send).
- Key = the existing `OPENROUTER_CC_API_KEY` env; never printed (same openrouter
  redaction rules as chat completions).
- razzorstack content never enters a Jev `state` (one-lane-one-writer hygiene:
  cross-lane texts are parked to their lanes, not classified here).

## 6. Implementation sketch (build-on-approval; nothing exists yet)

```python
# scripts/chip/jev_router.py (offline-first; live calls behind explicit env)
def decide(state: dict, questions: dict, *,
           environ=None, mock: bool = False) -> dict:
    """POST /api/alpha/decisions with pinned model; returns typed answers.
    mock=True (or CHIP_CHAT_MOCK=1) returns deterministic fixture offline.
    Fail-closed: transport/shape errors -> {"status": "fallback", ...}."""
```

- Env: honors `OPENROUTER_CC_API_KEY` (same key as chat). Mock responses fixture
  versioned in-repo (no network in tests — CI stays $0.00).
- CLI surfacing (both `--json`-shaped, offline-by-default): `chip jev ping` (schema
  probe), `chip jev classify --state-file x.json` — needed for Ally's one-shot
  self-verification without touching prod paths.
- Live calls: only inside the 3 call sites of §2, only when their host feature is
  itself enabled (Phase B/C gates), and only after the first fixture-based eval
  shows ≥95% agreement with the deterministic baseline on a labeled replay set.

## 7. Ally verification checklist (how you check each claim without me)

1. **Single-key claim**: OpenRouter docs (jev tutorial) state "any valid OpenRouter
   API key works, billed to your account" — no TypeSafe signup. Check the cc-env:
   still exactly one `OPENROUTER_CC_API_KEY` in `.env`/Infisical after any build.
2. **Pinning check**: `grep -r "jev-latest" scripts/` → zero hits ever.
3. **No-silent-integration check**: `grep -rn "jev" scripts/chip/(authorize|monitor|done_gate).py`
   → zero hits (Jev never lives INSIDE the gates - it is called BY callers).
4. **Determinism of gates unchanged**: full suite includes the existing
   determinism/dedup/authorize tests untouched by Jev PRs.
5. **Spend check**: `chip spend` shows jev rows only after an explicit live GO;
   mock/CI total stays $0.00.
6. **Cost reality check**: model page price $0.042/M input, output free; usage.cost
   in every response is auditable per call.

## 8. Open questions for the Ally/Grok consult

1. Rung order confirmation: script → GHA → **Jev** → cheap-agent → expensive-LLM
   (inserting a decision-model rung changes TOKEN_THRIFT ladder text = a spec-A PR).
2. Threshold values (`0.90` / `0.80`) — or does Grok prefer band structure (e.g.
   `>0.95 auto / 0.80–0.95 propose / <0.80 needs-repro`)?
3. Any 17th golden item? ("calibrated-decision gates: confidence < threshold ⇒
   deterministic fallback") — candidate is YOURS to ack, and per the PHASE-B
   R17-note it enters the GOLDEN block only by an explicit window ack of yours,
   never silent backfill.
4. Free tier `jev-1.13-free` (OpenCode Zen) as the dev/CI mockable target? (adds a
   second surface — recommended NO; keep chip on the one OpenRouter pipe).
