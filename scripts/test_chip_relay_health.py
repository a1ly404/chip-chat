from __future__ import annotations
"""Health HTTP endpoint tests (no Discord)."""


import urllib.error
import urllib.request

from chip_relay.health_server import (
    clear_gateway_ready,
    gateway_ready,
    health_port,
    set_gateway_ready,
    start_health_server,
)


def test_health_port_default() -> None:
    assert health_port() == 48082


def test_health_503_until_gateway_ready(monkeypatch) -> None:
    monkeypatch.setenv("CHIP_RELAY_HEALTH_PORT", "0")
    clear_gateway_ready()
    server = start_health_server()
    try:
        port = server.server_port
        url = f"http://127.0.0.1:{port}/health"
        try:
            urllib.request.urlopen(url, timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            assert exc.read() == b"starting\n"
        else:
            raise AssertionError("expected 503 before gateway ready")
        set_gateway_ready()
        assert gateway_ready()
        with urllib.request.urlopen(url, timeout=2) as resp:
            assert resp.status == 200
            assert resp.read() == b"ok\n"
    finally:
        server.shutdown()
        server.server_close()
        clear_gateway_ready()


def test_health_unknown_path_404(monkeypatch) -> None:
    monkeypatch.setenv("CHIP_RELAY_HEALTH_PORT", "0")
    clear_gateway_ready()
    server = start_health_server()
    try:
        port = server.server_port
        url = f"http://127.0.0.1:{port}/metrics"
        try:
            urllib.request.urlopen(url, timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404")
    finally:
        server.shutdown()
        server.server_close()
