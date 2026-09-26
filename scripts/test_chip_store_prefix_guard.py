"""Generation-side prefix guard: render must not stack [agent] markers."""

import json

from chip import store


def _seed_thread(tmp_path, monkeypatch, rows):
    thread = tmp_path / "threads" / "dev.jsonl"
    thread.parent.mkdir(parents=True, exist_ok=True)
    thread.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(store, "thread_path", lambda room: thread)


def test_render_does_not_double_existing_marker(tmp_path, monkeypatch):
    _seed_thread(
        tmp_path,
        monkeypatch,
        [{"role": "assistant", "agent": "buffrdev", "content": "[buffrdev] hop-ok-buffr"}],
    )
    out = store.load_room_history("dev")
    assert out[0]["content"] == "[buffrdev] hop-ok-buffr"  # exactly one marker


def test_render_prefixes_clean_content(tmp_path, monkeypatch):
    _seed_thread(
        tmp_path,
        monkeypatch,
        [{"role": "assistant", "agent": "buffrdev", "content": "hop-ok-buffr"}],
    )
    out = store.load_room_history("dev")
    assert out[0]["content"] == "[buffrdev] hop-ok-buffr"


def test_render_prefixes_unrelated_marker(tmp_path, monkeypatch):
    _seed_thread(
        tmp_path,
        monkeypatch,
        [{"role": "assistant", "agent": "pm", "content": "[TODO] frame the slice"}],
    )
    out = store.load_room_history("dev")
    assert out[0]["content"] == "[pm] [TODO] frame the slice"


def test_user_rows_never_prefixed(tmp_path, monkeypatch):
    _seed_thread(
        tmp_path,
        monkeypatch,
        [{"role": "user", "agent": "user", "content": "hello"}],
    )
    out = store.load_room_history("dev")
    assert out[0]["content"] == "hello"
