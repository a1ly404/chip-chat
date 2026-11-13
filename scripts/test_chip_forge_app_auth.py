"""chip.forge_driver GitHub App mode: per-step installation tokens, dev app = author, PM app = approver.

Throwaway RSA keys generated per test run and a fake forge (no network). Covers: JWT claims and RS256
signature, token scoped to the item repo + role permissions, dev token for push/open/merge, PM token
for a real forge review on the head sha, revoke after every step (also on failure), partial config /
missing role / same identity refused before any network call, no static-token fallback in app mode,
PM app as PR author refused, reused PR not opened by the dev app refused, never minted inside a job,
tokens/JWTs never in output or ledgers, unreadable/garbage key refused without echoing it, job_env
strips CHIP_FORGE_*.
"""

from __future__ import annotations

import base64
import json
import sys

import pytest

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from chip import dev_pr as dp, forge_driver as fd, job_policy as jp  # noqa: E402
from test_chip_dev_pr import AUTHOR, REVIEWER, REPO, assigned, env, job_receipt  # noqa: E402,F401
from test_chip_policy_exec import NOW, setup  # noqa: E402,F401

SHA = "a" * 40
MERGED = "c" * 40
CLOCK = 1_800_000_000.0
APPS = {"dev": ("111", "222", "acme-dev"), "pm": ("333", "444", "acme-pm")}
TOKS = {"dev": "ghs_" + "DEVTOK" * 6, "pm": "ghs_" + "PMTOK0" * 6}


def _key():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                          serialization.NoEncryption())
    return k.public_key(), pem


@pytest.fixture(scope="module")
def keys():
    return {r: _key() for r in fd.ROLES}


@pytest.fixture
def aenv(env, keys, tmp_path):  # noqa: F811
    e = {k: v for k, v in env.items() if k not in fd.TOKEN_ENVS}
    for role, (app_id, inst, _slug) in APPS.items():
        f = tmp_path / f"{role}.pem"
        f.write_bytes(keys[role][1])
        n_id, n_inst, n_key = fd.app_env_names(role)
        e.update({n_id: app_id, n_inst: inst, n_key: str(f)})
    return e


class AppForge:
    """Fake GitHub: app identity + token mint (JWT verified), PR endpoints keyed on the bearer's role."""

    def __init__(self, keys, author_login="acme-dev[bot]", head=SHA):
        self.keys, self.author_login, self.head = keys, author_login, head
        self.calls, self.minted, self.revoked, self.jwts, self.raw_jwts = [], [], [], [], []
        self.prs: dict[int, dict] = {}

    def _jwt_role(self, bearer):
        self.raw_jwts.append(bearer)
        for role, (app_id, _i, _s) in APPS.items():
            try:
                claims = jwt.decode(bearer, self.keys[role][0], algorithms=["RS256"],
                                    options={"verify_exp": False, "verify_iat": False})
            except jwt.InvalidTokenError:
                continue
            assert claims["iss"] == app_id
            self.jwts.append(claims)
            return role
        raise AssertionError("JWT not signed by a known app key")

    def pr_obj(self, n):
        return {"number": n, "state": "open", "head": {"sha": self.head}, "user": {"login": self.author_login},
                "base": {"repo": {"full_name": REPO}}, "html_url": f"https://forge/{n}"}

    def __call__(self, method, url, body, headers):
        bearer = headers["Authorization"].removeprefix("Bearer ")
        path = url[len(fd.API):]
        if path == "/app":
            role = self._jwt_role(bearer)
            return 200, {"id": int(APPS[role][0]), "slug": APPS[role][2]}
        if path.startswith("/app/installations/"):
            role = self._jwt_role(bearer)
            assert path == f"/app/installations/{APPS[role][1]}/access_tokens" and method == "POST"
            self.minted.append((role, body))
            return 201, {"token": TOKS[role], "expires_at": "2027-01-15T08:40:00Z"}
        if path == "/installation/token":
            assert method == "DELETE"
            self.revoked.append(bearer)
            return 204, None
        role = {v: k for k, v in TOKS.items()}.get(bearer)
        assert role, "PR call without an installation token"
        self.calls.append((role, method, path, body))
        if method == "GET" and "/pulls?state=open" in path:
            return 200, [self.pr_obj(n) for n in self.prs]
        if method == "POST" and path.endswith("/pulls"):
            self.prs[41] = {}
            return 201, self.pr_obj(41)
        if method == "GET" and "/pulls/" in path:
            return 200, self.pr_obj(int(path.rsplit("/", 1)[1]))
        if path.endswith("/check-runs?per_page=100"):
            return 200, {"check_runs": [{"status": "completed", "conclusion": "success"}]}
        if path.endswith("/status"):
            return 200, {"state": "success", "statuses": []}
        if method == "POST" and path.endswith("/reviews"):
            return 200, {"id": 7007}
        if method == "POST" and "/comments" in path:
            return 201, {"id": 9001}
        if method == "PUT" and path.endswith("/merge"):
            return 200, {"merged": True, "sha": MERGED}
        return 404, {"message": "nope"}


