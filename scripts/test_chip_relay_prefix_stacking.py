"""Prefix-stacking regression: stacked [persona] markers must not reach Discord."""

from chip_relay.dispatch import strip_stacked_persona_prefixes


def test_collapses_repeated_persona_markers():
    assert (
        strip_stacked_persona_prefixes("[buffrdev] [buffrdev] [buffrdev] [buffrdev] hop-ok-buffr")
        == "hop-ok-buffr"
    )


def test_collapses_mixed_marker_run():
    assert strip_stacked_persona_prefixes("[pm] [pm] inject-infisical-ok") == "inject-infisical-ok"


def test_single_marker_is_preserved():
    assert (
        strip_stacked_persona_prefixes("[mock Chip] Echo OK — received 16 chars.")
        == "[mock Chip] Echo OK — received 16 chars."
    )


def test_plain_reply_untouched():
    assert strip_stacked_persona_prefixes("hop-ok") == "hop-ok"


def test_leading_bracket_single_token_untouched():
    assert strip_stacked_persona_prefixes("[TODO] tighten the cap") == "[TODO] tighten the cap"


def test_stripped_empty_reply_still_flags():
    # A reply that is ONLY stacked markers collapses to empty and must fail the
    # postable-reply check rather than posting blank content.
    assert strip_stacked_persona_prefixes("[dev] [dev] [dev]") == ""


def test_case_insensitive_stack():
    assert strip_stacked_persona_prefixes("[BuffrDev] [BUFFRDEV] hop-ok-buffr") == "hop-ok-buffr"
