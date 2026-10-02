"""Read-only homelab checks for the monitor feed.

The feed may receipt. It does not mutate. Allowed commands are the baked
argv of ``docker-inspect``, ``docker-logs``, and ``wg-show`` in
``config/chip_tools.json`` — no docker run, rm, compose, or restart.
``wg-show`` uses one read-only ``docker exec wireguard wg show …`` because
``wg0`` lives in the host wireguard container, not the relay netns.

WireGuard handshake threshold: 180 seconds. A missing latest-handshake, or
one older than that, is a fault. Missing docker CLI or failed exec is status
``blocked`` with the miss named in evidence, not a crash.

``/downloads`` must be the ``razzor-nas-torrents`` mount (inspect Mounts),
and that filesystem must have at least 2 GiB free when the path is visible
in this container. qBittorrent ``connection_status`` is read from
``CHIP_FEED_QB_URL`` when set (optional ``CHIP_FEED_QB_USER`` /
``CHIP_FEED_QB_PASSWORD`` for WebUI login from Docker); ``firewalled`` is a fault.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

FEED_QB_USER_ENV = "CHIP_FEED_QB_USER"
FEED_QB_PASSWORD_ENV = "CHIP_FEED_QB_PASSWORD"
_TRANSFER_INFO_SUFFIX = "/api/v2/transfer/info"

HANDSHAKE_STALE_S = 180
DOWNLOADS_MIN_BYTES = 2 * 1024 * 1024 * 1024
DOWNLOADS_PATH = "/downloads"
TORRENT_VOLUME = "razzor-nas-torrents"

# Keys produced by ``collect_alerts`` (P2 homelab tell-tier). Used by the feed to
# gate Discord wake until ``CHIP_FEED_HOMELAB_WAKE=1`` (Ally GO).
HOMELAB_ALERT_KEYS = frozenset(
    {
        "wg0-handshake",
        "wg0-missing",
        "downloads-mount",
        "downloads-free",
        "qb-connection",
        "homelab-probes",
        "wg-killswitch",
    }
)

Runner = Callable[[list[str]], tuple[int, str, str]]

DOCKER_INSPECT_ARGV = [
    "docker",
    "inspect",
    "--format",
    "{{json .}}",
]
DOCKER_LOGS_ARGV = ["docker", "logs", "--tail", "80", "monitoring-api"]
WG_SHOW_ARGV = [
    "docker",
    "exec",
    "wireguard",
    "wg",
    "show",
    "wg0",
    "latest-handshakes",
]
WG_ROUTE_ARGV = [
    "docker",
    "exec",
    "wireguard",
    "ip",
    "-4",
    "route",
    "show",
    "default",
]
WG_IPTABLES_ARGV = [
    "docker",
    "exec",
    "wireguard",
    "iptables",
    "-C",
    "WGKS_OUT",
    "-o",
    "eth0",
    "-j",
    "DROP",
]
WG_IP6TABLES_ARGV = [
    "docker",
    "exec",
    "wireguard",
    "ip6tables",
    "-C",
    "WGKS6_OUT",
    "-o",
    "eth0",
    "-j",
    "DROP",
]
_READ_EXEC = frozenset(
    {
        tuple(WG_SHOW_ARGV),
        tuple(WG_ROUTE_ARGV),
        tuple(WG_IPTABLES_ARGV),
        tuple(WG_IP6TABLES_ARGV),
    }
)

_FORBIDDEN = (" run", " rm", " compose", " restart")


def is_homelab_feed_alert(alert: dict[str, object]) -> bool:
    """True when the alert came from homelab read-only probes (not health HTTP)."""
    key = str(alert.get("key", ""))
    if key in HOMELAB_ALERT_KEYS:
        return True
    if key == "no-such-image":
        return str(alert.get("detail", "")).startswith("tool:")
    return False


def four_line(claim: str, status: str, evidence: str, nxt: str) -> str:
    return (
        f"claim: {claim}\n"
        f"status: {status}\n"
        f"evidence: {evidence}\n"
        f"next: {nxt}"
    )


def _alert(key: str, claim: str, status: str, evidence: str, nxt: str) -> dict[str, object]:
    detail = evidence[:160]
    return {
        "key": key,
        "detail": detail,
        "ok": False,
        "receipt": four_line(claim, status, evidence, nxt),
    }


def assert_read_argv(argv: list[str]) -> None:
    blob = " ".join(argv).lower()
    for token in _FORBIDDEN:
        if token in f" {blob}":
            raise ValueError(f"refusing non-read argv: {argv}")
    if " exec" in f" {blob}" and tuple(argv) not in _READ_EXEC:
        raise ValueError(f"refusing non-read argv: {argv}")


def default_runner(argv: list[str]) -> tuple[int, str, str]:
    assert_read_argv(argv)
    if argv and shutil.which(argv[0]) is None:
        return 127, "", f"unreachable: {argv[0]} not on PATH"
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except Exception as exc:  # noqa: BLE001 — blocked receipt, never a crash loop
        return 127, "", f"unreachable: {exc.__class__.__name__}: {exc}"[:200]


def _blocked(key: str, claim: str, evidence: str) -> dict[str, object]:
    return _alert(key, claim, "blocked", evidence, "read-only receipt; no mutate from the feed")


def _fault(key: str, claim: str, evidence: str, nxt: str) -> dict[str, object]:
    return _alert(key, claim, "refused", evidence, nxt)


def parse_handshakes(stdout: str, *, now: float | None = None) -> dict[str, object] | None:
    """Return a fault alert, or None when a handshake is fresh."""
    now = time.time() if now is None else now
    newest: float | None = None
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            stamp = float(parts[-1])
        except ValueError:
            continue
        newest = stamp if newest is None else max(newest, stamp)
    if newest is None or newest <= 0:
        return _fault(
            "wg0-handshake",
            "wireguard latest handshake missing",
            "tool:wg-show.no-handshake",
            "propose compose-recreate only after scoped GO",
        )
    age = now - newest
    if age > HANDSHAKE_STALE_S:
        return _fault(
            "wg0-handshake",
            f"wireguard latest handshake stale ({int(age)}s > {HANDSHAKE_STALE_S}s)",
            "tool:wg-show.stale",
            "propose compose-recreate only after scoped GO",
        )
    return None


def wg_alert(runner: Runner, *, now: float | None = None) -> dict[str, object] | None:
    code, out, err = runner(list(WG_SHOW_ARGV))
    blob = f"{out}\n{err}".lower()
    if code == 127 or "not on path" in blob or "unreachable" in blob:
        return _blocked("wg0-handshake", "wg show unreachable from relay", "tool:wg-show.unreachable")
    if code != 0 or "no such device" in blob or "unable to access interface" in blob:
        return _fault(
            "wg0-missing",
            "wg0 interface missing",
            "tool:wg-show.missing-wg0",
            "propose compose-recreate only after scoped GO",
        )
    return parse_handshakes(out, now=now)


def _unreachable(code: int, out: str, err: str) -> bool:
    blob = f"{out}\n{err}".lower()
    return code == 127 or "not on path" in blob or "unreachable" in blob


def killswitch_alert(runner: Runner) -> dict[str, object] | None:
    """Fault when the default route is not wg0 or the eth0 DROP rule is missing."""
    code, out, err = runner(list(WG_ROUTE_ARGV))
    if _unreachable(code, out, err):
        return _blocked("wg-killswitch", "kill switch probe unreachable", "tool:wg-killswitch.unreachable")
    if code != 0 or "dev wg0" not in out:
        return _fault(
            "wg-killswitch",
            "default route is not wg0",
            "tool:wg-route.not-wg0",
            "propose compose-recreate only after scoped GO",
        )
    code, out, err = runner(list(WG_IPTABLES_ARGV))
    if _unreachable(code, out, err):
        return _blocked("wg-killswitch", "kill switch probe unreachable", "tool:wg-killswitch.unreachable")
    if code != 0:
        return _fault(
            "wg-killswitch",
            "eth0 IPv4 drop rule missing",
            "tool:wg-iptables.missing-drop",
            "propose compose-recreate only after scoped GO",
        )
    code, out, err = runner(list(WG_IP6TABLES_ARGV))
    if _unreachable(code, out, err):
        return _blocked("wg-killswitch", "kill switch probe unreachable", "tool:wg-killswitch.unreachable")
    if code != 0:
        return _fault(
            "wg-killswitch",
            "eth0 IPv6 drop rule missing",
            "tool:wg-ip6tables.missing-drop",
            "propose compose-recreate only after scoped GO",
        )
    return None


def downloads_free_alert(path: str = DOWNLOADS_PATH) -> dict[str, object] | None:
    try:
        st = os.statvfs(path)
    except OSError as exc:
        return _blocked(
            "downloads-free",
            "/downloads not visible in relay",
            f"tool:downloads-df.unreachable-{exc.__class__.__name__}",
        )
    free = st.f_bavail * st.f_frsize
    if free < DOWNLOADS_MIN_BYTES:
        return _fault(
            "downloads-free",
            f"/downloads filesystem under 2 GiB (free={free})",
            "tool:downloads-df.under-2gib",
            "disk-cleanup stays propose",
        )
    return None


def mount_alert(inspect_json: str) -> dict[str, object] | None:
    try:
        doc = json.loads(inspect_json)
    except json.JSONDecodeError:
        return _blocked("downloads-mount", "qbittorrent inspect unreadable", "tool:docker-inspect.unreadable")
    if isinstance(doc, list):
        doc = doc[0] if doc else {}
    if not isinstance(doc, dict):
        return _blocked("downloads-mount", "qbittorrent inspect unreadable", "tool:docker-inspect.unreadable")
    mounts = doc.get("Mounts") if isinstance(doc.get("Mounts"), list) else []
    hit = None
    for mnt in mounts:
        if isinstance(mnt, dict) and mnt.get("Destination") == DOWNLOADS_PATH:
            hit = mnt
            break
    if hit is None:
        return _fault(
            "downloads-mount",
            "/downloads is container overlay not razzor-nas-torrents",
            "tool:docker-inspect.downloads-overlay",
            "compose-recreate stays propose",
        )
    name = str(hit.get("Name") or "")
    source = str(hit.get("Source") or "")
    if TORRENT_VOLUME not in name and TORRENT_VOLUME not in source:
        return _fault(
            "downloads-mount",
            "/downloads is not the razzor-nas-torrents mount",
            "tool:docker-inspect.downloads-wrong-mount",
            "compose-recreate stays propose",
        )
    return None


def state_useless_alert(inspect_json: str, *, connection_status: str | None) -> dict[str, object] | None:
    try:
        doc = json.loads(inspect_json)
    except json.JSONDecodeError:
        return None
    if isinstance(doc, list):
        doc = doc[0] if doc else {}
    state = doc.get("State") if isinstance(doc, dict) else None
    if not isinstance(state, dict):
        return None
    running = str(state.get("Status") or "").lower() == "running"
    if running and connection_status == "firewalled":
        return _fault(
            "qb-connection",
            "qbittorrent is Up and connection_status is firewalled",
            "tool:qb-connection.firewalled",
            "compose-recreate stays propose",
        )
    return None


def log_image_alert(text: str) -> dict[str, object] | None:
    if "No such image" not in (text or ""):
        return None
    return _fault(
        "no-such-image",
        "monitoring log line No such image",
        "tool:docker-logs.no-such-image",
        "al-9501-dead-image stays propose",
    )


def _api_base_from_transfer_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = parts.path or ""
    if path.endswith(_TRANSFER_INFO_SUFFIX):
        path = path[: -len(_TRANSFER_INFO_SUFFIX)]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


def _qb_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _read_transfer_info(
    opener: urllib.request.OpenerDirector, url: str, timeout: float
) -> tuple[dict | None, str | None]:
    try:
        with opener.open(url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return None, "HTTPError403"
        return None, exc.__class__.__name__
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return None, exc.__class__.__name__
    if not isinstance(body, dict):
        return None, "unreadable"
    return body, None


def _qb_login(
    opener: urllib.request.OpenerDirector,
    base: str,
    user: str,
    password: str,
    timeout: float,
) -> str | None:
    login_url = f"{base.rstrip('/')}/api/v2/auth/login"
    payload = urllib.parse.urlencode({"username": user, "password": password}).encode("utf-8")
    req = urllib.request.Request(login_url, data=payload, method="POST")
    try:
        with opener.open(req, timeout=timeout) as resp:
            if resp.status != 200:
                return "login-failed"
    except urllib.error.HTTPError:
        return "login-failed"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return exc.__class__.__name__
    return None


def _fetch_connection(
    url: str,
    *,
    qb_user: str | None = None,
    qb_password: str | None = None,
    timeout: float = 5.0,
) -> tuple[str | None, str | None]:
    """Return (connection_status, error). status None + error None means skipped."""
    if not url:
        return None, None
    opener = _qb_opener()
    body, err = _read_transfer_info(opener, url, timeout)
    if err == "HTTPError403" and qb_user and qb_password:
        base = _api_base_from_transfer_url(url)
        login_err = _qb_login(opener, base, qb_user, qb_password, timeout)
        if login_err:
            return None, login_err
        body, err = _read_transfer_info(opener, url, timeout)
    if err:
        return None, err
    if body is None:
        return None, "unreadable"
    return str(body.get("connection_status") or ""), None


def collect_alerts(
    *,
    runner: Runner | None = None,
    downloads_path: str | None = DOWNLOADS_PATH,
    qb_url: str | None = None,
    qb_user: str | None = None,
    qb_password: str | None = None,
    now: float | None = None,
) -> list[dict[str, object]]:
    """Never raises. Unreachable probes become blocked receipts."""
    run = runner or default_runner
    alerts: list[dict[str, object]] = []
    try:
        wg = wg_alert(run, now=now)
    except Exception as exc:  # noqa: BLE001
        wg = _blocked("wg0-handshake", "wg show unreachable from relay", f"tool:wg-show.unreachable-{exc.__class__.__name__}")
    if wg:
        alerts.append(wg)
    try:
        ks = killswitch_alert(run)
    except Exception as exc:  # noqa: BLE001
        ks = _blocked("wg-killswitch", "kill switch probe unreachable", f"tool:wg-killswitch.unreachable-{exc.__class__.__name__}")
    if ks:
        alerts.append(ks)

    code, out, err = run(DOCKER_LOGS_ARGV)
    if code == 127 or "unreachable" in f"{err}".lower():
        alerts.append(_blocked("no-such-image", "docker logs unreachable from relay", "tool:docker-logs.unreachable"))
    else:
        hit = log_image_alert(out + "\n" + err)
        if hit:
            alerts.append(hit)

    code, out, err = run(DOCKER_INSPECT_ARGV + ["qbittorrent"])
    inspect_body = out
    if code == 127 or "unreachable" in f"{err}".lower() or (code != 0 and not out.strip()):
        alerts.append(
            _blocked("downloads-mount", "docker inspect unreachable from relay", "tool:docker-inspect.unreachable")
        )
        inspect_body = ""
    else:
        mounted = mount_alert(out) if out.strip() else _blocked(
            "downloads-mount", "docker inspect qbittorrent empty", "tool:docker-inspect.empty"
        )
        if mounted:
            alerts.append(mounted)

    if downloads_path:
        free = downloads_free_alert(downloads_path)
        if free:
            alerts.append(free)

    status, qerr = _fetch_connection(
        qb_url or "",
        qb_user=qb_user,
        qb_password=qb_password,
    )
    if qerr:
        alerts.append(_blocked("qb-connection", "qbittorrent connection_status unreachable", f"tool:qb-connection.unreachable-{qerr}"))
    elif inspect_body:
        useless = state_useless_alert(inspect_body, connection_status=status)
        if useless:
            alerts.append(useless)
    return alerts