class FakeGit:
    def __init__(self, head=SHA):
        self.head, self.calls = head, []

    def __call__(self, argv, cwd, env):
        self.calls.append((list(argv), dict(env)))
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return 0, self.head + "\n"
        return (0, "ok") if "push" in argv else (1, "unknown")


def drv(e, forge, git=None):
    return fd.Driver(env=e, http=forge, git=git or FakeGit(), now=NOW, clock=lambda: CLOCK)


def opened(e, forge, git=None):
    it = assigned(e)
    dec = job_receipt(e)
    out = drv(e, forge, git).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/alpha/fix-7",
                                  title="fix", body="b", decision_id=dec)
    return it["id"], out


def test_app_mode_end_to_end_roles_and_revoke(aenv, keys):
    forge, git = AppForge(keys), FakeGit()
    iid, out = opened(aenv, forge, git)
    assert out["pr"] == 41 and out["state"] == "submitted"
    push_env = [e for a, e in git.calls if "push" in a][0]
    want = base64.b64encode(f"x-access-token:{TOKS['dev']}".encode()).decode()
    assert push_env["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {want}"
    assert not any(k.startswith("CHIP_FORGE_") for k in push_env)
    assert {r for r, m, p, b in forge.calls} == {"dev"}

    r = drv(aenv, forge).review(iid, actor=REVIEWER, verdict="approve", note="lgtm")
    assert r["kind"] == "review" and r["review_id"] == 7007 and r["state"] == "approved"
    rv = [(role, b) for role, m, p, b in forge.calls if p.endswith("/reviews")]
    assert len(rv) == 1 and rv[0][0] == "pm"
    assert rv[0][1]["event"] == "APPROVE" and rv[0][1]["commit_id"] == SHA and REVIEWER in rv[0][1]["body"]
    assert not [c for c in forge.calls if "/comments" in c[2]]

    m = drv(aenv, forge).merge(iid, actor=jp.PM)
    assert m["merged"] and m["merged_sha"] == MERGED
    assert [role for role, mm, p, b in forge.calls if mm == "PUT"] == ["dev"]
    assert dp.show(iid)["state"] == "merged"

    # one mint + one revoke per step, scoped to the item repo and the role's permissions
    assert [role for role, _ in forge.minted] == ["dev", "pm", "dev"]
    for role, body in forge.minted:
        assert body == {"repositories": [REPO.split("/")[1]], "permissions": fd.APP_PERMS[role]}
    assert forge.revoked == [TOKS["dev"], TOKS["pm"], TOKS["dev"]]
    rows = [json.loads(x) for x in (jp.policy_dir() / fd.FILE_NAME).read_text().splitlines()]
    assert [r["auth"] for r in rows] == ["app:dev", "app:pm", "app:dev"]


def test_jwt_claims(aenv, keys):
    forge = AppForge(keys)
    opened(aenv, forge)
    c = forge.jwts[0]
    assert c["iss"] == APPS["dev"][0] and c["iat"] == int(CLOCK) - 60
    assert 0 < c["exp"] - int(CLOCK) <= 540 and c["exp"] - c["iat"] <= 600


def test_changes_requested_is_request_changes_review(aenv, keys):
    forge = AppForge(keys)
    iid, _ = opened(aenv, forge)
    drv(aenv, forge).review(iid, actor=REVIEWER, verdict="changes", note="fix x")
    assert [b["event"] for r, m, p, b in forge.calls if p.endswith("/reviews")] == ["REQUEST_CHANGES"]


def test_pm_app_as_pr_author_cannot_approve(aenv, keys):
    forge = AppForge(keys)
    iid, _ = opened(aenv, forge)
    forge.author_login = "acme-pm[bot]"
    with pytest.raises(dp.DevPrRefused, match="author != approver"):
        drv(aenv, forge).review(iid, actor=REVIEWER, verdict="approve")
    assert not [c for c in forge.calls if c[2].endswith("/reviews")]
    assert dp.show(iid)["state"] == "submitted" and forge.revoked[-1] == TOKS["pm"]


def test_reused_pr_not_opened_by_dev_app_refused(aenv, keys):
    forge = AppForge(keys, author_login="someone-else")
    forge.prs[41] = {}
    it = assigned(aenv)
    dec = job_receipt(aenv)
    with pytest.raises(dp.DevPrRefused, match="not opened by the dev app"):
        drv(aenv, forge).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/x", title="t", body="",
                              decision_id=dec)
    assert dp.show(it["id"])["state"] == "assigned" and forge.revoked == [TOKS["dev"]]


def test_revoked_even_when_step_fails(aenv, keys):
    forge, git = AppForge(keys, head="b" * 40), FakeGit(head=SHA)
    it = assigned(aenv)
    dec = job_receipt(aenv)
    with pytest.raises(dp.DevPrRefused, match="does not match the pushed sha"):
        drv(aenv, forge, git).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/x", title="t", body="",
                                   decision_id=dec)
    assert forge.revoked == [TOKS["dev"]]


