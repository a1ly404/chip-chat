"""Board-sweep tests (P1: done-gate leak class, whole-export verdicts)."""

from chip.done_gate import sweep, sweep_one

DONE_NO_STAMPS = {
    "id": "LIN-9001",
    "status": "Done",
    "statusType": "completed",
    "description": "## Acceptance\n- [x] thing A\n- [x] thing B\n",
}
INPROG_NO_STAMPS = {
    "id": "LIN-9002",
    "status": "In Progress",
    "statusType": "started",
    "description": "## Acceptance\n- [x] thing\n",
}
VAGUE_DONE = {
    "id": "LIN-9003",
    "status": "Done",
    "statusType": "completed",
    "description": "## Done\nclean, no boxes",
}
CLEAN_DONE = {
    "id": "LIN-9004",
    "status": "Done",
    "statusType": "completed",
    "description": "## Acceptance\n- [x] thing\nverified-as-of: 2026-09-27 06:00 — probe ok\n",
}
INPROG_STAMPED = {
    "id": "LIN-9005",
    "status": "In Progress",
    "statusType": "started",
    "description": "## Acceptance\n- [x] thing\nverified-as-of: 2026-09-27 06:10 — ok\n",
}
CANCELED_EMPTY = {
    "id": "LIN-9006",
    "status": "Canceled",
    "statusType": "canceled",
    "description": "",
}


def test_sweep_one_ranks_all_classes():
    f1 = sweep_one(DONE_NO_STAMPS)
    assert f1["severity"] == "reopen-required" and f1["checked"] == 2 and f1["stamps"] == 0
    f2 = sweep_one(INPROG_NO_STAMPS)
    assert f2["severity"] == "flipping-risk"
    f3 = sweep_one(VAGUE_DONE)
    assert f3["severity"] == "vague-done"


def test_sweep_one_clean_tickets_yield_none():
    assert sweep_one(CLEAN_DONE) is None
    assert sweep_one(INPROG_STAMPED) is None
    assert sweep_one(CANCELED_EMPTY) is None  # canceled is not a Done claim


def test_sweep_orders_by_severity():
    out = sweep([VAGUE_DONE, DONE_NO_STAMPS, INPROG_NO_STAMPS, CLEAN_DONE])
    assert [f["severity"] for f in out] == ["reopen-required", "flipping-risk", "vague-done"]
    assert [f["id"] for f in out] == ["LIN-9001", "LIN-9002", "LIN-9003"]
