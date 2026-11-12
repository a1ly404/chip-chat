"""Dev-bot PR pipeline (chip.dev_pr): assign → policy job → PR → independent review → CI gate → merge.

Attack matrix: dev bot assigning itself, self-review, reviewer swap, submit by a non-author, submit
without / with a failed / with someone else's job receipt, review of a stale sha, merge gate with
CI not green, head moved after approval, gate or merge record from inside a job, repo not on the
allowlist, merge recorded without (or with a stale) gate. Offline; real policy_exec receipts.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from chip import dev_pr as dp, job_policy as jp, policy_exec as px
from test_chip_policy_exec import NOW, FakeRunner, req, setup  # noqa: F401  (fixture reuse)

REPO = "acme/widgets"
SHA1 = "1" * 40
SHA2 = "2" * 40
MERGED = "3" * 40
AUTHOR = "dev:alpha"
REVIEWER = "dev:beta"


@pytest.fixture
def env(setup, monkeypatch):  # noqa: F811
    e = {**setup["env"], dp.REPOS_ENV: f"{REPO}, other/repo"}
    monkeypatch.setenv(dp.REPOS_ENV, e[dp.REPOS_ENV])
    return e


def job_receipt(env, requested_by=AUTHOR, **res) -> str:
    out = px.run_dev_job(req(requested_by=requested_by, job_id="j-" + requested_by.split(":")[1]),
                         pm_reason="dev pr work", env=env, now=NOW, runner=FakeRunner(**res))
    assert out["verdict"] == "allow"
    return out["receipt"]["decision_id"]


def assigned(env, **kw):
    args = {"ticket": "acme/widgets#7", "repo": REPO, "author": AUTHOR, "reviewer": REVIEWER, "actor": jp.PM,
            "reason": "small fix", "env": env, "now": NOW}
    args.update(kw)
    return dp.assign(**args)


def submitted(env):
    it = assigned(env)
    dec = job_receipt(env)
    return dp.submit(it["id"], actor=AUTHOR, pr=12, head_sha=SHA1, decision_id=dec, env=env, now=NOW)


def approved(env):
    it = submitted(env)
    return dp.review(it["id"], actor=REVIEWER, verdict="approve", head_sha=SHA1, env=env, now=NOW)


# ------------------------------------------------------------------ happy path


def test_full_pipeline_end_to_end(env):
    it = approved(env)
    assert it["state"] == "approved" and it["approved_by"] == REVIEWER
    g = dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA1, ci="success", env=env, now=NOW)
    assert g["verdict"] == "allow" and g["gate_id"].startswith("gate-") and g["reasons"] == []
    done = dp.record_merged(it["id"], actor=jp.PM, gate_id=g["gate_id"], merged_sha=MERGED, env=env, now=NOW)
    assert done["state"] == "merged" and done["merged_sha"] == MERGED
    events = [h["event"] for h in done["history"]]
    assert events == ["assign", "submit", "review", "gate", "merged"]
    rows = jp._jsonl(dp.ledger_path())
    assert len(rows) == 5 and all("ts" in r for r in rows)


def test_changes_requested_then_resubmit_and_approve(env):
    it = submitted(env)
    it = dp.review(it["id"], actor=REVIEWER, verdict="changes", head_sha=SHA1, note="add test", env=env, now=NOW)
    assert it["state"] == "changes_requested"
    dec = job_receipt(env)
    it = dp.submit(it["id"], actor=AUTHOR, pr=12, head_sha=SHA2, decision_id=dec, env=env, now=NOW)
    it = dp.review(it["id"], actor=REVIEWER, verdict="approve", head_sha=SHA2, env=env, now=NOW)
    assert it["state"] == "approved" and it["approved_sha"] == SHA2


def test_list_and_show(env):
    it = assigned(env)
    assert [i["id"] for i in dp.items(state="assigned")] == [it["id"]]
    assert dp.items(state="merged") == []
    with pytest.raises(dp.DevPrRefused):
        dp.show("devpr-000000000000")


# ------------------------------------------------------------------ assign rules


@pytest.mark.parametrize("actor", [AUTHOR, REVIEWER, "operator", "dev:pm", ""])
def test_only_pm_assigns(env, actor):
    with pytest.raises(dp.DevPrRefused, match="only the PM"):
        assigned(env, actor=actor)


def test_assign_refused_inside_job(env, monkeypatch):
    monkeypatch.setenv("CHIP_BGJOB_ID", "j9")
    with pytest.raises(dp.DevPrRefused, match="inside a job"):
        assigned(env)


def test_assign_refused_inside_job_via_passed_env(env):
    with pytest.raises(dp.DevPrRefused, match="inside a job"):
        assigned({**env, "CHIP_JOB_ACTOR": AUTHOR})


def test_reviewer_must_differ_from_author(env):
    with pytest.raises(dp.DevPrRefused, match="different bot"):
        assigned(env, reviewer="DEV:Alpha")


@pytest.mark.parametrize("field,val", [("author", "pm"), ("reviewer", "operator"), ("author", "alpha"),
                                       ("reviewer", "dev:")])
def test_author_and_reviewer_must_be_dev_bots(env, field, val):
    with pytest.raises(dp.DevPrRefused, match="dev bot"):
        assigned(env, **{field: val})


@pytest.mark.parametrize("repo", ["evil/repo", "acme/widgets/../x", "", "acme"])
def test_repo_allowlist(env, repo):
    with pytest.raises(dp.DevPrRefused, match="allowlist"):
        assigned(env, repo=repo)


def test_empty_allowlist_refuses_everything(env):
    with pytest.raises(dp.DevPrRefused, match="allowlist"):
        assigned({**env, dp.REPOS_ENV: ""})


def test_repo_match_is_case_insensitive(env):
    assert assigned(env, repo="ACME/Widgets")["repo"] == REPO


def test_policy_off_refuses(env):
    with pytest.raises(dp.DevPrRefused, match="disabled"):
        assigned({**env, jp.FLAG: "0"})


def test_duplicate_open_ticket_refused(env):
    assigned(env)
    with pytest.raises(dp.DevPrRefused, match="already has an open item"):
        assigned(env)


def test_ticket_reusable_after_close(env):
    it = assigned(env)
    dp.close(it["id"], actor=jp.PM, reason="dup", env=env, now=NOW)
    assert assigned(env)["state"] == "assigned"


def test_malformed_ticket(env):
    with pytest.raises(dp.DevPrRefused, match="ticket"):
        assigned(env, ticket="bad ticket; rm -rf")


# ------------------------------------------------------------------ submit rules


def test_submit_by_non_author_refused(env):
    it = assigned(env)
    dec = job_receipt(env)
    for who in (REVIEWER, jp.PM, "dev:gamma"):
        with pytest.raises(dp.DevPrRefused, match="assigned author"):
            dp.submit(it["id"], actor=who, pr=1, head_sha=SHA1, decision_id=dec, env=env, now=NOW)


def test_submit_needs_a_real_receipt(env):
    it = assigned(env)
    with pytest.raises(dp.DevPrRefused, match="no executor receipt"):
        dp.submit(it["id"], actor=AUTHOR, pr=1, head_sha=SHA1, decision_id="dec-forged0000000", env=env, now=NOW)
    with pytest.raises(dp.DevPrRefused, match="no executor receipt"):
        dp.submit(it["id"], actor=AUTHOR, pr=1, head_sha=SHA1, decision_id="", env=env, now=NOW)


def test_submit_refuses_failed_job(env):
    it = assigned(env)
    dec = job_receipt(env, exit=1)
    with pytest.raises(dp.DevPrRefused, match="did not succeed"):
        dp.submit(it["id"], actor=AUTHOR, pr=1, head_sha=SHA1, decision_id=dec, env=env, now=NOW)


def test_submit_refuses_someone_elses_job(env):
    it = assigned(env)
    dec = job_receipt(env, requested_by="dev:gamma")
    with pytest.raises(dp.DevPrRefused, match="not requested by the assigned author"):
        dp.submit(it["id"], actor=AUTHOR, pr=1, head_sha=SHA1, decision_id=dec, env=env, now=NOW)


@pytest.mark.parametrize("pr,sha", [(0, SHA1), (-3, SHA1), ("x", SHA1), (1, "abc123"), (1, "G" * 40)])
def test_submit_validates_pr_and_sha(env, pr, sha):
    it = assigned(env)
    dec = job_receipt(env)
    with pytest.raises(dp.DevPrRefused):
        dp.submit(it["id"], actor=AUTHOR, pr=pr, head_sha=sha, decision_id=dec, env=env, now=NOW)


def test_submit_twice_without_review_refused(env):
    it = submitted(env)
    dec = job_receipt(env)
    with pytest.raises(dp.DevPrRefused, match="cannot submit"):
        dp.submit(it["id"], actor=AUTHOR, pr=12, head_sha=SHA2, decision_id=dec, env=env, now=NOW)


# ------------------------------------------------------------------ review rules


def test_author_cannot_self_review(env):
    it = submitted(env)
    with pytest.raises(dp.DevPrRefused, match="own change"):
        dp.review(it["id"], actor=AUTHOR, verdict="approve", head_sha=SHA1, env=env, now=NOW)


@pytest.mark.parametrize("who", ["dev:gamma", jp.PM, "operator"])
def test_only_assigned_reviewer_reviews(env, who):
    it = submitted(env)
    with pytest.raises(dp.DevPrRefused, match="assigned reviewer"):
        dp.review(it["id"], actor=who, verdict="approve", head_sha=SHA1, env=env, now=NOW)


def test_review_binds_to_submitted_sha(env):
    it = submitted(env)
    with pytest.raises(dp.DevPrRefused, match="does not match"):
        dp.review(it["id"], actor=REVIEWER, verdict="approve", head_sha=SHA2, env=env, now=NOW)


def test_review_before_submit_refused(env):
    it = assigned(env)
    with pytest.raises(dp.DevPrRefused, match="cannot review"):
        dp.review(it["id"], actor=REVIEWER, verdict="approve", head_sha=SHA1, env=env, now=NOW)


def test_bad_verdict(env):
    it = submitted(env)
    with pytest.raises(dp.DevPrRefused, match="verdict"):
        dp.review(it["id"], actor=REVIEWER, verdict="lgtm", head_sha=SHA1, env=env, now=NOW)


# ------------------------------------------------------------------ merge gate


@pytest.mark.parametrize("ci", ["failure", "pending", "", "SUCCESS ", "neutral", None])
def test_gate_needs_ci_success(env, ci):
    it = approved(env)
    g = dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA1, ci=ci or "", env=env, now=NOW)
    if str(ci or "").strip().lower() == "success":
        assert g["verdict"] == "allow"
    else:
        assert g["verdict"] == "deny" and any("CI" in r for r in g["reasons"]) and g["gate_id"] == ""


def test_gate_denies_when_head_moved_after_approval(env):
    it = approved(env)
    g = dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA2, ci="success", env=env, now=NOW)
    assert g["verdict"] == "deny" and any("moved" in r for r in g["reasons"])


def test_gate_denies_without_approval(env):
    it = submitted(env)
    g = dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA1, ci="success", env=env, now=NOW)
    assert g["verdict"] == "deny" and any("not approved" in r for r in g["reasons"])
    it = dp.review(it["id"], actor=REVIEWER, verdict="changes", head_sha=SHA1, env=env, now=NOW)
    g = dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA1, ci="success", env=env, now=NOW)
    assert g["verdict"] == "deny"


@pytest.mark.parametrize("actor", [AUTHOR, REVIEWER, "operator"])
def test_gate_pm_only(env, actor):
    it = approved(env)
    with pytest.raises(dp.DevPrRefused, match="only the PM"):
        dp.merge_gate(it["id"], actor=actor, head_sha=SHA1, ci="success", env=env, now=NOW)


def test_gate_refused_inside_job(env, monkeypatch):
    it = approved(env)
    monkeypatch.setenv("CHIP_JOB_ACTOR", AUTHOR)
    with pytest.raises(dp.DevPrRefused, match="inside a job"):
        dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA1, ci="success", env=env, now=NOW)


def test_deny_gates_are_logged(env):
    it = approved(env)
    dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA1, ci="failure", env=env, now=NOW)
    rows = [r for r in jp._jsonl(dp.ledger_path()) if r["event"] == "gate"]
    assert rows and rows[-1]["verdict"] == "deny" and rows[-1]["reasons"]


def test_push_after_approval_reopens_review(env):
    """A resubmit (new head) after approval is not possible without a review round: state stays approved
    only for the approved sha; the gate refuses any other head."""
    it = approved(env)
    with pytest.raises(dp.DevPrRefused, match="cannot submit"):
        dp.submit(it["id"], actor=AUTHOR, pr=12, head_sha=SHA2, decision_id=job_receipt(env), env=env, now=NOW)


# ------------------------------------------------------------------ merge record


def test_merge_without_gate_refused(env):
    it = approved(env)
    with pytest.raises(dp.DevPrRefused, match="no matching allow gate"):
        dp.record_merged(it["id"], actor=jp.PM, gate_id="gate-000000000000", merged_sha=MERGED, env=env, now=NOW)


def test_merge_with_stale_gate_refused(env):
    it = approved(env)
    g1 = dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA1, ci="success", env=env, now=NOW)
    g2 = dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA1, ci="success", env=env, now=NOW + timedelta(seconds=5))
    assert g1["gate_id"] != g2["gate_id"]
    assert g2["verdict"] == "allow"
    with pytest.raises(dp.DevPrRefused):
        dp.record_merged(it["id"], actor=jp.PM, gate_id=g1["gate_id"], merged_sha=MERGED, env=env, now=NOW)
    assert dp.record_merged(it["id"], actor=jp.PM, gate_id=g2["gate_id"], merged_sha=MERGED, env=env,
                            now=NOW)["state"] == "merged"


def test_later_deny_revokes_allow(env):
    it = approved(env)
    g = dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA1, ci="success", env=env, now=NOW)
    d = dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA1, ci="failure", env=env, now=NOW)
    assert d["verdict"] == "deny" and dp.show(it["id"])["state"] == "approved"
    with pytest.raises(dp.DevPrRefused, match="no matching allow gate"):
        dp.record_merged(it["id"], actor=jp.PM, gate_id=g["gate_id"], merged_sha=MERGED, env=env, now=NOW)


def test_no_gate_after_merge(env):
    it = approved(env)
    g = dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA1, ci="success", env=env, now=NOW)
    dp.record_merged(it["id"], actor=jp.PM, gate_id=g["gate_id"], merged_sha=MERGED, env=env, now=NOW)
    assert dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA1, ci="success", env=env, now=NOW)["verdict"] == "deny"
    assert dp.show(it["id"])["state"] == "merged"


def test_merge_record_pm_only_and_not_in_job(env, monkeypatch):
    it = approved(env)
    g = dp.merge_gate(it["id"], actor=jp.PM, head_sha=SHA1, ci="success", env=env, now=NOW)
    with pytest.raises(dp.DevPrRefused, match="only the PM"):
        dp.record_merged(it["id"], actor=AUTHOR, gate_id=g["gate_id"], merged_sha=MERGED, env=env, now=NOW)
    monkeypatch.setenv("CHIP_BGJOB_ID", "j1")
    with pytest.raises(dp.DevPrRefused, match="inside a job"):
        dp.record_merged(it["id"], actor=jp.PM, gate_id=g["gate_id"], merged_sha=MERGED, env=env, now=NOW)


def test_closed_item_is_final(env):
    it = assigned(env)
    dp.close(it["id"], actor=jp.PM, reason="abandoned", env=env, now=NOW)
    with pytest.raises(dp.DevPrRefused, match="already"):
        dp.close(it["id"], actor=jp.PM, reason="again", env=env, now=NOW)
    with pytest.raises(dp.DevPrRefused, match="cannot submit"):
        dp.submit(it["id"], actor=AUTHOR, pr=1, head_sha=SHA1, decision_id=job_receipt(env), env=env, now=NOW)


def test_malformed_item_id(env):
    with pytest.raises(dp.DevPrRefused, match="malformed"):
        dp.review("../../etc", actor=REVIEWER, verdict="approve", head_sha=SHA1, env=env, now=NOW)


# ------------------------------------------------------------------ CLI


def test_cli_flow_and_exit_codes(env, capsys):
    assert dp.main(["assign", "--ticket", "acme/widgets#9", "--repo", REPO, "--author", AUTHOR,
                    "--reviewer", REVIEWER]) == 0
    iid = json.loads(capsys.readouterr().out)["id"]
    dec = job_receipt(env)
    assert dp.main(["submit", iid, "--actor", AUTHOR, "--pr", "3", "--head-sha", SHA1, "--decision-id", dec]) == 0
    capsys.readouterr()
    assert dp.main(["review", iid, "--actor", AUTHOR, "--verdict", "approve", "--head-sha", SHA1]) == 2
    assert "own change" in json.loads(capsys.readouterr().out)["refused"]
    assert dp.main(["review", iid, "--actor", REVIEWER, "--verdict", "approve", "--head-sha", SHA1]) == 0
    capsys.readouterr()
    assert dp.main(["gate", iid, "--head-sha", SHA1, "--ci", "failure"]) == 2
    capsys.readouterr()
    assert dp.main(["gate", iid, "--head-sha", SHA1, "--ci", "success"]) == 0
    gate = json.loads(capsys.readouterr().out)["gate_id"]
    assert dp.main(["merged", iid, "--gate-id", gate, "--merged-sha", MERGED]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "merged"
    assert dp.main(["list", "--state", "merged"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert dp.main(["bogus"]) == 64