@pytest.mark.parametrize("drop", [0, 1, 2])
def test_partial_app_config_refused_before_network(aenv, keys, drop):
    forge, git = AppForge(keys), FakeGit()
    it = assigned(aenv)
    dec = job_receipt(aenv)
    e = {**aenv, fd.app_env_names("dev")[drop]: ""}
    with pytest.raises(dp.DevPrRefused, match="partially configured"):
        drv(e, forge, git).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/x", title="t", body="",
                                decision_id=dec)
    assert forge.calls == [] and forge.minted == [] and git.calls == []


def test_no_static_token_fallback_in_app_mode(aenv, keys):
    forge = AppForge(keys)
    iid, _ = opened(aenv, forge)
    e = {k: v for k, v in aenv.items() if not k.startswith("CHIP_FORGE_PM_")}
    e["CHIP_FORGE_TOKEN"] = "ghp_" + "STATIC" * 6
    n = len(forge.calls)
    with pytest.raises(dp.DevPrRefused, match="pm app is not configured"):
        drv(e, forge).review(iid, actor=REVIEWER, verdict="approve")
    assert len(forge.calls) == n


@pytest.mark.parametrize("field", ["id", "inst", "key"])
def test_dev_and_pm_must_be_distinct(aenv, keys, field):
    forge = AppForge(keys)
    it = assigned(aenv)
    dev, pm = fd.app_env_names("dev"), fd.app_env_names("pm")
    i = {"id": 0, "inst": 1, "key": 2}[field]
    e = {**aenv, pm[i]: aenv[dev[i]]}
    with pytest.raises(dp.DevPrRefused, match="must be distinct"):
        drv(e, forge).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/x", title="t", body="",
                           decision_id="x")
    assert forge.minted == []


def test_never_minted_inside_a_job(aenv, keys, monkeypatch):
    forge = AppForge(keys)
    iid, _ = opened(aenv, forge)
    monkeypatch.setenv("CHIP_BGJOB_ID", "bg-1")
    n = len(forge.minted)
    with pytest.raises(dp.DevPrRefused, match="never minted inside a job"):
        drv(aenv, forge).review(iid, actor=REVIEWER, verdict="approve")
    assert len(forge.minted) == n and forge.jwts and len(forge.jwts) == 2 * n


def test_bad_key_refused_without_echo(aenv, keys, tmp_path):
    forge = AppForge(keys)
    it = assigned(aenv)
    dec = job_receipt(aenv)
    junk = tmp_path / "junk.pem"
    junk.write_text("-----BEGIN RSA PRIVATE KEY-----\nNOTAKEYMATERIAL123\n-----END RSA PRIVATE KEY-----\n")
    for path, msg in ((str(tmp_path / "missing.pem"), "not readable"), (str(junk), "not a usable RSA")):
        e = {**aenv, fd.app_env_names("dev")[2]: path}
        with pytest.raises(dp.DevPrRefused, match=msg) as exc:
            drv(e, forge).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/x", title="t", body="",
                               decision_id=dec)
        assert "NOTAKEYMATERIAL" not in str(exc.value)
    assert forge.minted == []


def test_missing_pyjwt_refused(aenv, keys, monkeypatch):
    monkeypatch.setitem(sys.modules, "jwt", None)
    forge = AppForge(keys)
    it = assigned(aenv)
    dec = job_receipt(aenv)
    with pytest.raises(dp.DevPrRefused, match="PyJWT"):
        drv(aenv, forge).open(it["id"], actor=AUTHOR, worktree="/wt", branch="dev/x", title="t", body="",
                              decision_id=dec)


def test_secrets_never_surface(aenv, keys, capsys):
    class Leaky(AppForge):
        def __call__(self, method, url, body, headers):
            if url.endswith("/merge"):
                raise RuntimeError("boom " + headers["Authorization"])
            return super().__call__(method, url, body, headers)

    forge = Leaky(keys)
    iid, _ = opened(aenv, forge)
    drv(aenv, forge).review(iid, actor=REVIEWER, verdict="approve")
    rc = fd.main(["merge", iid, "--actor", jp.PM], driver=drv(aenv, forge))
    out = capsys.readouterr().out
    assert rc == fd.EXIT_FORGE and "***" in out
    blobs = [out, (jp.policy_dir() / fd.FILE_NAME).read_text(), dp.ledger_path().read_text()]
    for blob in blobs:
        assert TOKS["dev"] not in blob and TOKS["pm"] not in blob and "BEGIN" not in blob
        assert forge.raw_jwts and not any(j in blob for j in forge.raw_jwts)
    assert forge.revoked[-1] == TOKS["dev"]


def test_cli_ci_uses_dev_app(aenv, keys, capsys):
    forge = AppForge(keys)
    iid, _ = opened(aenv, forge)
    assert fd.main(["ci", iid], driver=drv(aenv, forge)) == 0
    assert json.loads(capsys.readouterr().out)["ci"] == "success"
    assert forge.minted[-1][0] == "dev" and forge.revoked[-1] == TOKS["dev"]


def test_job_env_strips_forge_vars(aenv):
    e = {**aenv, "CHIP_FORGE_TOKEN": "x", "OTHER": "ok"}
    out = jp.job_env(e, job_id="j", actor="dev:alpha")
    assert not any(k.startswith("CHIP_FORGE_") for k in out) and out["OTHER"] == "ok"
