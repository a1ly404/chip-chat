"""READ-ONLY AirVPN client-area scrape via local Chrome DevTools Protocol.

Attaches to an already-running Chrome on loopback only. Does not launch a
browser, store credentials/cookies, click, Save, or rebind. Portal writes and
rebind belong to a future slice; this module only detects mismatch.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import struct
import sys
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import urlparse


def _is_private_or_loopback_ipv4(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return True
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return True
    if a == 10 or a == 127 or a == 0:
        return True
    if a == 192 and b == 168:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    return False

EXPECTED_PORT = 54543
DEFAULT_CDP_HOST = "127.0.0.1"
DEFAULT_CDP_PORT = 9222
# Documented example only; operators set AIRVPN_EXPECT_EXIT for their live egress.
DEFAULT_EXPECT_EXIT_EXAMPLE = "38.88.124.105"
CDP_TIMEOUT_S = 5.0
AIRVPN_HOST_MARK = "airvpn.org"
# Constant expression only — reads visible text; no user-supplied JS; no invented selectors.
PAGE_TEXT_EXPR = "document.body && document.body.innerText || ''"

_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
)
_PORT_NEAR_IP_RE = re.compile(
    rf"(?P<port>{EXPECTED_PORT}).{{0,120}}?(?P<ip>" + _IPV4_RE.pattern + r")"
    r"|"
    rf"(?P<ip2>" + _IPV4_RE.pattern + rf").{{0,120}}?(?P<port2>{EXPECTED_PORT})",
    re.IGNORECASE | re.DOTALL,
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class AirvpnPortalError(Exception):
    """Fail-loud scrape error (no retry)."""


def assert_loopback_host(host: str) -> None:
    cleaned = (host or "").strip().lower()
    if cleaned not in LOOPBACK_HOSTS:
        raise AirvpnPortalError(
            f"refusing non-loopback CDP host {host!r}; only 127.0.0.1/localhost allowed"
        )


def cdp_port_from_env(environ: dict[str, str] | None = None) -> int:
    env = environ if environ is not None else os.environ
    raw = (env.get("AIRVPN_CDP_PORT") or "").strip()
    if not raw:
        return DEFAULT_CDP_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise AirvpnPortalError(f"invalid AIRVPN_CDP_PORT {raw!r}") from exc
    if not (1 <= port <= 65535):
        raise AirvpnPortalError(f"AIRVPN_CDP_PORT out of range: {port}")
    return port


def expected_exit_from_env(environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    raw = (env.get("AIRVPN_EXPECT_EXIT") or "").strip()
    return raw or DEFAULT_EXPECT_EXIT_EXAMPLE


def http_get_json(url: str, *, timeout: float = CDP_TIMEOUT_S) -> Any:
    """Single HTTP GET with short timeout; no retry on failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.URLError as exc:
        raise AirvpnPortalError(f"CDP HTTP connection failed: {exc}") from exc
    except TimeoutError as exc:
        raise AirvpnPortalError(f"CDP HTTP timed out after {timeout}s") from exc
    except OSError as exc:
        raise AirvpnPortalError(f"CDP HTTP connection failed: {exc}") from exc
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AirvpnPortalError("CDP HTTP response was not JSON") from exc


def fetch_targets(host: str, port: int, *, timeout: float = CDP_TIMEOUT_S) -> list[dict[str, Any]]:
    assert_loopback_host(host)
    url = f"http://{host}:{port}/json/list"
    raw = http_get_json(url, timeout=timeout)
    if not isinstance(raw, list):
        raise AirvpnPortalError("CDP /json/list did not return a list")
    return [row for row in raw if isinstance(row, dict)]


def find_airvpn_target(targets: list[dict[str, Any]]) -> dict[str, Any]:
    for row in targets:
        url = str(row.get("url") or "")
        title = str(row.get("title") or "")
        blob = f"{url} {title}".lower()
        if AIRVPN_HOST_MARK in blob:
            return row
    raise AirvpnPortalError(
        "no airvpn.org tab in CDP target list — open the AirVPN client area "
        "(login/page missing); refusing to invent navigation"
    )


