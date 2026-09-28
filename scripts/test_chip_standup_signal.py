"""Standup signal writer tests — RAZ-329/RAZ-331 contract (Lyla binding).

Under test (docs/ops/CHIP_CHAT_STANDUP_SIGNAL.md, CHIP_CHAT_STANDUP_SIGNAL.md):
- top-level `source: chip_chat` + PT calendar date keys (YYYY-MM-DD)
- per-kind received/verified_as_of/missed_payload; collectors_live on gymlog
- CRITICAL law: an rc=3 correlated-dark day still writes the signal with
  received:true + missed_payload:true + verified_as_of set (absence of the
  signal is the ONLY forbidden thing — absence of a payload is banner class).
- a kind absent from the run = received:false (contract-allowed honest miss)
- merge law: today's date key replaces exactly; prior days persist;
  garbage existing file treated as absent, never fatal
- writer: env-driven path/URL; --post gating for the SITREP unchanged and
  the file write survives dry-run (anti-silence)
"""

import json
import sys
from pathlib import Path

from chip import standup_signal as sg
TS = "2026-09-28T18:25:12Z"


def silent_receipt(standup, age_h=None):
    return {
        "standup": standup, "issued_at": None,
        "payload": {"source": "silent", "age_h": age_h, "sha": None, "sections_expected": []},
        "skills_commanded": False,
        "credit": {"phaseC_gate": False, "reason": "no credit for silence"},
        "_schema_errors": [],
    }


def live_receipt(standup, age_h=2.0):
    return {
        "standup": standup, "issued_at": "2026-09-28 06:00 PT",
        "payload": {"source": "live", "age_h": age_h, "sha": "a" * 12, "sections_expected": []},
        "skills_commanded": False,
        "credit": {"phaseC_gate": True, "reason": "live payload within staleness"},
        "_schema_errors": [],
    }


def test_both_lanes_received_live_shape():
    sig = sg.build_signal([live_receipt("homelab"), live_receipt("gymlog")],
                          verified_as_of=TS, pt_date="2026-09-28")
    assert sig["source"] == "chip_chat"
    day = sig["2026-09-28"]
    assert set(day) == {"homelab", "gymlog"}
    for kind in ("homelab", "gymlog"):
        assert day[kind]["received"] is True
        assert day[kind]["verified_as_of"] == TS
        assert day[kind]["missed_payload"] is False
    assert day["gymlog"]["collectors_live"] is True


def test_correlated_dark_day_still_signals_received_true():
    # CRITICAL alignment: rc=3 pre-producer day is NOT silence.
    sig = sg.build_signal([silent_receipt("homelab", None), silent_receipt("gymlog")],
                          verified_as_of=TS, pt_date="2026-09-28")
    day = sig["2026-09-28"]
    for kind in ("homelab", "gymlog"):
        assert day[kind]["received"] is True
        assert day[kind]["verified_as_of"] == TS
        assert day[kind]["missed_payload"] is True  # explicit miss banner (soak-safe)
    assert day["gymlog"]["collectors_live"] is False


def test_kind_absent_from_run_is_received_false():
    sig = sg.build_signal([live_receipt("homelab")], verified_as_of=TS, pt_date="2026-09-28")
    gym = sig["2026-09-28"]["gymlog"]
    assert gym == {"received": False}  # verified_as_of/missed omitted per contract


def test_garbage_receipt_signals_miss_not_absence():
    garbage = {"standup": "homelab", "payload": {"source": "weird"}, "skills_commanded": False}
    sig = sg.build_signal([garbage], verified_as_of=TS, pt_date="2026-09-28")
    homelab = sig["2026-09-28"]["homelab"]
    assert homelab["received"] is True
    assert homelab["missed_payload"] is True
    assert homelab["verified_as_of"] == TS
    assert sig["source"] == "chip_chat"


def test_partial_collision_partial_receipt_mix():
    sig = sg.build_signal([live_receipt("homelab"), silent_receipt("gymlog")],
                          verified_as_of=TS, pt_date="2026-09-28")
    assert sig["2026-09-28"]["homelab"]["missed_payload"] is False
    assert sig["2026-09-28"]["gymlog"]["missed_payload"] is True


