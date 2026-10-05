"""Property tests: skill gates vs a plain-Python reference model.

Machinery per the "property testing with agent swarms" workflow
(recursion.wtf/posts/agents-and-property-tests/): a reference implementation
(``proptest.reference``), generators embedding the interaction patterns that
historically broke the gates (``proptest.generators``), and the invariant —
for EVERY generated history, the real gate pipeline
(``chip_relay.service_recovery_skill.maybe_run`` against recording mocks)
must produce exactly the reference's action trace, with zero executor calls
outside the allowlist, zero cap violations, zero duplicate attempts, and
fail-closed behavior on every malformed input. Hypothesis shrinks any
failure to a minimal history.

Safety: no test here ever touches Discord, the executor, or the live data
dir — the mock boundary IS one of the enforced invariants.

Swarm entry point: ``python -m proptest.run`` (from ``scripts/``) — see
``proptest/README.md``.

Note: no pytest fixtures here on purpose — hypothesis's given + fixture
params break under some plugin stacks (pytest-asyncio), and the swarm runs
these locally as well as in CI. Each example mints its own temp state dir
inside the harness, so isolation holds without fixtures.
"""

from __future__ import annotations

import os
import shutil
import tempfile

from hypothesis import given, settings

from proptest import generators, harness

# CI-friendly default; the swarm runner (`python -m proptest.run`) uses 500.
settings.register_profile(
    "chip_ci",
    max_examples=int(os.environ.get("CHIP_PROTEST_EXAMPLES", "50")),
    deadline=None,
)
settings.load_profile("chip_ci")


def _tmp_root():
    return tempfile.mkdtemp(prefix="chip-proptest-")


@given(generators.scenarios())
def test_property_gate_pipeline_matches_reference(scenario):
    """For every generated history: real gates == reference model, exactly."""
    root = _tmp_root()
    try:
        harness.check_scenario(scenario, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


@given(generators.storm_scenarios())
def test_regression_w013_duplicate_storm_posts_once(storm):
    """Historical bug (commit 9ebf862): the fingerprint hashed the raw error
    text, so duplicate re-pushes of the SAME failing container (docker error
    strings embed varying IDs) each minted a new incident and the skill
    executed + posted repeatedly. Property: a duplicate storm over any
    container/class/timestamps yields EXACTLY ONE attempt and ONE message."""
    from proptest import reference

    root = _tmp_root()
    try:
        expected = reference.simulate(storm)
        real = harness.run_scenario(storm, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    assert real["statuses"] == expected["statuses"]  # belt: model equality
    assert real["statuses"][0] == "recovery-attempted"
    assert all(s == "incident-repeat-skipped" for s in real["statuses"][1:])
    assert len(real["executor_calls"]) == 1
    assert real["report_posts"] == 1


@given(generators.scenarios())
def test_regression_w013_mock_mode_never_reaches_live_channel(scenario):
    """Historical bug (commits 76b503a/adbe3e1): harness runs posted to the
    live Discord channel because the report path ignored state-dir/mock
    isolation. Property: under CHIP_CHAT_MOCK=1 with the DEFAULT report
    path, zero live-channel posts for every generated history."""
    root = _tmp_root()
    try:
        harness.check_mock_boundary(scenario, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


@given(generators.garbage_law_scenarios())
def test_property_malformed_law_fails_closed(scenario):
    """Unparsable law JSON => the skill treats itself as expired (fail-closed):
    disarmed receipts only, at most one disarm notice, zero executor calls."""
    root = _tmp_root()
    try:
        harness.check_scenario(scenario, root)
        real = harness.run_scenario(scenario, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    assert all(s in (None, "disarmed") for s in real["statuses"])
    assert real["executor_calls"] == []
    assert real["report_posts"] <= 1


@given(generators.malformed_alert_lists())
def test_property_malformed_alerts_never_execute(alerts):
    """Alerts without a container field / with junk keys or empty details are
    never ours: no receipt, no executor, no matter how permissive the law."""
    root = _tmp_root()
    try:
        harness.check_malformed_alerts(alerts, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


@given(generators.key_churn_scenarios())
def test_regression_w019_key_churn_yields_one_attempt_per_incident(scenario):
    """W-019 storm shape (2026-10-05 12:28-12:58): the producer re-keys the
    SAME container+class alert every cycle, so the W-013 fingerprint
    (which hashed the key) minted a fresh incident per cycle — eight posts,
    all 'attempt 1/3', cap evadable. Property: with key-INDEPENDENT identity,
    key churn yields exactly ONE attempt per incident per 24h window, silent
    skips for every re-keyed duplicate, and the cap binds per container."""
    from proptest import reference

    root = _tmp_root()
    try:
        harness.check_key_churn(scenario, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


@given(generators.fault_scenarios())
def test_regression_w019_executor_path_faults_fail_closed(scenario):
    """W-019 fail-closed law (2026-10-05: qwen ChipConfigError/RuntimeError
    faults announced 'restart proceeds per PM decision' and posted a fixture
    'executor restarted caddy' receipt that never reached the executor).
    Property: a PM, qwen, or executor fault NEVER proceeds to a verified
    restart; failed incidents stay open, retry paced by the backoff window,
    bounded by the per-container cap, and receipts name what did NOT happen."""
    root = _tmp_root()
    try:
        harness.check_faults_fail_closed(scenario, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
