"""Receipt schema gate: bad sitreps become an ERROR line, never a channel post of the body."""

from chip_relay.receipt_gate import ERROR_PREFIX, body_for_store, gate_channel_body

GOOD = "\n".join(
    [
        "claim: doc edit only",
        "status: done",
        "evidence: sha:dc2d851",
        "next: none",
    ]
)


def test_valid_doc_receipt_passes() -> None:
    assert gate_channel_body(GOOD) == GOOD


def test_prose_answer_is_not_a_sitrep() -> None:
    prose = "The latest episode is not on disk yet. I have not checked the media indexer."
    assert gate_channel_body(prose) == prose


def test_missing_fields() -> None:
    out = gate_channel_body("claim: hi\nstatus: done")
    assert out == f"{ERROR_PREFIX}missing fields"


def test_done_without_evidence() -> None:
    body = "claim: finished\nstatus: done\nevidence: none\nnext: none"
    assert gate_channel_body(body) == f"{ERROR_PREFIX}done-without-evidence"


def test_unlabeled_table_spam() -> None:
    body = "| a | b |\n|---|---|\n| 1 | 2 |\nlooks fine"
    assert gate_channel_body(body) == f"{ERROR_PREFIX}unlabeled-table"


def test_malformed_body() -> None:
    assert gate_channel_body("   ") == f"{ERROR_PREFIX}malformed body"
    bad_status = "claim: x\nstatus: shipped\nevidence: none\nnext: none"
    assert gate_channel_body(bad_status) == f"{ERROR_PREFIX}malformed body"


def test_execution_claim_needs_tool_receipt() -> None:
    body = "\n".join(
        [
            "claim: ran the crontab comment",
            "status: done",
            "evidence: sha:11968de",
            "next: none",
        ]
    )
    assert gate_channel_body(body) == f"{ERROR_PREFIX}execution-without-tool-receipt"
    ok = body.replace("evidence: sha:11968de", "evidence: tool:cron-disable-1")
    assert gate_channel_body(ok) == ok


def test_blocked_allows_none() -> None:
    body = "claim: waiting on soak\nstatus: blocked\nevidence: none\nnext: recheck logs"
    assert gate_channel_body(body) == body


def test_dead_image_refusal_is_a_blocked_receipt() -> None:
    from chip_relay.refusal import human_followup_body, refusal_body

    ref = refusal_body()
    follow = human_followup_body()
    assert gate_channel_body(ref) == ref
    assert gate_channel_body(follow) == follow
    assert "propose-only" in ref
    assert "no fix from this channel" in follow


def test_relay_store_uses_gated_body() -> None:
    raw = "[mock PM] got it."
    assert body_for_store(raw, {}) == raw
    assert body_for_store(raw, {"CHIP_STORE_GATED": "1"}) == raw
    partial = "claim: hi"
    stored = body_for_store(partial, {"CHIP_STORE_GATED": "1"})
    assert stored == f"{ERROR_PREFIX}missing fields"
    assert body_for_store(GOOD, {"CHIP_STORE_GATED": "1"}) == GOOD
