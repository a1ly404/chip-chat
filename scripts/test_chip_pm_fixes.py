"""PM daily summary "Fixes CC made" section (chip.pm_fixes; fakes only, no network)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from chip import dev_pr, job_policy, pm_fixes, pm_summary, policy_exec

DAY = date(2026, 3, 8)
TZ = {"CHIP_PM_SUMMARY_TZ": "America/Los_Angeles"}
START = datetime(2026, 3, 8, 8, 0, tzinfo=timezone.utc)  # local midnight PST
END = datetime(2026, 3, 9, 7, 0, tzinfo=timezone.utc)  # local midnight PDT (DST day, 23h)
IN = "2026-03-08T18:00:00Z"
OUT = "2026-03-07T18:00:00Z"
REPO = "acme/widgets"
FAKE_TOKEN = "tok-" + "abc123"  # built at runtime so scanners don't flag a literal


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    for k in (pm_fixes.REPOS_ENV, pm_fixes.LABEL_ENV, pm_fixes.PREFIX_ENV, pm_fixes.JOB_TYPES_ENV,
              pm_fixes.API_ENV, pm_fixes.TOKEN_FROM_ENV, "CHIP_PM_SUMMARY_TZ"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def _rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _audit(job_id: str, *, context: str = "alert:container-down:web", job_type: str = "container-heal") -> dict:
    return {"ts": IN, "job_id": job_id, "verdict": "allow", "job_type": job_type, "action": "restart-container",
            "target": "web", "context": context, "requested_by": "dev:a"}


def _receipt(job_id: str, *, ts: str = IN, status: str = "ok") -> dict:
    return {"ts": ts, "job_id": job_id, "decision_id": f"dec-{job_id}", "status": status, "sudo": True}


class FakeForge:
    def __init__(self, issues=None, pulls=None, status: int = 200, boom: Exception | None = None) -> None:
        self.issues, self.pulls, self.status, self.boom = issues or [], pulls or [], status, boom
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers):
        self.calls.append((url, dict(headers)))
        if self.boom:
            raise self.boom
        if self.status != 200:
            return self.status, None
        return 200, (self.issues if "/issues?" in url else self.pulls)


def _pr(n: int, *, merged: str | None = IN, ref: str = "feat/x", labels=(), body: str = "", title: str = "t") -> dict:
    return {"number": n, "merged_at": merged, "head": {"ref": ref}, "labels": [{"name": x} for x in labels],
            "body": body, "title": title, "html_url": f"https://github.com/{REPO}/pull/{n}",
            "merge_commit_sha": "abcdef1234567"}


def _issue(n: int, *, closed: str = IN, pr: bool = False) -> dict:
    row = {"number": n, "closed_at": closed, "title": f"bug {n}", "html_url": f"https://github.com/{REPO}/issues/{n}"}
    if pr:
        row["pull_request"] = {}
    return row


# ------------------------------------------------------------------ receipts


def test_receipt_fixes_only_ok_alert_remediations_in_window() -> None:
    _rows(job_policy.audit_path(), [_audit("j1"), _audit("j2"), _audit("j3", context="routine:daily"),
                                    _audit("j4"), _audit("j5")])
    _rows(policy_exec.receipts_path(), [_receipt("j1"), _receipt("j2", status="refused"), _receipt("j3"),
                                        _receipt("j4", ts=OUT), _receipt("j-unknown")])
    got = pm_fixes.receipt_fixes(START, END, {})
    assert [g["job_id"] for g in got] == ["j1"]
    assert got[0]["decision_id"] == "dec-j1" and got[0]["target"] == "web" and got[0]["sudo"] is True


def test_receipt_fixes_listed_job_types_count() -> None:
    _rows(job_policy.audit_path(), [_audit("j3", context="routine:daily", job_type="disk-cleanup")])
    _rows(policy_exec.receipts_path(), [_receipt("j3")])
    assert pm_fixes.receipt_fixes(START, END, {}) == []
    got = pm_fixes.receipt_fixes(START, END, {pm_fixes.JOB_TYPES_ENV: "x, disk-cleanup"})
    assert [g["job_id"] for g in got] == ["j3"]


# ------------------------------------------------------------------ dev_pr ledger


def _devpr(iid: str, merged_ts: str, *, pr: int = 7) -> list[dict]:
    return [
        {"id": iid, "event": "assign", "actor": "pm", "ticket": "#12", "repo": REPO, "author": "dev:a",
         "reviewer": "dev:b", "ts": OUT},
        {"id": iid, "event": "submit", "actor": "dev:a", "pr": pr, "head_sha": "h1", "ts": OUT},
        {"id": iid, "event": "review", "actor": "dev:b", "verdict": "approve", "head_sha": "h1", "ts": OUT},
        {"id": iid, "event": "merged", "actor": "pm", "merged_sha": "m" * 40, "ts": merged_ts},
    ]


def test_devpr_fixes_merged_in_window() -> None:
    _rows(dev_pr.ledger_path(), _devpr("devpr-1", IN) + _devpr("devpr-2", OUT, pr=8))
    got = pm_fixes.devpr_fixes(START, END)
    assert len(got) == 1 and got[0]["url"] == f"https://github.com/{REPO}/pull/7"
    assert got[0]["author"] == "dev:a" and got[0]["reviewer"] == "dev:b" and got[0]["merged_sha"] == "m" * 12


# ------------------------------------------------------------------ forge


def test_forge_off_by_default_makes_no_calls() -> None:
    f = FakeForge()
    assert pm_fixes.forge_fixes(START, END, {}, f) == ([], [])
    assert f.calls == []


def test_forge_rejects_malformed_repo_names() -> None:
    assert pm_fixes.repos({pm_fixes.REPOS_ENV: "ok/repo, bad, ../x/y/z, a/b?c"}) == ["ok/repo"]


def test_forge_closed_issues_and_fix_prs() -> None:
    f = FakeForge(
        issues=[_issue(5), _issue(6, closed=OUT), _issue(9, pr=True)],
        pulls=[
            _pr(10, ref="cc-fix/5-thing"),            # branch prefix
            _pr(11, labels=["cc-fix"]),               # label
            _pr(12, body="Fixes #6"),                 # closes a fix issue (closed earlier, still fix-labelled)
            _pr(13, body="Fixes #99"),                # not a fix issue
            _pr(14, ref="feat/other"),                # plain feature
            _pr(15, merged=None, ref="fix/x"),        # closed unmerged
            _pr(16, merged=OUT, ref="fix/y"),         # merged outside window
        ],
    )
    items, errors = pm_fixes.forge_fixes(START, END, {pm_fixes.REPOS_ENV: REPO}, f)
    assert errors == []
    assert [(i["kind"], i["number"]) for i in items] == [("issue", 5), ("pr", 10), ("pr", 11), ("pr", 12)]
    assert {i["why"] for i in items if i["kind"] == "pr"} == {"branch", "label", "closes"}
    issues_url = f.calls[0][0]
    assert "labels=cc-fix" in issues_url and "state=closed" in issues_url and "since=2026-03-08T08%3A00%3A00Z" in issues_url
    assert "Authorization" not in f.calls[0][1]


def test_forge_custom_label_and_prefixes() -> None:
    f = FakeForge(pulls=[_pr(10, ref="fix/a"), _pr(11, ref="hotfix/b"), _pr(12, labels=["bugfix"])])
    env = {pm_fixes.REPOS_ENV: REPO, pm_fixes.LABEL_ENV: "bugfix", pm_fixes.PREFIX_ENV: "hotfix/"}
    items, _ = pm_fixes.forge_fixes(START, END, env, f)
    assert [i["number"] for i in items] == [11, 12]
    assert "labels=bugfix" in f.calls[0][0]


def test_forge_token_only_via_named_token_var_and_never_returned() -> None:
    f = FakeForge(pulls=[_pr(10, ref="fix/a")])
    env = {pm_fixes.REPOS_ENV: REPO, pm_fixes.TOKEN_FROM_ENV: "MY_FORGE_TOKEN", "MY_FORGE_TOKEN": FAKE_TOKEN}
    items, errors = pm_fixes.forge_fixes(START, END, env, f)
    assert f.calls[0][1]["Authorization"] == f"Bearer {FAKE_TOKEN}"
    assert FAKE_TOKEN not in json.dumps([items, errors])
    # a TOKEN_FROM pointing at a non-token var is ignored
    f2 = FakeForge()
    pm_fixes.forge_fixes(START, END, {pm_fixes.REPOS_ENV: REPO, pm_fixes.TOKEN_FROM_ENV: "HOME", "HOME": "x"}, f2)
    assert "Authorization" not in f2.calls[0][1]


def test_forge_errors_are_short_and_non_fatal() -> None:
    items, errors = pm_fixes.forge_fixes(START, END, {pm_fixes.REPOS_ENV: REPO}, FakeForge(status=404))
    assert items == [] and errors == [f"{REPO} issues: HTTP 404", f"{REPO} pulls: HTTP 404"]
    items, errors = pm_fixes.forge_fixes(START, END, {pm_fixes.REPOS_ENV: REPO}, FakeForge(boom=TimeoutError("x")))
    assert items == [] and errors[0] == f"{REPO} issues: TimeoutError"


# ------------------------------------------------------------------ collect + summary


def test_collect_dedupes_devpr_and_forge_pr_by_url() -> None:
    _rows(dev_pr.ledger_path(), _devpr("devpr-1", IN, pr=10))
    f = FakeForge(pulls=[_pr(10, ref="fix/a")])
    got = pm_fixes.collect(START, END, env={pm_fixes.REPOS_ENV: REPO}, http=f)
    assert [i["kind"] for i in got["items"]] == ["devpr"]


def test_summary_has_fixes_section_with_links() -> None:
    _rows(job_policy.audit_path(), [_audit("j1")])
    _rows(policy_exec.receipts_path(), [_receipt("j1")])
    f = FakeForge(issues=[_issue(5)], pulls=[_pr(10, ref="cc-fix/5", title="fix the thing")])
    s = pm_summary.build(DAY, env={**TZ, pm_fixes.REPOS_ENV: REPO}, jobs=[], forge_http=f)
    assert len(s["fixes"]["items"]) == 3
    text = pm_summary.render(s)
    sect = text.split("**Fixes CC made**", 1)[1]
    assert f"https://github.com/{REPO}/pull/10" in sect and "`abcdef1`" in sect and "fix the thing" in sect
    assert f"https://github.com/{REPO}/issues/5" in sect
    assert "`j1` container-heal: restart-container on web ok (receipt dec-j1)" in sect
    # comes after "Waiting on you" so a long fixes list can never clip the decisions
    assert text.index("**Waiting on you**") < text.index("**Fixes CC made**")


def test_summary_quiet_day_fixes_none_and_forge_error_line() -> None:
    text = pm_summary.render(pm_summary.build(DAY, env=TZ, jobs=[]))
    assert "**Fixes CC made**\n- none" in text
    s = pm_summary.build(DAY, env={**TZ, pm_fixes.REPOS_ENV: REPO}, jobs=[], forge_http=FakeForge(status=500))
    assert "forge unavailable: acme/widgets issues: HTTP 500" in pm_summary.render(s)


def test_summary_without_forge_http_and_no_repos_does_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_net(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(pm_fixes, "_urllib_http", _no_net)
    s = pm_summary.build(DAY, env=TZ, jobs=[])
    assert s["fixes"] == {"items": [], "errors": []}
