"""Offline tests for READ-ONLY AirVPN portal CDP scrape (no live browser)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chip import airvpn_portal
from chip import lanes
from chip import tool
from chip.tools_manifest import resolve_tool


MISMATCH_TEXT = (
    "AirVPN client area\n"
    "Forwarded port 54543 bound to 192.30.89.27\n"
    "status: assigned\n"
)

MATCH_TEXT = (
    "AirVPN client area\n"
    "Forwarded port 54543 bound to 38.88.124.105\n"
    "status: assigned\n"
)


def test_connection_refused_fails_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise airvpn_portal.AirvpnPortalError("CDP HTTP connection failed: Connection refused")

    monkeypatch.setattr(airvpn_portal, "fetch_targets", boom)
    with pytest.raises(airvpn_portal.AirvpnPortalError, match="Connection refused"):
        airvpn_portal.scrape(host="127.0.0.1", port=9222, expected_exit="38.88.124.105")
    assert calls["n"] == 1


def test_no_airvpn_tab_fails_loud() -> None:
    targets = [
        {"url": "https://example.com/", "title": "Example", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/1"},
    ]
    with pytest.raises(airvpn_portal.AirvpnPortalError, match="no airvpn.org tab"):
        airvpn_portal.scrape(
            host="127.0.0.1",
            port=9222,
            expected_exit="38.88.124.105",
            fetch_targets_fn=lambda *_a, **_k: targets,
        )


def test_mismatch_stale_exit_vs_live() -> None:
    receipt = airvpn_portal.scrape(
        host="127.0.0.1",
        expected_exit="38.88.124.105",
        page_text=MISMATCH_TEXT,
    )
    assert receipt["expected_port"] == 54543
    assert receipt["expected_exit"] == "38.88.124.105"
    assert receipt["observed_port"] == 54543
    assert receipt["observed_exit"] == "192.30.89.27"
    assert receipt["mismatch"] is True


def test_matching_exit_mismatch_false() -> None:
    receipt = airvpn_portal.scrape(
        host="127.0.0.1",
        expected_exit="38.88.124.105",
        page_text=MATCH_TEXT,
    )
    assert receipt["mismatch"] is False
    assert receipt["observed_exit"] == "38.88.124.105"
    assert receipt["observed_port"] == 54543


def test_unreadable_page_mismatch_unknown() -> None:
    receipt = airvpn_portal.scrape(
        host="127.0.0.1",
        expected_exit="38.88.124.105",
        page_text="   ",
    )
    assert receipt["mismatch"] is None
    assert receipt["observed_port"] is None
    assert "could not be read" in receipt["reason"]


def test_non_loopback_cdp_host_rejected() -> None:
    with pytest.raises(airvpn_portal.AirvpnPortalError, match="non-loopback"):
        airvpn_portal.assert_loopback_host("192.168.1.10")
    with pytest.raises(airvpn_portal.AirvpnPortalError, match="non-loopback"):
        airvpn_portal.scrape(host="8.8.8.8", page_text=MATCH_TEXT)
    rc = airvpn_portal.main(["--cdp-host", "10.0.0.1", "--cdp-port", "9222"])
    assert rc == 1


def test_main_emits_json_receipt_on_injected_path(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(
        airvpn_portal,
        "scrape",
        lambda **_k: airvpn_portal.build_receipt(
            expected_port=54543,
            expected_exit="38.88.124.105",
            observed_port=54543,
            observed_exit="38.88.124.105",
            mismatch=False,
            reason="port and exit match expected",
        ),
    )
    assert airvpn_portal.main(["--cdp-port", "9222"]) == 0
    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert data["mismatch"] is False


def test_main_nonzero_when_unreadable(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(
        airvpn_portal,
        "scrape",
        lambda **_k: airvpn_portal.build_receipt(
            expected_port=54543,
            expected_exit="38.88.124.105",
            observed_port=None,
            observed_exit=None,
            mismatch=None,
            reason="page text could not be read",
        ),
    )
    assert airvpn_portal.main([]) == 1
    data = json.loads(capsys.readouterr().out.strip())
    assert data["mismatch"] is None


def test_tool_manifest_read_only_and_argv_clean() -> None:
    spec = resolve_tool("airvpn-portal-scrape")
    assert spec is not None
    assert spec.risk == "low"
    assert spec.allow_extra_args is False
    assert spec.argv == ("python3", "-m", "chip.airvpn_portal")
    blob = " ".join(spec.argv)
    assert lanes.refuse_blob(blob) is None
    for deny in ("/mnt/nas/media", "boards/", ".env"):
        assert deny not in blob
    assert "read-only" in spec.description.lower() or "READ-ONLY" in spec.description


def test_tool_not_on_pm_allowlist() -> None:
    pm_tools = lanes.persona_tool_allow("pm")
    assert pm_tools is not None
    assert "airvpn-portal-scrape" not in pm_tools
    with pytest.raises(tool.ToolNotAllowedError, match="outside the pm allowlist"):
        tool.run("airvpn-portal-scrape", execute=True, speaker="pm", room="dev", scope="x")


def test_refuse_blob_rejects_denied_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    # refuse_blob scans argv + cwd string fragments before spawn
    with pytest.raises(tool.ToolGatedError, match="global deny"):
        tool.run(
            "airvpn-portal-scrape",
            execute=True,
            cwd="/mnt/nas/media",
            is_human_or_inject=True,
        )
    with pytest.raises(tool.ToolGatedError, match="global deny"):
        tool.run(
            "airvpn-portal-scrape",
            execute=True,
            cwd=str(tmp_path / "boards" / "x"),
            is_human_or_inject=True,
        )


def test_http_get_connection_refused_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_urlopen(*_a, **_k):
        calls["n"] += 1
        raise OSError("Connection refused")

    monkeypatch.setattr(airvpn_portal.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(airvpn_portal.AirvpnPortalError, match="connection failed"):
        airvpn_portal.fetch_targets("127.0.0.1", 9222)
    assert calls["n"] == 1


def test_evaluate_refuses_non_constant_expression() -> None:
    with pytest.raises(airvpn_portal.AirvpnPortalError, match="non-constant"):
        airvpn_portal.cdp_runtime_evaluate("ws://127.0.0.1:9222/devtools/page/x", "alert(1)")


def test_live_path_uses_airvpn_tab_and_evaluate(monkeypatch: pytest.MonkeyPatch) -> None:
    targets = [
        {
            "url": "https://airvpn.org/client/",
            "title": "AirVPN",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/abc",
        }
    ]

    def fake_eval(ws_url: str, expression: str, *, timeout: float = 5.0) -> str:
        assert "127.0.0.1" in ws_url
        assert expression == airvpn_portal.PAGE_TEXT_EXPR
        return MISMATCH_TEXT

    receipt = airvpn_portal.scrape(
        host="127.0.0.1",
        port=9222,
        expected_exit="38.88.124.105",
        fetch_targets_fn=lambda *_a, **_k: targets,
        evaluate_fn=fake_eval,
    )
    assert receipt["mismatch"] is True
    assert receipt["observed_exit"] == "192.30.89.27"
