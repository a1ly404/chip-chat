"""Forge driver read-only ``fixes`` step (PM app) + ``chip.pm_fixes`` CHIP_PM_FIXES_VIA=forge (fakes only).

Covers: the driver step mints a READ-ONLY PM token scoped to the repo, retries once without
``issues`` on a 422 mint, revokes, returns pm_fixes items, refuses a repo off the allowlist / a bad
window / inside a job before any network call, never leaks the token; pm_fixes routes only the
forge repos through the runner (others stay direct), drops off-repo/out-of-window items, turns
runner failures into error lines, and is refused inside a job.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from chip import dev_pr as dp, forge_driver as fd, forge_steps as fs, job_policy as jp, pm_fixes  # noqa: E402
from test_chip_dev_pr import REPO, env  # noqa: E402,F401
from test_chip_forge_app_auth import APPS, TOKS, AppForge, aenv, drv, keys  # noqa: E402,F401
from test_chip_policy_exec import setup  # noqa: E402,F401

SINCE, UNTIL = "2026-03-08T08:00:00Z", "2026-03-09T08:00:00Z"
START = datetime(2026, 3, 8, 8, 0, tzinfo=timezone.utc)
END = datetime(2026, 3, 9, 8, 0, tzinfo=timezone.utc)
IN = "2026-03-08T18:00:00Z"


class FixForge(AppForge):
    def __init__(self, keys, mint_422_with_issues=False):
        super().__init__(keys)
        self.mint_422 = mint_422_with_issues

    def __call__(self, method, url, body, headers):
        path = url[len(fd.API):]
        if self.mint_422 and path.startswith("/app/installations/") and "issues" in body["permissions"]:
            self._jwt_role(headers["Authorization"].removeprefix("Bearer "))
            self.minted.append(("pm", body))
            return 422, {"message": "permissions not granted"}
        if path.startswith(f"/repos/{REPO}/issues?") or path.startswith(f"/repos/{REPO}/pulls?"):
            bearer = headers["Authorization"].removeprefix("Bearer ")
            self.calls.append(({v: k for k, v in TOKS.items()}.get(bearer), method, path, body))
            if "/issues?" in path:
                return 200, [{"number": 7, "title": "cc-fix: x", "closed_at": IN, "html_url": "https://f/i/7"}]
            return 200, [{"number": 8, "title": "fix x (Fixes #7)", "merged_at": IN, "merge_commit_sha": "c" * 40,
                          "html_url": "https://f/p/8", "head": {"ref": "dev/a"}, "labels": []}]
        return super().__call__(method, url, body, headers)


def test_fixes_step_reads_with_readonly_pm_token(aenv, keys):  # noqa: F811
    forge = FixForge(keys)
    out = drv(aenv, forge).fixes(REPO, since=SINCE, until=UNTIL)
    assert out["auth"] == "app:pm" and out["errors"] == []
    assert [(i["kind"], i["number"]) for i in out["items"]] == [("issue", 7), ("pr", 8)]
    assert forge.minted == [("pm", {"repositories": [REPO.split("/")[1]], "permissions": fd.FIXES_PERMS[0]})]
    assert all(v == "read" for v in fd.FIXES_PERMS[0].values())
    assert {(r, m) for r, m, p, b in forge.calls} == {("pm", "GET")}
    assert forge.revoked == [TOKS["pm"]]
    assert TOKS["pm"] not in json.dumps(out)
    rows = [json.loads(x) for x in (jp.policy_dir() / fd.FILE_NAME).read_text().splitlines()]
    assert rows[-1]["step"] == "fixes" and rows[-1]["items"] == 2 and TOKS["pm"] not in json.dumps(rows)


def test_fixes_step_retries_without_issues_on_422(aenv, keys):  # noqa: F811
    forge = FixForge(keys, mint_422_with_issues=True)
    out = drv(aenv, forge).fixes(REPO, since=SINCE, until=UNTIL)
    assert [b["permissions"] for _r, b in forge.minted] == list(fd.FIXES_PERMS)
    assert out["auth"] == "app:pm" and len(out["items"]) == 2


@pytest.mark.parametrize("repo,since,until,why", [
    ("not/allowed", SINCE, UNTIL, "allowlist"),
    ("bad repo", SINCE, UNTIL, "malformed"),
    (REPO, "yesterday", UNTIL, "ISO"),
    (REPO, UNTIL, SINCE, "window"),
    (REPO, "2026-01-01T00:00:00Z", UNTIL, "window"),
])
def test_fixes_step_refuses_before_network(aenv, keys, repo, since, until, why):  # noqa: F811
    forge = FixForge(keys)
    with pytest.raises(dp.DevPrRefused, match=why):
        drv(aenv, forge).fixes(repo, since=since, until=until)
    assert forge.minted == [] and forge.calls == []


def test_fixes_step_never_mints_inside_a_job(aenv, keys, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CHIP_BGJOB_ID", "j1")
    forge = FixForge(keys)
    with pytest.raises(dp.DevPrRefused):
        drv(aenv, forge).fixes(REPO, since=SINCE, until=UNTIL)
    assert forge.minted == []


def test_fixes_cli(aenv, keys, capsys):  # noqa: F811
    forge = FixForge(keys)
    rc = fd.main(["fixes", "--repo", REPO, "--since", SINCE, "--until", UNTIL], driver=drv(aenv, forge))
    out = json.loads(capsys.readouterr().out)
    assert rc == fd.EXIT_OK and len(out["items"]) == 2
    rc = fd.main(["fixes", "--repo", "x/y", "--since", SINCE, "--until", UNTIL], driver=drv(aenv, forge))
    assert rc == fd.EXIT_REFUSED


# ------------------------------------------------------------------ pm_fixes side


class FakeRunner:
    def __init__(self, rc=0, out=None, boom=None):
        self.rc, self.out, self.boom, self.calls = rc, out, boom, []

    def __call__(self, step, args):
        self.calls.append((step, list(args)))
        if self.boom:
            raise self.boom
        return fs.StepResult(self.rc, self.out if self.out is not None else {})


PRIV, PUB = "acme/private", "acme/public"
VENV = {pm_fixes.REPOS_ENV: f"{PUB},{PRIV}", pm_fixes.VIA_ENV: "forge", pm_fixes.FORGE_REPOS_ENV: PRIV}


@pytest.fixture
def clean(monkeypatch, tmp_path):
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    for k in ("CHIP_BGJOB_ID", "CHIP_JOB_ACTOR"):
        monkeypatch.delenv(k, raising=False)


def test_via_forge_selection(clean):
    assert pm_fixes.via_forge({pm_fixes.REPOS_ENV: PRIV}) == []
    assert pm_fixes.via_forge({**VENV}) == [PRIV]
    assert pm_fixes.via_forge({**VENV, pm_fixes.FORGE_REPOS_ENV: ""}) == [PUB, PRIV]


def test_collect_routes_forge_repo_through_runner(clean):
    item = {"kind": "pr", "at": IN, "repo": PRIV, "number": 9, "url": "https://f/p/9", "title": "t", "sha": "abc1234",
            "why": "label", "extra": "dropped"}
    junk = [{"kind": "pr", "at": IN, "repo": "evil/repo", "url": "u1"}, {"kind": "pr", "at": "2020-01-01T00:00:00Z",
            "repo": PRIV, "url": "u2"}, {"kind": "receipt", "at": IN, "repo": PRIV}, "nope"]
    r = FakeRunner(out={"repo": PRIV, "items": [item, *junk], "errors": [], "auth": "app:pm"})
    direct = []

    def http(url, headers):
        direct.append(url)
        return 200, []

    got = pm_fixes.collect(START, END, env=VENV, http=http, runner=r)
    assert r.calls == [("fixes", ["--repo", PRIV, "--since", SINCE, "--until", UNTIL, "--label", "cc-fix"])]
    assert direct and all(f"/repos/{PUB}/" in u for u in direct)
    assert got["errors"] == []
    assert got["items"] == [{k: v for k, v in item.items() if k != "extra"} | {"via": "forge"}]
    assert any("PR merged https://f/p/9" in x for x in pm_fixes.lines(got))
    assert not any("forge unavailable" in x for x in pm_fixes.lines(got))


@pytest.mark.parametrize("runner,want", [
    (FakeRunner(rc=2, out={"refused": "repo not in allowlist"}), "rc 2 repo not in allowlist"),
    (FakeRunner(rc=5, out={"forge_error": "pm app token mint -> 401"}), "rc 5 pm app token mint"),
    (FakeRunner(out={"items": [], "errors": ["x issues: HTTP 403"]}), "via forge: x issues: HTTP 403"),
    (FakeRunner(boom=OSError("no docker")), "via forge: OSError"),
])
def test_runner_failures_are_error_lines(clean, runner, want):
    got = pm_fixes.collect(START, END, env={**VENV, pm_fixes.REPOS_ENV: PRIV}, http=lambda u, h: (200, []), runner=runner)
    assert got["items"] == [] and any(want in e for e in got["errors"])
    assert any(x.startswith("forge unavailable") for x in pm_fixes.lines(got))


def test_no_runner_configured_is_an_error_line(clean, monkeypatch):
    monkeypatch.delenv(fs.CMD_ENV, raising=False)
    got = pm_fixes.collect(START, END, env={pm_fixes.REPOS_ENV: PRIV, pm_fixes.VIA_ENV: "forge"})
    assert got["errors"] == [f"{PRIV} via forge: no runner ({fs.CMD_ENV})"]


def test_refused_inside_a_job(clean, monkeypatch):
    monkeypatch.setenv("CHIP_BGJOB_ID", "j1")
    r = FakeRunner(out={"items": []})
    got = pm_fixes.collect(START, END, env={pm_fixes.REPOS_ENV: PRIV, pm_fixes.VIA_ENV: "forge"}, runner=r)
    assert r.calls == [] and "refused inside a job" in got["errors"][0]
