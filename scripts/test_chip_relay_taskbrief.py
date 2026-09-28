"""Dispatch→brief wiring tests (Phase 2; offline; isolated palace).

- delegate hops anchored to a RAZ id get the brief when CHIP_TASKBRIEF=1
  (task row columns flow into chip via --task-id/--task-type)
- not opted in → legacy hop shape, zero behavior change (regression law)
- palace gate fail-visible: unreachable palace REFUSES the hop with an
  explicit alert (never a silent downgrade to the raw task)
- stand-down + hop-cap precedence unchanged
"""

import json

import pytest

from chip_relay import dispatch
from chip_relay.delegate import DelegateRequest


class _Chip:
    def __init__(self, capture):
        self.capture = capture

    def __call__(self, args, timeout=0):
        self.capture.append(list(args))
        return dispatch.ChipResult(returncode=0, stdout='{"reply": "ok"}')


class _Webhook:
    def __init__(self, capture):
        self.capture = capture

    def __call__(self, url, persona, text):
        self.capture.append((persona, text))
        return dispatch.WebhookResult(status=204)


def _room():
    return dispatch.RoomBinding(id="dev", as_agent="pm", webhook_env="CHIP_WEBHOOK_DEV", discord_channel="chip-dev")


def _env(tmp_path):
    return {
        "CHIP_WEBHOOK_DEV": "https://probe.invalid/dev",
        "CHIP_TASK_PALACE_DIR": str(tmp_path / "palace"),
    }




# /tmp/opencode: safe tmp root for the palace override (the chip data dir path
# default is ~/appdata/chip-chat when env missing — the relay container env is
# production-shaped; tests always override). fixture: tmp_path/palace.


def test_brief_opt_in_anchored_hop_gets_task_args(tmp_path):
    (tmp_path / "palace").mkdir()
    chips, web = [], []
    res = dispatch.handle_inbound_delegate(
        dispatch.RoomBinding(id="dev", as_agent="pm", webhook_env="CHIP_WEBHOOK_DEV", discord_channel="chip-dev"),
        from_persona="chipchatdev",
        content="delegate:homelabdev RAZ-9501 diagnose the cron gap",
        chip=_Chip(chips), webhook=_Webhook(web),
        environ={
            "CHIP_TASKBRIEF": "1",
            "CHIP_TASK_PALACE_DIR": str(tmp_path / "palace"),
            "CHIP_WEBHOOK_DEV": "w",
        },
    )
    assert res.ok
    assert any("--task-id" in [str(x) for x in a] or "RAZ-9501" in [str(x) for x in a] for a in chips)


def test_brief_optin_unreachable_palace_refuses_hop(tmp_path):
    res = dispatch.handle_inbound_delegate(
        dispatch.RoomBinding(id="dev", as_agent="pm", webhook_env="CHIP_WEBHOOK_DEV", discord_channel="chip-dev"),
        from_persona="chipchatdev",
        content="delegate:homelabdev RAZ-9502 diagnose",
        chip=_Chip([]), webhook=_Webhook([]),
        environ={
            "CHIP_TASKBRIEF": "1",
            "CHIP_TASK_PALACE_DIR": str(tmp_path / "ghost"),
            "CHIP_WEBHOOK_DEV": "w",
        },
    )
    assert not res.ok
    assert "delegation refused" in res.alert
    assert "fail-visible" in res.alert.lower() or "palace" in res.alert.lower()


def test_brief_off_legacy_shape(tmp_path):
    chips = []
    res = dispatch.handle_inbound_delegate(
        dispatch.RoomBinding(id="dev", as_agent="pm", webhook_env="CHIP_WEBHOOK_DEV", discord_channel="chip-dev"),
        from_persona="chipchatdev",
        content="delegate:homelabdev RAZ-9502 diagnose",
        chip=_Chip(chips), webhook=_Webhook([]),
        environ={
            "CHIP_TASKBRIEF": "0",
            "CHIP_TASK_PALACE_DIR": str(tmp_path / "ghost"),
            "CHIP_WEBHOOK_DEV": "w",
        },
    )
    assert res.ok  # not opted in → legacy hop runs WITHOUT touching the palace gate
    assert chips and "--task-id" not in [str(x) for x in chips[0]]
