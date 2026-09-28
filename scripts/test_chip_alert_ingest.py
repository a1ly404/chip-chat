"""RAZ-332 Step 3 dispatcher tests (shadow-mode law; live-HTTP smoke offline).

- route table is MONITOR_CASES-anchored; unmatched fail-closed + banner
- shadow: every record logged, shadow:true, NOTHING executes
- LIVE endpoint smoke: real HTTPServer (free port) — GET /health untouched,
  POST /alert 401 without bearer, 202 + JSONL row with bearer (isolated tmp
  data dir via CHIP_CHAT_DATA_DIR)
- Garbage body -> 400 structurally (no crash, no silent drop)
"""

import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from chip import config
from chip_relay import alert_ingest


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def relay_server(tmp_path):
    import os

    os.environ["CHIP_CHAT_DATA_DIR"] = str(tmp_path / "appdata")
    (tmp_path / "appdata").mkdir(exist_ok=True)
    from chip_relay import health_server as hs

    config = hs  # module-level functions used by the server
    srv = hs.HTTPServer(("127.0.0.1", _free_port()), hs._HealthHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv, tmp_path / "appdata", tmp_path
    srv.shutdown()


ALERT = {"alert_id": "al-9501", "key": "internal-loop-degrade", "detail": "404 loop"}


# --- route table --------------------------------------------------------------

def test_routes_cover_monitor_cases():
    needed = ("stage1-advisor", "pgs-zero-production", "internal-loop-degrade",
              "gold-signal-down", "suppression-saturation", "skill-runner-fault")
    for n in needed:
        assert n in alert_ingest.ROUTE_TABLE, f"missing: {n}"


def test_dispatch_record_shape():
    rec = alert_ingest.dispatch_record(ALERT)
    assert set(rec) >= {"ts", "alert_id", "key", "skill_selected", "matched", "shadow"}
    assert rec["shadow"] is True
    assert rec["matched"] is True


def test_unmatched_class_never_invented():
    skill, verdict = alert_ingest.classify({"alert_id": "a2", "key": "brand-new-case"})
    assert skill.startswith("UNMATCHED")
    assert verdict == "unmatched"


def test_banner_only_for_unmatched():
    assert alert_ingest.unmatched_banner({"key": "x", "matched": False}) is not None
    assert alert_ingest.unmatched_banner({"key": "y", "matched": True}) is None


# --- ingest law ----------------------------------------------------------------

def test_handle_alert_garbage_fails_closed():
    assert alert_ingest.handle_alert({})["status"] == 400
    assert alert_ingest.handle_alert("not a dict")["status"] == 400


def test_handle_alert_appends_jsonl_row():
    rec = alert_ingest.handle_alert(ALERT)["record"]
    path = config.data_dir() / "chip_state" / "alert_dispatch.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    assert rows[-1]["alert_id"] == rec["alert_id"]
    assert rows[-1]["matched"] is True and rows[-1]["shadow"] is True


# --- LIVE HTTP ----------------------------------------------------------------

def test_live_health_and_alert_post(relay_server):
    srv, appdata, tmp_path = relay_server
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    # GET /health must be untouched by the alert face (gateway may still be in
    # its pre-ready state in a fresh process — 200 or 503 are both VALID shapes)
    try:
        body = urllib.request.urlopen(base + "/health", timeout=5).read()
        assert body in (b"ok\n", b"starting\n")
    except urllib.error.HTTPError as e:
        assert e.code == 503  # starting shape
    # POST /alert without token → 401
    req = urllib.request.Request(base + "/alert", data=json.dumps(ALERT).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 401
    # POST with bearer → 202 + JSONL row
    with_token = urllib.request.Request(base + "/alert", data=json.dumps(ALERT).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer tok"},
        method="POST")
    import os

    os.environ["CHIP_ALERT_INGEST_TOKEN"] = "tok"
    resp = urllib.request.urlopen(with_token, timeout=5)
    assert resp.status == 202
    rows = [json.loads(l) for l in
            (appdata / "chip_state" / "alert_dispatch.jsonl").read_text().splitlines()]
    assert rows[-1]["alert_id"] == "al-9501"
    # garbage body → 400 (fail-closed, visible)
    bad = urllib.request.Request(base + "/alert", data=b"{nope",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer tok"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as ei2:
        urllib.request.urlopen(bad, timeout=5)
    assert ei2.value.code == 400
