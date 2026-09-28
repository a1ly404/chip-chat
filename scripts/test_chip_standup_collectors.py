"""RAZ-330 collector + morning digest tests (offline)."""

import json
from pathlib import Path

from chip import standup_collectors as sc
from chip import standup_format as sf
from chip import standup_morning as sm


def _ok_fetch(url: str, headers: dict, timeout: float) -> tuple[int, str]:
    if "actions/workflows" in url:
        body = json.dumps(
            {
                "workflow_runs": [
                    {
                        "name": "CI",
                        "conclusion": "success",
                        "html_url": "https://example/run/1",
                    }
                ]
            }
        )
        return 200, body
    if "search/issues" in url:
        return 200, json.dumps({"total_count": 0})
    if "gym.razzormail.com" in url or url.endswith("/"):
        return 303, ""
    return 404, ""


def test_collectors_all_ok_with_mocks(monkeypatch):
    import urllib.request

    monkeypatch.setenv("GITHUB_TOKEN", "test")
    monkeypatch.setenv("LINEAR_API_KEY", "lin_test")

    class Resp:
        status = 200

        def __init__(self, body: str):
            self._body = body.encode()

        def read(self, n=-1):
            return self._body if n < 0 else self._body[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    def urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "linear.app" in url:
            return Resp(
                json.dumps(
                    {
                        "data": {
                            "issues": {
                                "nodes": [
                                    {"labels": {"nodes": [{"name": "feedback"}]}},
                                ]
                            }
                        }
                    }
                )
            )
        raise AssertionError(f"unexpected urlopen {url}")

    def http_get(url: str, headers: dict, timeout: float) -> tuple[int, str]:
        return _ok_fetch(url, headers, timeout)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    bundle = sc.run_gymlog_collectors(http_get=http_get)
    assert bundle.collectors_live is True
    fields = sc.gymlog_fields_from_bundle(bundle)
    text = sf.format_gymlog_standup("2026-09-28", fields)
    assert "**Gate:** PASS" in text
    assert "unknown" not in text.split("**Blockers:**")[-1]


def test_collectors_site_fail_marks_miss_banner(tmp_path, monkeypatch, capsys):
    import urllib.request

    monkeypatch.setenv("GITHUB_TOKEN", "test")
    monkeypatch.setenv("LINEAR_API_KEY", "lin_test")
    monkeypatch.setenv("CHIP_CHAT_STANDUP_SIGNAL_FILE", str(tmp_path / "sig.json"))
    monkeypatch.setenv("GYMLOG_ORIGIN", "https://gym.razzormail.com")

    def fail_site(url: str, headers: dict, timeout: float) -> tuple[int, str]:
        if "gym.razzormail.com" in url:
            raise TimeoutError("down")
        return _ok_fetch(url, headers, timeout)

    class Resp:
        def __init__(self, body: str):
            self._body = body.encode()

        def read(self, n=-1):
            return self._body if n < 0 else self._body[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    def urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "linear.app" in url:
            return Resp(json.dumps({"data": {"issues": {"nodes": []}}}))
        status, body = fail_site(url, dict(req.header_items()), timeout)
        if status >= 400:
            raise OSError(f"http {status}")
        return Resp(body)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sc, "run_gymlog_collectors", lambda environ=None, http_get=None: sc.GymlogCollectorBundle(
        gha=sc.CollectorResult("gha", True, "ok", ""),
        site=sc.CollectorResult("site", False, "down", ""),
        linear=sc.CollectorResult("linear", True, "ok", ""),
        live_e2e=sc.CollectorResult("live_e2e", True, "open 0", ""),
        collectors_live=False,
    ))

    rc = sm.main(["--receipts-dir", str(tmp_path / "rcpt")])
    assert rc == 2
    out = capsys.readouterr().out
    assert "Missed standup payload" in out
    sig = json.loads((tmp_path / "sig.json").read_text())
    day = sig["2026-09-28"]["gymlog"]
    assert day["received"] is True
    assert day["missed_payload"] is True
    assert day["collectors_live"] is False


def test_format_keys_match_contract():
    stub = sf.gymlog_stub_payload()
    text = sf.format_gymlog_standup("2026-09-28", stub)
    for key in (
        "Gate",
        "Deploy",
        "AutoDev/QA",
        "local-exec",
        "Alerts",
        "Discord digest",
        "Blockers",
    ):
        assert key in text