def _ws_recv_frame(sock: socket.socket) -> bytes:
    header = sock.recv(2)
    if len(header) < 2:
        raise AirvpnPortalError("CDP WebSocket closed before frame header")
    b1, b2 = header[0], header[1]
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        ext = sock.recv(2)
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = sock.recv(8)
        length = struct.unpack("!Q", ext)[0]
    mask_key = sock.recv(4) if masked else b""
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            raise AirvpnPortalError("CDP WebSocket closed mid-frame")
        payload += chunk
    if masked:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    opcode = b1 & 0x0F
    if opcode == 0x8:
        raise AirvpnPortalError("CDP WebSocket closed by peer")
    if opcode != 0x1:
        raise AirvpnPortalError(f"unexpected CDP WebSocket opcode {opcode}")
    return payload


def _ws_send_text(sock: socket.socket, text: str) -> None:
    data = text.encode("utf-8")
    mask_key = os.urandom(4)
    header = bytearray([0x81])
    n = len(data)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))
    header.extend(mask_key)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
    sock.sendall(header + masked)


def cdp_runtime_evaluate(ws_url: str, expression: str, *, timeout: float = CDP_TIMEOUT_S) -> str:
    """Tiny CDP Runtime.evaluate over WebSocket. ``expression`` must be a source constant."""
    if expression != PAGE_TEXT_EXPR:
        raise AirvpnPortalError("refusing non-constant Runtime.evaluate expression")
    parsed = urlparse(ws_url)
    if parsed.scheme not in ("ws", "http"):
        raise AirvpnPortalError(f"unsupported CDP websocket URL scheme: {parsed.scheme!r}")
    host = parsed.hostname or ""
    assert_loopback_host(host)
    port = parsed.port or DEFAULT_CDP_PORT
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(req.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise AirvpnPortalError("CDP WebSocket handshake failed")
            buf += chunk
        status_line = buf.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
        if "101" not in status_line:
            raise AirvpnPortalError(f"CDP WebSocket handshake refused: {status_line}")

        msg = {
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": PAGE_TEXT_EXPR, "returnByValue": True},
        }
        _ws_send_text(sock, json.dumps(msg))
        while True:
            raw = _ws_recv_frame(sock)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AirvpnPortalError("CDP WebSocket frame was not JSON") from exc
            if payload.get("id") != 1:
                continue
            result = payload.get("result") or {}
            if "exceptionDetails" in result:
                raise AirvpnPortalError("Runtime.evaluate raised on the page")
            value = (result.get("result") or {}).get("value")
            if not isinstance(value, str):
                raise AirvpnPortalError("page text could not be read (empty/non-string)")
            return value
    finally:
        sock.close()


def parse_portal_text(text: str) -> tuple[int | None, str | None]:
    """Extract reserved port and a bound public IP from scraped body text.

    No DOM selectors — text scan only. Selectors remain unknown until a real
    attach documents them.
    """
    if not (text or "").strip():
        return None, None
    observed_port: int | None = None
    observed_exit: str | None = None
    if re.search(rf"\b{EXPECTED_PORT}\b", text):
        observed_port = EXPECTED_PORT
    match = _PORT_NEAR_IP_RE.search(text)
    if match:
        observed_port = EXPECTED_PORT
        observed_exit = match.group("ip") or match.group("ip2")
    if observed_exit is None:
        ips = _IPV4_RE.findall(text)
        # Prefer non-RFC1918 / non-loopback when several appear.
        public = [ip for ip in ips if not _is_private_or_loopback_ipv4(ip)]
        if public:
            observed_exit = public[0]
        elif ips:
            observed_exit = ips[0]
    return observed_port, observed_exit


def build_receipt(
    *,
    expected_port: int,
    expected_exit: str,
    observed_port: int | None,
    observed_exit: str | None,
    mismatch: bool | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "expected_port": expected_port,
        "expected_exit": expected_exit,
        "observed_port": observed_port,
        "observed_exit": observed_exit,
        "mismatch": mismatch,
        "reason": reason,
    }


def compare_observation(
    *,
    expected_port: int,
    expected_exit: str,
    observed_port: int | None,
    observed_exit: str | None,
) -> dict[str, Any]:
    if observed_port is None or observed_exit is None:
        return build_receipt(
            expected_port=expected_port,
            expected_exit=expected_exit,
            observed_port=observed_port,
            observed_exit=observed_exit,
            mismatch=None,
            reason="page text could not yield both port and exit IP",
        )
    if observed_port != expected_port or observed_exit != expected_exit:
        return build_receipt(
            expected_port=expected_port,
            expected_exit=expected_exit,
            observed_port=observed_port,
            observed_exit=observed_exit,
            mismatch=True,
            reason=(
                f"mismatch: observed {observed_exit}:{observed_port} "
                f"vs expected {expected_exit}:{expected_port}"
            ),
        )
    return build_receipt(
        expected_port=expected_port,
        expected_exit=expected_exit,
        observed_port=observed_port,
        observed_exit=observed_exit,
        mismatch=False,
        reason="port and exit match expected",
    )


def scrape(
    *,
    host: str = DEFAULT_CDP_HOST,
    port: int | None = None,
    expected_exit: str | None = None,
    timeout: float = CDP_TIMEOUT_S,
    fetch_targets_fn: Callable[..., list[dict[str, Any]]] | None = None,
    evaluate_fn: Callable[..., str] | None = None,
    page_text: str | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Attach (or use injected text) and emit a detection receipt.

    When ``page_text`` is provided, CDP is skipped (unit tests). Live path
    never retries on failure.
    """
    assert_loopback_host(host)
    cdp_port = DEFAULT_CDP_PORT if port is None else port
    if port is None:
        cdp_port = cdp_port_from_env(environ)
    expect = expected_exit if expected_exit is not None else expected_exit_from_env(environ)

    if page_text is None:
        targets_fn = fetch_targets_fn or fetch_targets
        targets = targets_fn(host, cdp_port, timeout=timeout)
        target = find_airvpn_target(targets)
        ws_url = str(target.get("webSocketDebuggerUrl") or "").strip()
        if not ws_url:
            raise AirvpnPortalError("airvpn.org tab has no webSocketDebuggerUrl")
        eval_fn = evaluate_fn or cdp_runtime_evaluate
        page_text = eval_fn(ws_url, PAGE_TEXT_EXPR, timeout=timeout)

    if not (page_text or "").strip():
        receipt = build_receipt(
            expected_port=EXPECTED_PORT,
            expected_exit=expect,
            observed_port=None,
            observed_exit=None,
            mismatch=None,
            reason="page text could not be read",
        )
        return receipt

    observed_port, observed_exit = parse_portal_text(page_text)
    return compare_observation(
        expected_port=EXPECTED_PORT,
        expected_exit=expect,
        observed_port=observed_port,
        observed_exit=observed_exit,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="READ-ONLY AirVPN portal scrape via local CDP (no rebind)"
    )
    parser.add_argument(
        "--cdp-host",
        default=DEFAULT_CDP_HOST,
        help="CDP host (loopback only; default 127.0.0.1)",
    )
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=None,
        help=f"CDP port (default env AIRVPN_CDP_PORT or {DEFAULT_CDP_PORT})",
    )
    args = parser.parse_args(argv)

    try:
        assert_loopback_host(args.cdp_host)
        port = args.cdp_port if args.cdp_port is not None else cdp_port_from_env()
        receipt = scrape(host=args.cdp_host, port=port)
    except AirvpnPortalError as exc:
        fail = build_receipt(
            expected_port=EXPECTED_PORT,
            expected_exit=expected_exit_from_env(),
            observed_port=None,
            observed_exit=None,
            mismatch=None,
            reason=str(exc),
        )
        print(json.dumps(fail, separators=(",", ":")))
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(receipt, separators=(",", ":")))
    if receipt.get("mismatch") is None:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
