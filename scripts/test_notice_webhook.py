"""CHIP_WEBHOOK_RELAY first, CHIP_WEBHOOK_DEV fallback (no network)."""

from chip_relay.notice_webhook import notice_webhook_env, notice_webhook_url, resolve_room_url


def test_relay_preferred():
    env = {"CHIP_WEBHOOK_RELAY": "https://example.invalid/relay", "CHIP_WEBHOOK_DEV": "https://example.invalid/dev"}
    assert notice_webhook_env(env) == "CHIP_WEBHOOK_RELAY"
    assert notice_webhook_url(env) == "https://example.invalid/relay"


def test_dev_fallback_when_relay_blank():
    env = {"CHIP_WEBHOOK_RELAY": "  ", "CHIP_WEBHOOK_DEV": "https://example.invalid/dev"}
    assert notice_webhook_env(env) == "CHIP_WEBHOOK_DEV"
    assert notice_webhook_url(env) == "https://example.invalid/dev"


def test_neither_set():
    assert notice_webhook_url({}) == ""


def test_resolve_room_url():
    env = {"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"}
    assert resolve_room_url("CHIP_WEBHOOK_RELAY", env) == "https://example.invalid/dev"
    assert resolve_room_url("CHIP_WEBHOOK_SELFTEST", env) == ""
    env["CHIP_WEBHOOK_RELAY"] = "https://example.invalid/relay"
    assert resolve_room_url("CHIP_WEBHOOK_RELAY", env) == "https://example.invalid/relay"
