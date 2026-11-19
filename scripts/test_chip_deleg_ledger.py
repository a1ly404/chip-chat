"""Delegation ledger: hand-off follow-through, contradiction clarify, check-in, summary (no network)."""

from __future__ import annotations

from datetime import date

import pytest

from chip import deleg_ledger as dl
from chip import pm_summary

T0 = 1_800_000_000.0
ENV = {dl.FLAG: "1", dl.TZ_ENV: "America/Los_Angeles"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    for k in (dl.FLAG, dl.DEADLINE_ENV, dl.CHECKIN_ENV, dl.TZ_ENV):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def _open(ask="apply the pending VM security update", **kw):
    kw.setdefault("thread_id", "t1")
    return dl.open_or_attach("dev", "opsdev", ask, user="lyla", env=ENV, now=kw.pop("now", T0), **kw)


def test_mode_flag():
    assert dl.mode({}) == "off" and dl.mode({dl.FLAG: "shadow"}) == "shadow" and dl.mode({dl.FLAG: "1"}) == "live"


@pytest.mark.parametrize("text,want", [
    ("yeah i’m blocked on applying it because maintenance is windowed to 01:00-04:00 and we're outside it", "blocked"),
    ("got it. running the security update now. will drop the sha once they verify.", "progress"),
    ("Applied the update and verified the new kernel is booted.", "done"),
    ("I'm blocked by the window but running it now anyway", "contradictory"),
    ("claim: patched\nstatus: done\nevidence: tool:x\nnext: none", "done"),
    ("claim: x\nstatus: blocked\nevidence: none\nnext: need GO", "blocked"),
    ("I'll deploy it once it's verified", "progress"),
    ("hello there", "unclear"),
])
def test_classify(text, want):
    assert dl.classify(text) == want


def test_created_then_duplicate_suppressed():
    item, out = _open()
    assert out == "created" and item["state"] == dl.OPEN
    assert "Handed this to opsdev" in dl.ack_text(item, ENV) and "PST" in dl.ack_text(item, ENV)
    again, out2 = _open("Apply the pending VM security update now (asked again)", now=T0 + 60)
    assert out2 == "duplicate" and again["id"] == item["id"]
    assert dl.get(item["id"])["duplicates"] == 1


def test_similar_ask_other_thread_is_duplicate_within_deadline():
    item, _ = _open()
    _, out = _open("apply pending VM security update", thread_id="t2", now=T0 + 120)
    assert out == "duplicate"
    _, out3 = _open("something unrelated entirely different", thread_id="t2", now=T0 + 130)
    assert out3 == "created"


def test_done_reply_relays_outcome_and_checks_in_then_yes_closes():
    item, _ = _open()
    act = dl.on_dev_reply(item["id"], "Applied the update and verified the new kernel.", env=ENV, now=T0 + 30)
    assert act.kind == "outcome" and act.to == "user" and "Did that fix it?" in act.post and act.thread_id == "t1"
    assert dl.get(item["id"])["state"] == dl.CHECKIN
    yes = dl.on_user_reply("t1", "yes thanks", env=ENV, now=T0 + 90)
    assert yes.kind == "closed_yes" and dl.get(item["id"])["state"] == dl.CLOSED


def test_checkin_no_reopens_and_next_delegate_attaches():
    item, _ = _open()
    dl.on_dev_reply(item["id"], "Fixed and deployed.", env=ENV, now=T0 + 30)
    act = dl.on_user_reply("t1", "no, still broken", env=ENV, now=T0 + 60)
    assert act.kind == "reopened" and dl.get(item["id"])["state"] == dl.OPEN
    assert "NOT fixed" in dl.reopen_context(dl.get(item["id"]))
    again, out = _open("retry the update, user says still broken", now=T0 + 70)
    assert out == "attached" and again["id"] == item["id"]


def test_blocked_tells_user_whats_next():
    item, _ = _open()
    act = dl.on_dev_reply(item["id"], "I'm blocked: outside the maintenance window, need an explicit override.", env=ENV, now=T0 + 5)
    assert act.kind == "blocked" and "is blocked" in act.post and "Next:" in act.post
    assert dl.get(item["id"])["state"] == dl.WAITING_USER


def test_blocked_then_running_now_asks_dev_to_clarify_before_user():
    item, _ = _open()
    dl.on_dev_reply(item["id"], "I'm blocked: we're outside the window.", env=ENV, now=T0 + 5)
    act = dl.on_dev_reply(item["id"], "got it. running the security update now.", env=ENV, now=T0 + 6)
    assert act.kind == "clarify" and act.to == "dev"
    assert act.post.startswith("delegate:opsdev ") and f"(deleg {item['id']})" in act.post
    # The tagged clarify hop attaches to the same item (no new ack).
    same, out = dl.open_or_attach("dev", "opsdev", act.post[len("delegate:opsdev "):], env=ENV, now=T0 + 7)
    assert out == "attached" and same["id"] == item["id"]
    # A second contradiction goes to the user as "not done".
    act2 = dl.on_dev_reply(item["id"], "blocked but running it now", env=ENV, now=T0 + 8)
    assert act2.kind == "mixed" and "not done" in act2.post


def test_user_turn_between_blocked_and_running_is_not_a_contradiction():
    item, _ = _open()
    dl.on_dev_reply(item["id"], "I'm blocked: outside the window, need an override.", env=ENV, now=T0 + 5)
    dl.on_user_reply("t1", "override granted, go ahead", env=ENV, now=T0 + 10)
    again, out = _open("apply it now, override granted", now=T0 + 11)
    assert out == "attached"
    act = dl.on_dev_reply(item["id"], "running the update now", env=ENV, now=T0 + 12)
    assert act.kind == "progress"


def test_deadline_nudge_once_then_escalate_at_2x():
    item, _ = _open()
    assert dl.tick(env=ENV, now=T0 + 29 * 60) == []
    acts = dl.tick(env=ENV, now=T0 + 30 * 60)
    assert [a.kind for a in acts] == ["nudge"] and acts[0].to == "dev" and acts[0].post.startswith("delegate:opsdev")
    assert dl.tick(env=ENV, now=T0 + 45 * 60) == []
    esc = dl.tick(env=ENV, now=T0 + 60 * 60)
    assert [a.kind for a in esc] == ["escalate"] and "not done" in esc[0].post and "Next:" in esc[0].post
    assert dl.get(item["id"])["state"] == dl.WAITING_USER
    assert dl.tick(env=ENV, now=T0 + 600 * 60) == []


def test_progress_reply_restarts_deadline():
    item, _ = _open()
    act = dl.on_dev_reply(item["id"], "on it, starting the job", env=ENV, now=T0 + 20 * 60)
    assert act.kind == "progress" and "check back by" in act.post
    assert dl.tick(env=ENV, now=T0 + 31 * 60) == []
    assert [a.kind for a in dl.tick(env=ENV, now=T0 + 51 * 60)] == ["nudge"]


def test_force_due_operator_tool():
    item, _ = _open()
    acts = dl.tick(env=ENV, now=T0 + 60, force_due=item["id"])
    assert [a.kind for a in acts] == ["nudge"]
    assert [a.kind for a in dl.tick(env=ENV, now=T0 + 120, force_due=item["id"])] == ["escalate"]


def test_checkin_times_out_quietly_and_shows_in_summary():
    item, _ = _open()
    dl.on_dev_reply(item["id"], "Done, deployed.", env=ENV, now=T0 + 10)
    acts = dl.tick(env=ENV, now=T0 + 10 + 24 * 3600)
    assert [a.kind for a in acts] == ["quiet_close"] and acts[0].post == ""
    s = dl.summary(T0, T0 + 2 * 86400, env=ENV, now=T0 + 25 * 3600)
    assert s["closed_quiet"] and "closed quietly" in dl.summary_lines(s)[0]


def test_yes_no_parser():
    assert dl.parse_yes_no("yes!") == "yes" and dl.parse_yes_no("yep works now") == "yes"
    assert dl.parse_yes_no("thanks but it's still broken") == "no" and dl.parse_yes_no("nope") == "no"
    assert dl.parse_yes_no("what about the other thing?") is None


def test_decisions_carry_and_decide():
    rows = dl.track_decisions(["approve running the re-auth flow (job pick-1)"], now=T0)
    assert len(rows) == 1 and rows[0]["id"].startswith("k-")
    again = dl.track_decisions([], now=T0 + 86400)
    assert again and again[0]["id"] == rows[0]["id"]
    assert dl.decide(rows[0]["id"], now=T0 + 90000)
    assert dl.track_decisions([], now=T0 + 90001) == []


def test_pm_summary_lists_open_delegations_and_carried_decisions(monkeypatch):
    env = {"CHIP_PM_SUMMARY_TZ": "UTC", dl.FLAG: "1"}
    dl.track_decisions(["approve the re-auth flow"], now=T0 - 3 * 86400)
    _open()
    day = date.fromtimestamp(T0)
    monkeypatch.setattr(pm_summary.pm_fixes, "collect", lambda *a, **k: {})
    s = pm_summary.build(day, env=env, jobs=[])
    assert s["delegations"]["live"] and any("still waiting since" in w for w in s["waiting"])
    text = pm_summary.render(s)
    assert "**Open delegations**" in text and "opsdev" in text


def test_pm_summary_unchanged_when_ledger_off(monkeypatch):
    monkeypatch.setattr(pm_summary.pm_fixes, "collect", lambda *a, **k: {})
    s = pm_summary.build(date.fromtimestamp(T0), env={"CHIP_PM_SUMMARY_TZ": "UTC"}, jobs=[])
    assert s["delegations"] == {} and "Open delegations" not in pm_summary.render(s)
