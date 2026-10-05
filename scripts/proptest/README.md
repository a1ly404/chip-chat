# Property-test harness for skill gates

Reference model + generators + invariant assertions for the chip-relay skill
gate pipeline, per the "property testing with agent swarms" workflow
(https://recursion.wtf/posts/agents-and-property-tests/). The machinery is
built ONCE; agent swarms (or CI, or you) run thousands of generated incident
histories through the REAL gates without spending LLM tokens per case.

## What's enforced

Every generated history is a list of alert events (timestamps + alerts) plus
skill-law "episodes" (the law file is rewritten between episodes, so
allowlist/suppression/expiry churn mid-history is exercised). For every
history, the harness runs the real `chip_relay.service_recovery_skill.maybe_run`
against recording mocks and asserts:

1. **Exact gate trace** — the real pipeline's receipt statuses, executor call
   sequence, report-post count, and PM/qwen call counts must equal the plain
   reference model (`proptest/reference.py`) event for event.
2. **Allowlist wall** — zero executor calls on non-allowlisted containers.
3. **Suppression wall** — zero executor calls on suppressed containers.
4. **Native wall** — zero executor calls on `missing-container` (native
   service) targets.
5. **Dedup** — the same incident fingerprint is never executed twice within
   the rolling 24h window (one attempt per incident).
6. **Cap** — no attempt beyond `max_attempts_24h` in any rolling window.
7. **Fail-closed** — unparsable/absent expiry, garbage law JSON, missing HMAC
   secret, PM no-go or PM fault: refusal receipts, never execution.
8. **Mock boundary** — under `CHIP_CHAT_MOCK=1` with the default report path,
   the live monitoring channel is NEVER reached, for ANY history. (This is
   the W-013 live-post regression, as a property.)
9. **Duplicate-storm regression** — a storm of re-pushed alerts for the SAME
   failing container with VARYING raw error text yields exactly ONE executor
   call and ONE message. (The W-013 duplicate-post bug, as a property: the
   old fingerprint hashed the raw text, so every re-push minted a new
   incident and re-fired.)

## Run it

```bash
# CI suite: ~50 examples per property (pytest)
python3 -m pytest scripts/test_property_gates.py -q

# Swarm entry point: 500 examples per property, machine-readable JSON summary
cd scripts && python3 -m proptest.run

# Deep sweep, reproducible
cd scripts && python3 -m proptest.run --examples 5000 --seed 42

# Single property, subset
cd scripts && python3 -m proptest.run --examples 1000 --properties pipeline_matches_reference
```

The runner prints one JSON document to stdout:

```json
{
 "tool": "chip-proptest",
 "examples_per_property": 500,
 "verdict": "PASS",
 "properties": {"pipeline_matches_reference": {"verdict": "PASS", ...}},
 "seconds": 30.1
}
```

Exit code 0 = all properties held; 2 = at least one falsified.

## Generator strategy coverage

`proptest/generators.py` embeds the interaction patterns that historically
broke the gates, per the article's "make operations interact" rule:

- duplicate storms: same container + failure class, varying error text
  (docker req-ids), 2-8 re-pushes, jittered timestamps;
- floods: many distinct incidents (drives the 3/24h cap);
- interleaved containers with re-appearing incidents;
- native-vs-container confusion (`sonarr`/`radarr`/`plex`), unknown
  containers, suppressed containers;
- allowlist/suppression/expiry churn across episode boundaries (entries
  reinserted and removed mid-history);
- clock jitter, small backward jumps, and 12-36h jumps crossing the 24h
  window; second-vs-microsecond precision (attempt stamps persist at seconds
  precision);
- malformed alerts (wrong/near-miss keys, missing `container=` field, empty
  container name) and garbage law JSON;
- faults injected mid-sequence: PM decider refusing/faulting, executor
  failing on the k-th call;
- colliding alert-supplied fingerprints (the `fingerprint` stub overrides the
  computed identity — the model mirrors that).

## When a property fails (the swarm workflow)

1. Hypothesis prints the SHRUNK minimal history to stderr (the runner
   re-raises after the JSON summary so both appear). A failing example is a
   plain-JSON scenario dict.
2. Reproduce deterministically: `python3 -m proptest.run --seed <seed> --examples <n>`,
   or paste the scenario into a scratch test and call
   `proptest.harness.check_scenario(scenario, tmpdir)`.
3. Classify: if the REAL gates are wrong, write a minimal regression test
   (see `test_regression_w013_*` in `scripts/test_property_gates.py` for the
   shape), fix the skill in its own standalone PR — test must fail before the
   fix and pass after. If the REFERENCE model is wrong (the law intends
   different behavior), fix `proptest/reference.py` in the same PR as any
   generator that exposed the ambiguity.
4. Keep the discovery sweeps on a branch; land fixes as small PRs. Extend
   generators toward whatever the last bug class was — a bug pattern a
   generator can't produce is a coverage hole.

## Extending to other skills

The harness targets `service_recovery_skill` (the fullest gate chain). To
cover `plex_fix_skill` (same pattern: expiry -> enabled -> dedup -> cap ->
allowlist, plus probe/docker), add a second reference model in
`proptest/reference.py` keyed by the plex gate order, a generator for
`plex-external*` alerts, and a `check` function in `proptest/harness.py` —
then wire it into `proptest/run.py`'s property table and pytest module. The
mock/report/executor seams are already there.

## Out of scope (known)

- Law files with wrong TYPES (e.g. `max_attempts_24h: "abc"`): the real
  `law_config` would raise on `int()` — the generator only produces
  well-typed laws or garbage JSON (which fails closed cleanly).
- `alert_ingest` routing and the shadow-ledger dedupe (separate mechanism,
  has its own unit tests).
