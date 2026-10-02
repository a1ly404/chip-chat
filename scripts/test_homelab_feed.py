"""Homelab feed receipts, read-only allowlist, scoped GO stub."""

import json
from datetime import datetime, timezone
from pathlib import Path

from chip.go_scope import propose_go_marker
from chip_relay import homelab_probes, monitor_feed

REPO = Path(__file__).resolve().parents[1]
_NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def _runner(mapping):
    def run(argv):
        homelab_probes.assert_read_argv(argv)
        key = tuple(argv)
        if key in mapping:
            return mapping[key]
        if key == tuple(homelab_probes.WG_ROUTE_ARGV):
            return (0, "default dev wg0\n", "")
        if key in (tuple(homelab_probes.WG_IPTABLES_ARGV), tuple(homelab_probes.WG_IP6TABLES_ARGV)):
            return (0, "", "")
        return (127, "", "unreachable: missing")
    return run


def test_dockerfile_installs_docker_cli() -> None:
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "download.docker.com/linux/static" in text
    assert "/usr/local/bin" in text


def test_read_tools_are_narrow() -> None:
    spec = json.loads((REPO / "config" / "chip_tools.json").read_text(encoding="utf-8"))
    by_id = {t["id"]: t for t in spec["tools"]}
    assert by_id["docker-inspect"]["argv"] == homelab_probes.DOCKER_INSPECT_ARGV + ["qbittorrent"]
    assert by_id["docker-logs"]["argv"] == homelab_probes.DOCKER_LOGS_ARGV
    assert by_id["wg-show"]["argv"] == homelab_probes.WG_SHOW_ARGV
    for tid in ("docker-inspect", "docker-logs"):
        assert by_id[tid]["allow_extra_args"] is False
        blob = " ".join(by_id[tid]["argv"])
        for banned in (" run", " rm", "compose", "restart", " exec"):
            assert banned not in f" {blob}"
    assert by_id["wg-show"]["allow_extra_args"] is False
    wg_blob = " ".join(by_id["wg-show"]["argv"])
    for banned in (" run", " rm", "compose", "restart"):
        assert banned not in f" {wg_blob}"
    assert by_id["wg-show"]["argv"][:5] == ["docker", "exec", "wireguard", "wg", "show"]
    assert by_id["wg-route"]["argv"] == homelab_probes.WG_ROUTE_ARGV
    assert by_id["wg-iptables-drop"]["argv"] == homelab_probes.WG_IPTABLES_ARGV
    assert by_id["wg-ip6tables-drop"]["argv"] == homelab_probes.WG_IP6TABLES_ARGV


def test_runbook_classes_unchanged() -> None:
    from chip import runbooks

    assert runbooks.runbook_class("al-9501-dead-image") == "propose"
    assert runbooks.runbook_class("compose-recreate") == "propose"
    assert runbooks.runbook_class("disk-cleanup") == "propose"
    assert runbooks.runbook_class("remux") == "blocked"
    assert runbooks.may_autorun("docker-inspect") is False
    assert runbooks.may_autorun("restart-named-service") is True


def test_missing_wg0_and_low_disk_and_image_line() -> None:
    fresh = ""
    alerts = homelab_probes.collect_alerts(
        runner=_runner({
            tuple(homelab_probes.WG_SHOW_ARGV): (1, "", "Unable to access interface: No such device"),
            tuple(homelab_probes.DOCKER_LOGS_ARGV): (0, "Error: No such image: monitoring-api:dead\n", ""),
            tuple(homelab_probes.DOCKER_INSPECT_ARGV + ["qbittorrent"]): (
                0,
                json.dumps({"State": {"Status": "running"}, "Mounts": []}),
                "",
            ),
        }),
        downloads_path=None,
        qb_url="",
    )
    keys = {a["key"] for a in alerts}
    assert "wg0-missing" in keys
    assert "no-such-image" in keys
    assert "downloads-mount" in keys
    for a in alerts:
        body = str(a["receipt"])
        assert body.startswith("claim:")
        assert "\nstatus:" in body and "\nevidence:" in body and "\nnext:" in body
    assert fresh == ""


