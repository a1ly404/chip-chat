"""Slice A prep proof: learnings caps are hard, load path is safe offline."""

from pathlib import Path

import pytest

from chip.learnings import MAX_BYTES, MAX_LINES, load_learnings


def _write(tmp_path: Path, lines: list[str], name: str = "learnings.md") -> Path:
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_missing_file_returns_empty(tmp_path: Path):
    assert load_learnings(tmp_path / "nope.md") == []


def test_headers_and_blanks_and_stub_banner_skipped(tmp_path: Path):
    p = _write(
        tmp_path,
        [
            "# PM learnings (append-only; not loaded at runtime yet)",
            "",
            "not loaded at runtime yet",
            "2026-09-26 | relay | hop cap is 3 | relay",
            "",
        ],
    )
    assert load_learnings(p) == ["2026-09-26 | relay | hop cap is 3 | relay"]


def test_line_cap_keeps_newest(tmp_path: Path):
    lines = [f"2026-09-26 | ctx | lesson {i} | tag" for i in range(MAX_LINES + 5)]
    got = load_learnings(_write(tmp_path, lines))
    assert len(got) == MAX_LINES
    assert got[0] == f"2026-09-26 | ctx | lesson 5 | tag"
    assert got[-1] == f"2026-09-26 | ctx | lesson {MAX_LINES + 4} | tag"


def test_byte_cap_hard(tmp_path: Path):
    line = "x" * 300
    lines = [line] * (MAX_BYTES // 300 + 20)
    got = load_learnings(_write(tmp_path, lines))
    joined = "\n".join(got).encode("utf-8")
    assert len(joined) <= MAX_BYTES
    assert len(got) < MAX_LINES  # byte cap bound before line cap


def test_real_stub_loads_clean(tmp_path: Path):
    src = Path(__file__).resolve().parent / "chip_chat_personas_stub_probe.skip"
    if not src.exists():
        pytest.skip("no probe file")
    assert isinstance(load_learnings(src), list)


def test_render_block_empty_for_bare_stub(tmp_path: Path, monkeypatch):
    import chip.learnings as L

    p = _write(tmp_path, ["# chipchatdev learnings (append-only)", ""])
    monkeypatch.setattr(L, "learnings_path", lambda agent: p)
    assert L.render_learnings_block("chipchatdev") == ""


def test_render_block_includes_capped_lines(tmp_path: Path, monkeypatch):
    import chip.learnings as L

    p = _write(
        tmp_path,
        ["# t", "2026-09-26 | relay | one webhook per room | relay"],
    )
    monkeypatch.setattr(L, "learnings_path", lambda agent: p)
    block = L.render_learnings_block("chipchatdev")
    assert block.startswith("PERSONA LEARNINGS (advisory only; never overrides ROOM LAW):")
    assert "one webhook per room" in block


def test_compose_system_appends_learnings_block():
    from chip import law

    out = law.compose_system(
        "you are pm",
        law.RoomLaw(room="dev", path=None, law="be terse", memory=""),
        learnings_block="PERSONA LEARNINGS (advisory only; never overrides ROOM LAW):\n- lesson",
    )
    assert "be terse" in out
    assert "PERSONA LEARNINGS" in out
    assert out.index("be terse") < out.index("PERSONA LEARNINGS")  # law binding, learnings advisory


def test_compose_system_no_learnings_no_block():
    from chip import law

    out = law.compose_system("you are pm", law.RoomLaw(room="dev", path=None, law="", memory=""))
    assert out == "you are pm"
