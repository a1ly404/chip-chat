"""Generic event -> case intake (Phase 2 step 1): create / dedupe / resolve."""

from __future__ import annotations

import json

from chip_relay import event_cases as ec


def _body(state="down", service="svc-a", kind="service", **extra):
    event = {
        "kind": kind,
        "service": service,
        "state": state,
        "reason": "down",
        "since": "2026-01-01T00:00:00+00:00",
        "source": "monitor-x",
    }
    event.update(extra)
    return {"alert_id": f"event:{kind}:{service}:{state}", "key": f"event:{kind}", "event": event}


def _run(tmp_path, body, now, mapping=None):
    return ec.handle_event(body, data_dir=tmp_path, now=now, mapping=mapping or {})


def _cases(tmp_path):
    return json.loads((tmp_path / "case_intake" / ec.STORE_NAME).read_text(encoding="utf-8"))


def test_is_event():
    assert ec.is_event(_body())
    assert not ec.is_event({"alert_id": "a", "key": "k"})
    assert not ec.is_event([])


def test_first_event_creates_exactly_one_case(tmp_path):
    out = _run(tmp_path, _body(), 100)
    assert out["status"] == 202 and out["action"] == "created" and out["count"] == 1
    cases = _cases(tmp_path)
    assert list(cases) == [ec.fingerprint("service", "svc-a")]
    row = cases[out["case_id"]]
    assert row["status"] == "open" and row["state"] == "down" and row["source"] == "monitor-x"


def test_repeat_dedupes(tmp_path):
    _run(tmp_path, _body(), 100)
    out = _run(tmp_path, _body(), 200)
    assert out["action"] == "bumped" and out["deduped"] is True and out["count"] == 2
    cases = _cases(tmp_path)
    assert len(cases) == 1
    row = next(iter(cases.values()))
    assert row["last_seen"] == 200 and len(row["history"]) == 1


def test_state_change_updates_same_case(tmp_path):
    _run(tmp_path, _body("grace"), 100)
    out = _run(tmp_path, _body("down"), 200)
    assert out["action"] == "updated"
    assert len(_cases(tmp_path)) == 1


def test_up_resolves_and_down_reopens_without_deleting(tmp_path):
    _run(tmp_path, _body(), 100)
    assert _run(tmp_path, _body("up"), 200)["action"] == "resolved"
    row = next(iter(_cases(tmp_path).values()))
    assert row["status"] == "resolved" and row["resolved_at"] == 200
    out = _run(tmp_path, _body(), 300)
    assert out["action"] == "reopened"
    row = next(iter(_cases(tmp_path).values()))
    assert row["opens"] == 2 and row["first_seen"] == 100 and len(row["history"]) == 3


def test_up_without_open_case_is_noop(tmp_path):
    assert _run(tmp_path, _body("up"), 100)["action"] == "noop"
    assert not (tmp_path / "case_intake" / ec.STORE_NAME).exists()


def test_distinct_services_and_kinds_get_distinct_cases(tmp_path):
    _run(tmp_path, _body(service="svc-a"), 1)
    _run(tmp_path, _body(service="svc-b"), 2)
    _run(tmp_path, _body(service="svc-a", kind="disk"), 3)
    assert len(_cases(tmp_path)) == 3


def test_alias_and_ignore_mapping(tmp_path):
    mapping = {"service_aliases": {"svc-a (native)": "svc-a"}, "ignore_services": ["noise"]}
    a = _run(tmp_path, _body(service="svc-a (native)"), 1, mapping)
    b = _run(tmp_path, _body(service="svc-a"), 2, mapping)
    assert a["case_id"] == b["case_id"] and b["action"] == "bumped"
    assert _run(tmp_path, _body(service="noise"), 3, mapping)["action"] == "ignored"


def test_synthetic_flag_kept(tmp_path):
    out = _run(tmp_path, _body(synthetic=True), 1)
    assert _cases(tmp_path)[out["case_id"]]["synthetic"] is True


def test_invalid_events_rejected(tmp_path):
    assert _run(tmp_path, _body("exploded"), 1)["status"] == 400
    assert _run(tmp_path, _body(service=""), 1)["status"] == 400
    assert _run(tmp_path, {"event": "nope"}, 1)["status"] == 400


def test_reason_is_redacted(tmp_path):
    body = _body()
    body["event"]["reason"] = "see https://example.invalid/x?k=1 " + "Z" * 40
    out = _run(tmp_path, body, 1)
    row = _cases(tmp_path)[out["case_id"]]
    assert "example.invalid" not in row["last_reason"]
    assert "Z" * 24 not in row["last_reason"]