def test_stale_handshake_and_firewalled_up_container(tmp_path: Path) -> None:
    old = "peerkey 1\n"
    mounts = {
        "State": {"Status": "running"},
        "Mounts": [{"Destination": "/downloads", "Name": "razzor-nas-torrents", "Type": "volume"}],
    }
    path = tmp_path / "dl"
    path.mkdir()
    alerts = homelab_probes.collect_alerts(
        runner=_runner({
            tuple(homelab_probes.WG_SHOW_ARGV): (0, old, ""),
            tuple(homelab_probes.DOCKER_LOGS_ARGV): (0, "ok\n", ""),
            tuple(homelab_probes.DOCKER_INSPECT_ARGV + ["qbittorrent"]): (0, json.dumps(mounts), ""),
        }),
        downloads_path=str(path),
        qb_url="",
        now=10_000,
    )
    by = {a["key"]: a for a in alerts}
    assert "wg0-handshake" in by
    assert "stale" in str(by["wg0-handshake"]["receipt"])
    assert "downloads-free" not in by or "under-2gib" in str(by.get("downloads-free", {}).get("receipt", ""))


def test_unreachable_is_blocked_not_raise() -> None:
    alerts = homelab_probes.collect_alerts(
        runner=lambda argv: (127, "", "unreachable: docker not on PATH"),
        downloads_path=None,
        qb_url="http://127.0.0.1:9/api/v2/transfer/info",
    )
    assert alerts
    assert all("status: blocked" in str(a["receipt"]) for a in alerts)


