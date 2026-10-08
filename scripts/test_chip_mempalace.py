from __future__ import annotations
"""MemPalace Lite unit tests (offline, no OpenRouter)."""


import json
from pathlib import Path

import pytest

from chip import mempalace, store


def test_extract_formats_kinds() -> None:
    rows = [
        {
            "role": "user",
            "content": "We decided to ship with mock mode first.",
            "agent": "planner",
            "ts": "2026-01-01T00:00:00+00:00",
        },
        {
            "role": "assistant",
            "content": "Blocked on API key rotation until ops pastes the new secret.",
            "agent": "builder",
            "ts": "2026-01-01T00:01:00+00:00",
        },
        {
            "role": "assistant",
            "content": "[memory:fact] Relay maps Discord authors via speakers.json handles.",
            "agent": "lucy",
            "ts": "2026-01-01T00:02:00+00:00",
        },
    ]
    records = mempalace.extract_from_transcript("demo", rows)
    kinds = {r.kind for r in records}
    assert "decision" in kinds
    assert "blocker" in kinds
    assert "fact" in kinds
    explicit = [r for r in records if "speakers.json" in r.text]
    assert explicit and explicit[0].kind == "fact"
    for rec in records:
        payload = rec.to_json()
        assert payload["kind"] in ("fact", "decision", "blocker")
        assert payload["room"] == "demo"
        assert payload["text"]


def test_recall_retrieval_prefers_relevant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    room = "demo"
    agent = "lucy"
    mempalace.append_memories(
        room,
        [
            mempalace.MemoryRecord(
                kind="fact",
                text="Docker compose mounts chip-chat-data at /data",
                agent=agent,
                ts="t1",
                room=room,
            ),
            mempalace.MemoryRecord(
                kind="decision",
                text="We decided to use pytest for all offline tests",
                agent=agent,
                ts="t2",
                room=room,
            ),
        ],
    )
    loaded = mempalace.load_memories(room, agent)
    picked = mempalace.select_top_k(loaded, "docker volume mount path", k=5, token_budget=500)
    assert picked
    assert "Docker" in picked[0].text


def test_top_k_and_token_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    room = "example"
    agent = "mick"
    long = "x" * 400
    batch = [
        mempalace.MemoryRecord(kind="fact", text=f"alpha {i}", agent=agent, ts=f"t{i}", room=room)
        for i in range(8)
    ]
    batch.append(
        mempalace.MemoryRecord(kind="fact", text=long, agent=agent, ts="t-long", room=room)
    )
    mempalace.append_memories(room, batch)
    loaded = mempalace.load_memories(room, agent)
    picked = mempalace.select_top_k(loaded, "alpha 3", k=5, token_budget=80)
    assert len(picked) <= 5
    total_chars = sum(len(r.text) for r in picked)
    assert total_chars < 800


def test_room_close_mines_segment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    store.log_message("demo", "user", "We decided to add MemPalace Lite.")
    store.log_message(
        "demo",
        "assistant",
        "Blocked until the close path writes memories under appdata.",
        agent="lucy",
    )
    from chip.cli import close_room_session

    assert close_room_session("demo") == 0
    mem_file = tmp_path / "memories" / "demo" / "lucy.jsonl"
    assert mem_file.is_file()
    lines = mem_file.read_text(encoding="utf-8").strip().splitlines()
    assert lines
    kinds = {json.loads(line)["kind"] for line in lines}
    assert "blocker" in kinds
    thread = store.thread_path("demo").read_text(encoding="utf-8")
    assert "session_close" in thread


def test_build_room_messages_includes_mined_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path))
    mempalace.append_memories(
        "example",
        [
            mempalace.MemoryRecord(
                kind="fact",
                text="Spend cutoff remains at ten dollars monthly",
                agent="default",
                ts="t1",
                room="example",
            )
        ],
    )
    from chip.cli import build_room_messages

    messages = build_room_messages(
        "example",
        "default",
        "What is the spend cutoff?",
        recall_agent="default",
    )
    system = messages[0]["content"]
    assert "ADVISORY MEMORY (not_law)" in system
    assert "ten dollars" in system
    assert "ROOM LAW" in system
    assert system.index("ADVISORY MEMORY") < system.index("ROOM LAW")
    assert system.index("ten dollars") < system.index("ROOM LAW")
