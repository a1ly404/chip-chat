"""done_gate tests (ticket-gated Done enforcement; golden R1/R13)."""

from chip.done_gate import may_flip_done, parse_done_state, reopen_required

GOOD = """## Acceptance

- [x] Health probe returns 200
verified-as-of: 2026-09-27 03:10 — container health+BUILD_SHA ok
- [x] pytest green
verified-as-of: 2026-09-27 03:20 — 241 passed

## Notes
text
"""

BAD_MISSING = """## Acceptance

- [x] Health probe returns 200
(item missing its verified-as-of binding)
- [x] pytest green
"""

BAD_EMPTY = """## Done

no boxes at all, just vibes
"""


def test_good_state_passes():
    scan = parse_done_state(GOOD)
    assert scan.premature_done is False
    assert [b.verified for b in scan.boxes] == [True, True]
    ok, reason = may_flip_done(GOOD)
    assert ok and "verified-as-of" in reason


def test_checked_without_stamp_is_premature():
    scan = parse_done_state(BAD_MISSING)
    assert scan.premature_done is True
    assert any("verified-as-of" in r for r in scan.reasons)
    ok, reason = may_flip_done(BAD_MISSING)
    assert not ok


def test_done_without_boxes_is_premature():
    scan = parse_done_state(BAD_EMPTY)
    assert scan.premature_done is True
    assert any("zero acceptance boxes" in r for r in scan.reasons)


def test_reopen_required_watchdog_semantics():
    req_open, _ = reopen_required(BAD_MISSING)
    assert req_open is True
    req_open2, _ = reopen_required(GOOD)
    assert req_open2 is False


def test_verify_stamp_requires_full_datetime_not_bare_word():
    weak = """## Acceptance
- [x] thing done — verified, trust me
"""
    scan = parse_done_state(weak)
    assert scan.premature_done is True  # bare "verified" is not machine-checkable (R1)


def test_done_with_only_unchecked_boxes_is_premature():
    only_open = """## Acceptance

- [ ] not finished yet
"""
    ok, reason = may_flip_done(only_open)
    assert not ok
    assert "no checked" in reason


def test_stale_relative_time_rejected():
    weak = """## Acceptance
- [x] thing
verified-as-of: yesterday — not machine-checkable
"""
    scan = parse_done_state(weak)
    assert scan.premature_done is True
