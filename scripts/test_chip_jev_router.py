"""Jev router tests: the four checklist rows,
each an explicit acceptance gate. Offline / mock-only / $0.00."""

import json
import os
from pathlib import Path

import pytest

from chip import counter_registry as reg_mod, jev_router as jr
from chip.config import data_dir

REGISTRY = None  # use the real checked-in registry


@pytest.fixture()
def jev_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    for var in ("CHIP_JEV_LIVE", "OPENROUTER_CC_API_KEY", "CHIP_CHAT_MOCK"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# Row 1: registry pre-call counter_id logging (counter_id=none before HTTP)
# ---------------------------------------------------------------------------


def test_row1_precheck_logged_before_call(jev_env: Path) -> None:
    env = dict(os.environ)
    env["CHIP_CHAT_MOCK"] = "1"
    jr.ask({"task_type": "wake-gray", "status": "In Progress"},
           {"eligible": {"type": "noul"}}, call_site="test", environ=env)

    rows = [json.loads(l) for l in (data_dir() / jr.LEDGER_FILE).read_text().splitlines()]
    pre = [r for r in rows if r["event"] == "precheck"]
    dec = [r for r in rows if r["event"] == "decide"]
    assert len(pre) >= 1 and len(dec) >= 1
    # precheck row precedes the decide row in WRITTEN ORDER
    idx_pre, idx_dec = rows.index(pre[0]), rows.index(dec[0])
    assert idx_pre < idx_dec


def test_row1_counter_id_is_none_before_live_call(jev_env: Path) -> None:
    env = dict(os.environ)
    env["CHIP_CHAT_MOCK"] = "1"
    jr.ask({"task_type": "wake-gray"}, {"q": {"type": "noul"}}, call_site="t", environ=env)
    rows = [json.loads(l) for l in (data_dir() / jr.LEDGER_FILE).read_text().splitlines()]
    pre = [r for r in rows if r["event"] == "precheck"][-1]
    assert "none" in pre["counter_id"]


# ---------------------------------------------------------------------------
# Row 2: row-#1 fixture shape must FAIL the build if it reaches the Jev client
# ---------------------------------------------------------------------------


def test_row2_row1_fixture_shape_never_reaches_the_client(jev_env: Path) -> None:
    import os as os

    env = dict(os.environ)
    env["CHIP_CHAT_MOCK"] = "1"
    env["CHIP_JEV_LIVE"] = "1"
    env["OPENROUTER_CC_API_KEY"] = "testkey"
    receipt = jr.ask(dict(reg_mod_counter_fixture := {}), {"q": {"type": "noul"}},
                     call_site="t", environ=env) if False else None  # unused guard

    # the exact row-#1 state, with live GO'ed: registry chooses DEFER, client silent
    result = jr.ask(
        {
            "task_id": "LIN-199", "task_type": "done-gate", "status": "Done",
            "statusType": "completed", "checked_boxes": 4, "receipts_bound": 0,
            "unbound_done": True,
        },
        {"q": {"type": "noul"}}, call_site="t", environ=env,
    )
    assert result["status"] == "deferred-registry-hit"
    ledger = [
        json.loads(l) for l in (data_dir() / jr.LEDGER_FILE).read_text().splitlines()
    ]
    # and NO decide row ever reaches the "answered" outcome with real cost
    assert all(r["outcome"] != "answered" for r in ledger if r["event"] == "decide")
    assert all(r["action"] == "conservative" for r in ledger if r["event"] == "decide")


# ---------------------------------------------------------------------------
# Row 3: shadow rows carry band_hypothetical + conservative action (no branch)
# ---------------------------------------------------------------------------


def test_row3_shadow_rows_never_branch(jev_env: Path) -> None:
    env = dict(os.environ)
    env["CHIP_CHAT_MOCK"] = "1"
    questions = {
        "q": {"type": "noul"},
    }
    receipt = jr.ask({"task_type": "wake-gray"}, questions, call_site="t", environ=env)
    ledger = [
        json.loads(l) for l in (data_dir() / jr.LEDGER_FILE).read_text().splitlines()
    ]
    dec = [r for r in ledger if r["event"] == "decide"][-1]
    # band_hypothetical is meta-only: the taken action stays conservative
    assert dec["action"] == "conservative"
    # mock answers record band_hypothetical but never a gate flip
    assert dec.get("band_hypothetical") in (None, "needs-repro", "propose-in-room",
                                            "propose-auto")


# ---------------------------------------------------------------------------
# Row 4: room lines are missing-field blanks only, never confidences
# ---------------------------------------------------------------------------


def test_row4_room_reach_returns_none_pre_emitter(jev_env: Path) -> None:
    assert jr.room_reach({}, {}, call_site="t") is None


# ---------------------------------------------------------------------------
# Sanitize / redaction / fail-closed fallback
# ---------------------------------------------------------------------------


def test_secret_shaped_keys_dropped(jev_env: Path) -> None:
    state = {"webhook": "https://x", "api_key": "sk-secret", "counters": 3}
    cleaned = jr.sanitize_state(state)
    assert "webhook" not in cleaned and "api_key" not in cleaned
    assert cleaned["counters"] == 3


def test_long_and_controlled_strings_truncated(jev_env: Path) -> None:
    state = {"excerpt_escaped": "a" * 400 + "\x00\x01"}
    cleaned = jr.sanitize_state(state)
    e = cleaned["excerpt_escaped"]
    assert len(e) <= 200 and "\x00" not in e and "\x01" not in e


def test_fallback_when_jev_down(jev_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = dict(os.environ)
    env["CHIP_JEV_LIVE"] = "1"
    env["OPENROUTER_CC_API_KEY"] = "key"
    env.pop("CHIP_CHAT_MOCK", None)
    # every urlopen raises — single retry, then fail-closed fallback
    monkeypatch.setattr("urllib.request.urlopen"
                        , _raising_open)
    receipt = jr.ask({"task_type": "wake-gray"}, {"q": {"type": "noul"}},
                     call_site="t", environ=env)
    assert receipt["status"] == "fallback"
    # the ledger row marks the conservative default
    ledger = [
        json.loads(l) for l in (data_dir() / jr.LEDGER_FILE).read_text().splitlines()
    ]
    assert ledger[-1]["outcome"].startswith("fallback")


def _raising_open(*args, **kwargs):
    import urllib.error

    raise ConnectionError("down")


def test_state_sha_deterministic(jev_env: Path) -> None:
    s = {"a": 1, "b": [2, 3]}
    assert jr._sha12(s) == jr._sha12({"b": [2, 3], "a": 1})
