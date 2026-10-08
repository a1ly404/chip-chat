from __future__ import annotations

import json
import urllib.error

from chip_relay import linear_key_gate as gate


def test_absent_key_is_not_ok(tmp_path):
    assert gate.check({}, data_root=tmp_path)["state"] == "absent"
    assert not (tmp_path / "linear_key_gate.jsonl").exists()


def test_invalid_key_fails_closed_with_receipt(tmp_path):
    out = gate.check({"LINEAR_API_KEY": "lin_bad"}, data_root=tmp_path, use_cache=False,
                     probe=lambda k: {"errors": [{"message": "Authentication required"}]})
    assert out == {"state": "invalid", "ok": False, "detail": "[{'message': 'Authentication required'}]"}
    row = json.loads((tmp_path / "linear_key_gate.jsonl").read_text())
    assert row["fail_closed"] is True and "lin_bad" not in json.dumps(row)


def test_http_401_is_invalid(tmp_path):
    def probe(_k):
        raise urllib.error.HTTPError(gate.LINEAR_API_URL, 401, "no", {}, None)

    assert gate.check({"LINEAR_API_KEY": "x"}, data_root=tmp_path, probe=probe, use_cache=False)["state"] == "invalid"


def test_valid_key(tmp_path):
    out = gate.check({"LINEAR_API_KEY": "lin_ok"}, data_root=tmp_path, use_cache=False,
                     probe=lambda k: {"data": {"viewer": {"id": "u1"}}})
    assert out["ok"] and out["state"] == "valid"