def test_signal_present_collision_left_unresolved():
    # Contract collision (filed on RAZ-329, NOT resolved): mixed present reading
    # vs soak-safe banner. Writer keeps honest fields; reader semantics pending.
    sig = sg.build_signal([live_receipt("homelab")], verified_as_of=TS, pt_date="2026-09-28")
    assert sig["2026-09-28"]["gymlog"]["received"] is False


def test_verified_as_of_required_never_placeholder():
    import pytest

    with pytest.raises(ValueError):
        sg.build_signal([live_receipt("homelab")], verified_as_of=None, pt_date="d")


# --- writer -------------------------------------------------------------------

def test_write_env_file(tmp_path, monkeypatch):
    p = tmp_path / "sig.json"
    sig = sg.build_signal([live_receipt("homelab")], verified_as_of=TS, pt_date="2026-09-28")
    monkeypatch.setenv("CHIP_CHAT_STANDUP_SIGNAL_FILE", str(p))
    out = sg.write_signal(sig, None)
    assert out["wrote"] == str(p) and "signal written" in out["note"]
    loaded = json.loads(p.read_text())
    assert loaded["source"] == "chip_chat" and loaded["2026-09-28"]["homelab"]["received"] is True


def test_write_merge_preserves_prior_days(tmp_path):
    p = tmp_path / "sig.json"
    p.write_text(json.dumps({"source": "chip_chat", "2026-09-27": {"legacy": True}}))
    sig = sg.build_signal([silent_receipt("homelab")], verified_as_of=TS, pt_date="2026-09-28")
    sg.write_signal(sig, str(p))
    loaded = json.loads(p.read_text())
    assert loaded["2026-09-27"] == {"legacy": True}
    assert loaded["2026-09-28"]["homelab"]["missed_payload"] is True


def test_write_garbage_existing_recovered(tmp_path):
    p = tmp_path / "sig.json"
    p.write_text("{corrupt")
    sg.write_signal(sg.build_signal([live_receipt("homelab")], verified_as_of=TS, pt_date="2026-09-28"), str(p))
    assert json.loads(p.read_text())["source"] == "chip_chat"


def test_write_no_config_visible_skip(tmp_path, monkeypatch):
    monkeypatch.delenv("CHIP_CHAT_STANDUP_SIGNAL_FILE", raising=False)
    out = sg.write_signal({"source": "chip_chat"}, None, environ={})
    assert out["wrote"] == "" and "skipped" in out["note"]


def test_post_url_gated_by_env(monkeypatch):
    monkeypatch.delenv("CHIP_CHAT_STANDUP_SIGNAL_URL", raising=False)
    out = sg.post_signal_url({}, environ={})
    assert out["url"] == "" and "no env" in out["note"]


def test_post_url_uses_chip_relay_ua(monkeypatch):
    calls = []

    class R:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    monkeypatch.setenv("CHIP_CHAT_STANDUP_SIGNAL_URL", "https://probe.invalid")
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: calls.append((req,)) or R())
    out = sg.post_signal_url({"source": "chip_chat"})
    assert "posted" in out["note"]
    assert calls[0][0].headers["User-agent"] == "chip-relay"


# --- main() wiring: dry-run STILL writes the signal (anti-silence) ------------

def test_main_dry_run_writes_signal(tmp_path, capsys, monkeypatch):
    from chip import standup_emit as se

    monkeypatch.delenv("CHIP_WEBHOOK_MONITORING", raising=False)
    (tmp_path / "homelab.json").write_text(json.dumps({
        "standup": "homelab", "issued_at": "2026-09-28 11:20 PT",
        "payload": {"source": "live", "age_h": 1.0, "sha": "a" * 12, "sections_expected": []},
        "skills_commanded": False,
    }))
    cfg = tmp_path / "sig.json"
    monkeypatch.setenv("CHIP_CHAT_STANDUP_SIGNAL_FILE", str(cfg))
    monkeypatch.setenv("CHIP_STANDUP_ISSUED_AT", "")  # pt clock path
    rc = se.main(["--lanes", "homelab,gymlog", "--receipts-dir", str(tmp_path)])
    assert rc == 2  # single-lane dark: gymlog absent -> STALL, no correlated pair
    out = capsys.readouterr().out
    assert "signal: signal written" in out
    loaded = json.loads(cfg.read_text())
    assert loaded["source"] == "chip_chat"
