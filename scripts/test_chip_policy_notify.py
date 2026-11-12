"""Operator ask notifier: built-in generic webhook channel + `chip policy notify` CLI.

The webhook is a fake transport (no network). The URL must never appear in CLI output.
"""

from __future__ import annotations

import json
from datetime import timezone

import pytest

from chip import cli, job_policy as jp, policy_exec as px

from test_chip_policy_exec import NOW, auth, req, setup  # noqa: F401  (fixture reuse)

FAKE_URL = "https://hooks.example.invalid/ops/abc123"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    jp.clear_hooks()
    px.clear_notifiers()
    sent: list[tuple[str, dict]] = []

    def transport(url, body):
        sent.append((url, json.loads(body.decode("utf-8"))))
        return transport.status

    transport.status = 204
    px.set_webhook_transport(transport)
    monkeypatch.setattr(jp, "_now", lambda now=None: (now or NOW).astimezone(timezone.utc))
    yield transport, sent
    px.set_webhook_transport(None)
    jp.clear_hooks()
    px.clear_notifiers()


def _env(setup, **extra):
    return {**setup["env"], px.NOTIFY_FLAG: "1", px.NOTIFY_WEBHOOK_ENV: FAKE_URL, **extra}


def test_webhook_env_posts_confirm_once_with_mentions_disabled(setup, _clean):
    _, sent = _clean
    a = auth(setup, req(sudo=True), env=_env(setup), pm_reason="app down @everyone")
    assert a.verdict == "ask" and a.notify == "sent"
    assert len(sent) == 1
    url, payload = sent[0]
    assert url == FAKE_URL
    assert payload["allowed_mentions"] == {"parse": []}
    assert f"chip policy confirm {a.request_id}" in payload["content"]
    again = auth(setup, req(sudo=True, job_id="j2"), env=_env(setup))
    assert again.notify == "duplicate" and len(sent) == 1


def test_webhook_off_by_default_even_with_url(setup, _clean):
    _, sent = _clean
    env = {**setup["env"], px.NOTIFY_WEBHOOK_ENV: FAKE_URL}
    a = auth(setup, req(sudo=True), env=env)
    assert a.notify == "off" and sent == []


def test_webhook_non_2xx_is_error_then_retried_by_sweep(setup, _clean):
    transport, sent = _clean
    transport.status = 500
    a = auth(setup, req(sudo=True), env=_env(setup))
    assert a.notify == "error"
    transport.status = 200
    res = px.notify_pending(env=_env(setup), now=NOW)
    assert res == {a.request_id: "sent"} and len(sent) == 2
    assert px.notify_pending(env=_env(setup), now=NOW) == {}


def test_bad_webhook_url_is_error_not_raise(setup, _clean):
    _, sent = _clean
    a = auth(setup, req(sudo=True), env=_env(setup, **{px.NOTIFY_WEBHOOK_ENV: "file:///etc/passwd"}))
    assert a.notify == "error" and sent == []


def test_long_message_is_clipped(setup, _clean):
    _, sent = _clean
    auth(setup, req(sudo=True), env=_env(setup), pm_reason="x" * 300)
    assert len(sent[0][1]["content"]) <= px.NOTIFY_MAX_CHARS
    fn = px.webhook_notifier(FAKE_URL, transport=lambda u, b: sent.append((u, json.loads(b))) or 200)
    fn("y" * 5000, {})
    assert len(sent[-1][1]["content"]) <= px.NOTIFY_MAX_CHARS and sent[-1][1]["content"].endswith("(clipped)")


def test_notify_channels_has_no_url(setup):
    ch = px.notify_channels(_env(setup))
    assert ch == {"enabled": True, "registered": 0, "hooks": 0, "webhook": True}
    assert FAKE_URL not in json.dumps(ch)


def test_cli_check_notify_and_notify_command(setup, _clean, monkeypatch, capsys):
    _, sent = _clean
    for k, v in _env(setup).items():
        monkeypatch.setenv(k, v)
    raw = json.dumps(req(sudo=True))
    assert cli.main(["policy", "check", "--request", raw, "--notify"]) == 4
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "ask" and out["notify"] == "sent" and len(sent) == 1
    rid = out["request_id"]
    assert cli.main(["policy", "notify", rid]) == 0
    text = capsys.readouterr().out
    assert json.loads(text)["notify"] == "duplicate" and FAKE_URL not in text
    assert cli.main(["policy", "notify", "pr-nope"]) == 3
    capsys.readouterr()
    assert cli.main(["policy", "notify", "--pending"]) == 0
    assert json.loads(capsys.readouterr().out)["notify"] == {}
    assert cli.main(["policy", "notify"]) == 2
    assert cli.main(["policy", "status"]) == 0
    st = capsys.readouterr().out
    assert json.loads(st)["notify"]["webhook"] is True and FAKE_URL not in st


def test_cli_check_without_notify_flag_does_not_post(setup, _clean, monkeypatch, capsys):
    _, sent = _clean
    for k, v in _env(setup).items():
        monkeypatch.setenv(k, v)
    assert cli.main(["policy", "check", "--request", json.dumps(req(sudo=True))]) == 4
    assert "notify" not in json.loads(capsys.readouterr().out) and sent == []


def test_cli_notify_refused_inside_a_job(setup, _clean, monkeypatch, capsys):
    _, sent = _clean
    for k, v in _env(setup).items():
        monkeypatch.setenv(k, v)
    a = auth(setup, req(sudo=True), env={**setup["env"]})  # notify off: nothing posted yet
    monkeypatch.setenv("CHIP_BGJOB_ID", "j9")
    assert cli.main(["policy", "notify", a.request_id]) == 2
    assert cli.main(["policy", "notify", "--pending"]) == 2
    assert sent == []


def test_cli_notify_off_exit_1(setup, _clean, monkeypatch, capsys):
    for k, v in setup["env"].items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv(px.NOTIFY_FLAG, raising=False)
    a = auth(setup, req(sudo=True))
    assert cli.main(["policy", "notify", a.request_id]) == 1
    assert json.loads(capsys.readouterr().out)["notify"] == "off"


def test_job_env_never_carries_the_webhook(setup):
    env = jp.job_env(_env(setup), job_id="j1", actor="dev:alpha")
    assert px.NOTIFY_WEBHOOK_ENV not in env and FAKE_URL not in json.dumps(env)
