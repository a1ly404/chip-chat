"""Ask-notifier runtime fallback: CHIP_POLICY_NOTIFY_WEBHOOK_FROM names the var holding the URL."""

from __future__ import annotations

import json
from datetime import timezone

import pytest

from chip import job_policy as jp, policy_exec as px

from test_chip_policy_exec import NOW, auth, req, setup  # noqa: F401  (fixture reuse)

FAKE_URL = "https://hooks.example.invalid/ops/fallback"
DIRECT_URL = "https://hooks.example.invalid/ops/direct"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    jp.clear_hooks()
    px.clear_notifiers()
    sent: list[tuple[str, dict]] = []

    def transport(url, body):
        sent.append((url, json.loads(body.decode("utf-8"))))
        return 204

    px.set_webhook_transport(transport)
    monkeypatch.setattr(jp, "_now", lambda now=None: (now or NOW).astimezone(timezone.utc))
    yield sent
    px.set_webhook_transport(None)
    jp.clear_hooks()
    px.clear_notifiers()


def _env(setup, **extra):
    return {**setup["env"], px.NOTIFY_FLAG: "1", **extra}


def test_fallback_var_is_used_when_direct_url_is_unset(setup, _clean):
    env = _env(setup, **{px.NOTIFY_WEBHOOK_ENV: "", px.NOTIFY_WEBHOOK_FROM_ENV: "OPS_WEBHOOK_DEV", "OPS_WEBHOOK_DEV": FAKE_URL})
    assert px.notify_webhook_source(env) == "OPS_WEBHOOK_DEV"
    assert px.notify_channels(env)["webhook"] is True
    assert FAKE_URL not in json.dumps(px.notify_channels(env))
    a = auth(setup, req(sudo=True), env=env)
    assert a.verdict == "ask" and a.notify == "sent"
    assert [u for u, _ in _clean] == [FAKE_URL]


def test_direct_url_wins_over_fallback(setup, _clean):
    env = _env(setup, **{px.NOTIFY_WEBHOOK_ENV: DIRECT_URL, px.NOTIFY_WEBHOOK_FROM_ENV: "OPS_WEBHOOK_DEV", "OPS_WEBHOOK_DEV": FAKE_URL})
    assert px.notify_webhook_url(env) == DIRECT_URL
    auth(setup, req(sudo=True), env=env)
    assert [u for u, _ in _clean] == [DIRECT_URL]


@pytest.mark.parametrize(
    "name,extra",
    [
        ("OPS_WEBHOOK_DEV", {}),  # named var unset
        ("OPS_WEBHOOK_DEV", {"OPS_WEBHOOK_DEV": "  "}),  # blank
        ("LINEAR_API_KEY", {"LINEAR_API_KEY": FAKE_URL}),  # not a *WEBHOOK* name: refused
        ("ops_webhook", {"ops_webhook": FAKE_URL}),  # not an env-var-shaped name
        ("", {}),
    ],
)
def test_fallback_refuses_or_finds_nothing(setup, _clean, name, extra):
    env = _env(setup, **{px.NOTIFY_WEBHOOK_FROM_ENV: name, **extra})
    assert px.notify_webhook_source(env) == "" and px.notify_webhook_url(env) == ""
    assert px.notify_channels(env)["webhook"] is False
    a = auth(setup, req(sudo=True), env=env)
    assert a.verdict == "ask" and a.notify == "no-notifier" and not _clean


def test_job_env_never_carries_fallback_url(setup):
    env = {**setup["env"], px.NOTIFY_WEBHOOK_FROM_ENV: "OPS_WEBHOOK_DEV", "OPS_WEBHOOK_DEV": FAKE_URL}
    je = jp.job_env(env, job_id="j1", actor="dev:x")
    assert FAKE_URL not in json.dumps(je)
