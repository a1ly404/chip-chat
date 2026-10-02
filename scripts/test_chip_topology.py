"""Topology lock — solo default, mothballed swarm, delegate hop cap."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from chip import collab, topology
from chip_relay.delegate import MAX_DELEGATE_HOPS
from chip_relay.dispatch import handle_message, Incoming
from chip_relay.config import RelayConfig, RoomBinding
from chip_relay.dispatch import ChipResult, WebhookResult


def test_config_pm_hub_interface_and_swarm_mothballed() -> None:
    cfg = topology.load()
    assert cfg["default_topology"] == "pm_hub"
    assert cfg["one_persona_per_go"] is True
    assert topology.human_entry_persona() == "pm"
    assert topology.agent_to_agent_reply_chains_allowed() is False
    assert cfg["interface"]["pm_executes_and_posts_receipt_as_pm"] is True
    assert cfg["specialists"]["not_a_second_conversation"] is True
    assert cfg["visible_room"]["goal_is_human_visibility_not_agent_coherence"] is True
    assert cfg["delegate"]["charter_cross_lane_only"] is True
    assert cfg["delegate"]["no_agent_to_agent_reply_chains"] is True
    assert cfg["swarm"]["mothballed"] is True
    assert cfg["relay"]["auto_multi_persona_fanout"] is False


def test_delegate_hop_cap_matches_config_and_at_most_three() -> None:
    hops = topology.max_delegate_hops()
    assert hops <= 3
    assert MAX_DELEGATE_HOPS == hops


def test_multi_persona_brainstorm_refused_without_swarm_unlock(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHIP_LANE_GO_TICKET", "RAZ-335")
    monkeypatch.delenv("CHIP_TOPOLOGY_SWARM_GO", raising=False)
    topology.load.cache_clear()
    with pytest.raises(PermissionError, match="mothballed"):
        collab.begin_brainstorm("dev", "goal", ["a", "b"])


def test_multi_persona_brainstorm_allowed_with_swarm_unlock(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHIP_LANE_GO_TICKET", "RAZ-335")
    monkeypatch.setenv("CHIP_TOPOLOGY_SWARM_GO", "1")
    topology.load.cache_clear()
    board = collab.begin_brainstorm("dev", "goal", ["a", "b"])
    assert board["participants"] == ["a", "b"]


def test_collab_ab_refused_when_mothballed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from chip.collab_ab import run_ab

    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHIP_LANE_GO_TICKET", "RAZ-335")
    monkeypatch.delenv("CHIP_TOPOLOGY_SWARM_GO", raising=False)
    topology.load.cache_clear()
    with pytest.raises(PermissionError, match="mothballed"):
        run_ab("dev", "probe", ["pm", "chipchatdev", "opsdev"])


def test_ops_ab_trio_arm_refused_when_mothballed(monkeypatch: pytest.MonkeyPatch) -> None:
    ab = importlib.import_module("chip.ops_session_a_b")
    monkeypatch.delenv("CHIP_TOPOLOGY_SWARM_GO", raising=False)
    topology.load.cache_clear()
    assert ab.main(["--arm", "trio", "--allow-mock-mutation"]) == 5


def test_relay_no_delegate_single_chip_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    assert topology.relay_auto_fanout_enabled() is False
    room = RoomBinding(
        id="dev",
        as_agent="dev",
        webhook_env="CHIP_WEBHOOK_DEV",
        discord_channel="chip-dev",
    )
    config = RelayConfig(channel="chip-relay", turn_limit=1, auto=False, rooms=(room,))
    calls: list[int] = []

    def chip(_args: list[str], timeout: int = 0) -> ChipResult:
        del timeout
        calls.append(1)
        return ChipResult(0, '{"reply": "claim: dev scope\\nstatus: ok\\nevidence: none\\nnext: none"}')

    result = handle_message(
        Incoming("chip-dev", "ping", "member"),
        config,
        chip=chip,
        webhook=lambda *_a: WebhookResult(204),
        environ={"CHIP_WEBHOOK_DEV": "https://example.invalid/dev"},
    )
    assert result.ok
    assert calls == [1]
    assert result.delegate_hops == 0
