"""Noise floor + runbook registry tests (Grok round-6 implementation)."""

from chip import monitor, runbooks


def _alert(key, detail="d", fp=None, **kw):
    a = {"key": key, "detail": detail, **kw}
    if fp:
        a["fingerprint"] = fp
    return a


def test_skip_key_never_actionable():
    v, _ = monitor.classify(_alert("plex", "down"), now=1000.0, state={}, persist=False)
    assert v == "skip-key"


def test_first_occurrence_actionable_then_dedup():
    st = {}
    v1, st = monitor.classify(_alert("qbittorrent", "unauth"), now=1000.0, state=st, persist=False)
    v2, st = monitor.classify(_alert("qbittorrent", "unauth"), now=1000.0 + 60, state=st, persist=False)
    assert v1 == "actionable" and v2 == "dedup"


def test_flap_after_threshold_in_window():
    st = {}
    verdicts = []
    for i in range(monitor.FLAP_THRESHOLD):
        v, st = monitor.classify(_alert("gw", "tunnel-down", fp="fp-flap"), now=1000.0 + i * 60, state=st, persist=False)
        verdicts.append(v)
    assert verdicts[0] == "actionable"
    assert verdicts[-1] == "flap"


def test_dead_letter_after_suppressions_then_human_reopen():
    st = {}
    v = "x"
    for i in range(12):  # 12 hits spaced 100s: 1 actionable + suppressions reach the dead cap
        v, st = monitor.classify(_alert("gw", "flake", fp="fp-dead"), now=1000.0 + i * 100, state=st, persist=False)
    assert v == "dead-letter"
    monitor.mark_human_reopen("fp-dead", state=st)
    v, st = monitor.classify(_alert("gw", "flake", fp="fp-dead"), now=1000.0 + 1300, state=st, persist=False)
    assert v != "dead-letter"  # human reopen clears the dead state (suppression restarts)


def test_fingerprint_stable_per_key_and_detail():
    assert monitor.fingerprint_for("gw", "down") == monitor.fingerprint_for("gw", "down")
    assert monitor.fingerprint_for("gw", "down") != monitor.fingerprint_for("gw", "up")


def test_runbook_classes_and_fail_closed():
    assert runbooks.may_autorun("restart-named-service") is True
    assert runbooks.may_autorun("remux") is False
    assert runbooks.may_autorun("plex-restart") is False  # blocked (SYSTEM AutoFix incident)
    assert runbooks.runbook_class("never-seen-before") == "propose"  # unknown fail-closed
    assert runbooks.may_autorun("never-seen-before") is False