def test_homelab_wake_gate_blocks_discord(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHIP_FEED_ENABLED", "1")
    monkeypatch.setenv("CHIP_WEBHOOK_DEV", "https://example.invalid/hook")
    posted = []

    def hook(url, username, content):
        posted.append(content)
        class R:
            status = 204
        return R()

    receipt = {
        "verdicts": [
            {
                "key": "wg0-missing",
                "detail": "tool:wg-show.missing-wg0",
                "verdict": "actionable",
                "receipt": homelab_probes.four_line("x", "refused", "tool:wg-show.missing-wg0", "next"),
            }
        ]
    }
    assert monitor_feed.post_alerts(receipt, webhook=hook, now=1_000.0, only_fps=None) == 0
    assert posted == []
    monkeypatch.setenv("CHIP_FEED_HOMELAB_WAKE", "1")
    assert monitor_feed.post_alerts(receipt, webhook=hook, now=1_000.0, only_fps=None) == 1


def test_feed_posts_four_line_once_per_cooldown(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHIP_FEED_ENABLED", "1")
    monkeypatch.setenv("CHIP_FEED_HOMELAB_WAKE", "1")
    monkeypatch.setenv("CHIP_WEBHOOK_DEV", "https://example.invalid/hook")
    posted = []

    def hook(url, username, content):
        posted.append(content)
        class R:
            status = 204
        return R()

    receipt = {
        "verdicts": [
            {
                "key": "wg0-missing",
                "detail": "tool:wg-show.missing-wg0",
                "verdict": "actionable",
                "receipt": homelab_probes.four_line(
                    "wg0 interface missing",
                    "refused",
                    "tool:wg-show.missing-wg0",
                    "propose compose-recreate only after scoped GO",
                ),
            }
        ]
    }
    n = monitor_feed.post_alerts(receipt, webhook=hook, now=1_000.0, only_fps=None)
    assert n == 1
    assert posted[0].splitlines()[0].startswith("claim:")
    assert len(posted[0].splitlines()) == 4
    n2 = monitor_feed.post_alerts(receipt, webhook=hook, now=1_100.0, only_fps=None)
    assert n2 == 0


def test_killswitch_faults_when_route_or_drop_missing() -> None:
    alerts = homelab_probes.collect_alerts(
        runner=_runner({
            tuple(homelab_probes.WG_SHOW_ARGV): (0, "peer 1\n", ""),
            tuple(homelab_probes.WG_ROUTE_ARGV): (0, "default via 192.168.1.1 dev eth0\n", ""),
            tuple(homelab_probes.DOCKER_LOGS_ARGV): (0, "", ""),
            tuple(homelab_probes.DOCKER_INSPECT_ARGV + ["qbittorrent"]): (
                0,
                json.dumps({"State": {"Status": "running"}, "Mounts": [{"Destination": "/downloads", "Name": "razzor-nas-torrents"}]}),
                "",
            ),
        }),
        downloads_path=None,
        qb_url="",
    )
    hit = [a for a in alerts if a["key"] == "wg-killswitch"]
    assert hit
    assert "not-wg0" in str(hit[0]["receipt"])
    alerts = homelab_probes.collect_alerts(
        runner=_runner({
            tuple(homelab_probes.WG_SHOW_ARGV): (0, "peer 1\n", ""),
            tuple(homelab_probes.WG_IPTABLES_ARGV): (1, "", "Bad rule"),
            tuple(homelab_probes.DOCKER_LOGS_ARGV): (0, "", ""),
            tuple(homelab_probes.DOCKER_INSPECT_ARGV + ["qbittorrent"]): (
                0,
                json.dumps({"State": {"Status": "running"}, "Mounts": [{"Destination": "/downloads", "Name": "razzor-nas-torrents"}]}),
                "",
            ),
        }),
        downloads_path=None,
        qb_url="",
    )
    hit = [a for a in alerts if a["key"] == "wg-killswitch"]
    assert hit and "missing-drop" in str(hit[0]["receipt"])
    assert homelab_probes.is_homelab_feed_alert(hit[0])


def test_is_homelab_feed_alert_classifies_keys() -> None:
    assert homelab_probes.is_homelab_feed_alert({"key": "wg0-handshake", "detail": "tool:wg-show.stale"})
    assert homelab_probes.is_homelab_feed_alert({"key": "no-such-image", "detail": "tool:docker-logs.no-such-image"})
    assert not homelab_probes.is_homelab_feed_alert({"key": "monitoring-api", "detail": "status=degraded"})
    assert not homelab_probes.is_homelab_feed_alert({"key": "no-such-image", "detail": "pull failed"})


def test_tick_homelab_faults_classify_when_enabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHIP_FEED_HOMELAB", "1")
    monkeypatch.setattr(monitor_feed, "_fetch_health", lambda host, port, name: {})
    monkeypatch.setattr(
        monitor_feed,
        "collect_homelab_alerts",
        lambda environ=None: [
            {
                "key": "downloads-mount",
                "detail": "tool:docker-inspect.downloads-overlay",
                "ok": False,
                "receipt": homelab_probes.four_line("m", "refused", "tool:docker-inspect.downloads-overlay", "next"),
            }
        ],
    )
    receipt = monitor_feed.tick(persist=False)
    keys = {v["key"]: v.get("verdict") for v in receipt["verdicts"]}
    assert keys.get("downloads-mount") == "actionable"


def test_homelab_off_adds_no_alerts(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CHIP_FEED_HOMELAB", raising=False)
    assert monitor_feed.collect_homelab_alerts() == []


def test_text_line_no_such_image() -> None:
    from chip import text_sources

    out = text_sources.ingest_lines([{"text": "pull failed: No such image: dead:tag"}])
    assert any(a["key"] == "no-such-image" for a in out["alerts"])


def test_propose_stub_requires_future_scoped_go() -> None:
    missing, why = propose_go_marker("GO: do the thing")
    assert missing is None and "fp=" in why
    ok, _ = propose_go_marker(
        "GO: fp=wg0-missing lane=razzorstack exp=2026-10-01T00:00:00Z",
        now=_NOW,
    )
    assert ok is not None
    expired, why2 = propose_go_marker(
        "GO: fp=wg0-missing lane=razzorstack exp=2020-01-01T00:00:00Z",
        now=_NOW,
    )
    assert expired is None and "expired" in why2


def test_api_base_from_transfer_url() -> None:
    assert homelab_probes._api_base_from_transfer_url(
        "http://host.docker.internal:8080/api/v2/transfer/info"
    ) == "http://host.docker.internal:8080"


def test_qb_403_without_credentials_is_blocked(monkeypatch) -> None:
    monkeypatch.setattr(
        homelab_probes,
        "_read_transfer_info",
        lambda opener, url, timeout: (None, "HTTPError403"),
    )
    status, err = homelab_probes._fetch_connection("http://qb:8080/api/v2/transfer/info")
    assert status is None
    assert err == "HTTPError403"


def test_qb_403_login_then_transfer_info(monkeypatch) -> None:
    attempts = {"n": 0}
    login_calls: list[tuple[str, str, str]] = []

    def read_info(opener, url, timeout):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return None, "HTTPError403"
        return {"connection_status": "connected"}, None

    def login(opener, base, user, password, timeout):
        login_calls.append((base, user, password))
        return None

    monkeypatch.setattr(homelab_probes, "_read_transfer_info", read_info)
    monkeypatch.setattr(homelab_probes, "_qb_login", login)
    status, err = homelab_probes._fetch_connection(
        "http://qb:8080/api/v2/transfer/info",
        qb_user="relay",
        qb_password="secret-not-in-receipts",
    )
    assert err is None
    assert status == "connected"
    assert login_calls == [("http://qb:8080", "relay", "secret-not-in-receipts")]
    alerts = homelab_probes.collect_alerts(
        runner=lambda argv: (0, "", ""),
        downloads_path=None,
        qb_url="http://qb:8080/api/v2/transfer/info",
        qb_user="relay",
        qb_password="secret-not-in-receipts",
    )
    receipt_blob = json.dumps(alerts)
    assert "secret-not-in-receipts" not in receipt_blob


def test_qb_login_failure_blocked(monkeypatch) -> None:
    monkeypatch.setattr(
        homelab_probes,
        "_read_transfer_info",
        lambda opener, url, timeout: (None, "HTTPError403"),
    )
    monkeypatch.setattr(
        homelab_probes,
        "_qb_login",
        lambda opener, base, user, password, timeout: "login-failed",
    )
    status, err = homelab_probes._fetch_connection(
        "http://qb:8080/api/v2/transfer/info",
        qb_user="u",
        qb_password="p",
    )
    assert status is None
    assert err == "login-failed"
    alerts = homelab_probes.collect_alerts(
        runner=lambda argv: (127, "", "unreachable"),
        downloads_path=None,
        qb_url="http://qb:8080/api/v2/transfer/info",
        qb_user="u",
        qb_password="p",
    )
    qb = [a for a in alerts if a["key"] == "qb-connection"]
    assert qb
    assert "login-failed" in str(qb[0]["receipt"])
    assert "status: blocked" in str(qb[0]["receipt"])


def test_wrong_mount_and_firewalled(monkeypatch) -> None:
    body = json.dumps({
        "State": {"Status": "running"},
        "Mounts": [{"Destination": "/downloads", "Name": "overlay", "Source": "/var/lib/docker/overlay2"}],
    })

    def run(argv):
        if tuple(argv) == tuple(homelab_probes.WG_SHOW_ARGV):
            return 0, "peer 9999999999\n", ""
        if argv[:2] == ["docker", "logs"]:
            return 0, "", ""
        return 0, body, ""

    class Resp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b'{"connection_status":"firewalled"}'

    monkeypatch.setattr(homelab_probes.urllib.request, "urlopen", lambda *a, **k: Resp())
    alerts = homelab_probes.collect_alerts(runner=run, downloads_path=None, qb_url="http://qb/api", now=100)
    keys = {a["key"] for a in alerts}
    assert "downloads-mount" in keys
    assert "qb-connection" in keys
