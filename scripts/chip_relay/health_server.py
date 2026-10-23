"""Local HTTP /health for container healthchecks (internal port 48082 by default)."""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_PORT = 48082
_ready = threading.Event()


def set_gateway_ready() -> None:
    _ready.set()


def clear_gateway_ready() -> None:
    _ready.clear()


def gateway_ready() -> bool:
    return _ready.is_set()


def health_port() -> int:
    raw = os.environ.get("CHIP_RELAY_HEALTH_PORT", "").strip()
    if raw:
        return int(raw)
    return DEFAULT_PORT


def status_payload() -> dict:
    """Read-only relay status: gateway readiness + monthly budget mode (normal/thrift/stopped)."""
    try:
        from chip import budget_mode

        budget = budget_mode.status()
    except Exception as exc:  # noqa: BLE001 — status must never crash the health server
        budget = {"mode": "unknown", "error": exc.__class__.__name__}
    return {"ready": gateway_ready(), "budget": budget}


class _HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            if gateway_ready():
                body = b"ok\n"
                code = 200
            else:
                body = b"starting\n"
                code = 503
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/status":
            self._json_response(200, status_payload())
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # WI-332 Step 3: monitoring>alert ingest (shadow)
        path = self.path.split("?", 1)[0]
        if path != "/alert":
            self.send_response(404)
            self.end_headers()
            return
        import json as _json
        import os as _os

        from chip_relay.alert_ingest import ALERT_TOKEN_ENV, handle_alert

        token = (_os.environ.get(ALERT_TOKEN_ENV, "") or "").strip()
        auth = (self.headers.get("Authorization") or "").strip()
        if not token or auth != f"Bearer {token}":
            self._json_response(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = _json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))
        except (ValueError, _json.JSONDecodeError):
            self._json_response(400, {"ok": False, "error": "invalid json"})
            return
        from chip_relay import event_cases

        if event_cases.is_event(body):
            # Phase 2 step 1: structured events only touch the case store
            # (no handlers, no chat posts, no restarts, no paging).
            try:
                out = event_cases.handle_event(body, environ=dict(_os.environ))
            except Exception:  # noqa: BLE001 - never 5xx-crash the handler thread
                out = {"status": 500, "ok": False, "error": "event store error"}
            self._json_response(out.get("status", 500), out)
            return
        out = handle_alert(body, environ=dict(_os.environ))
        self._json_response(out.get("status", 500), out)

    def _json_response(self, status: int, payload: dict) -> None:
        import json as _json

        body = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_health_server() -> HTTPServer:
    """Bind and serve in a daemon thread. Caller should call set_gateway_ready() when Discord is up."""
    server = HTTPServer(("0.0.0.0", health_port()), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, name="chip-relay-health", daemon=True)
    thread.start()
    return server
