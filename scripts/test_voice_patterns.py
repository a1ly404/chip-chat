"""Voice-pattern guard: persona replies must read like a coworker, not a status board.

Conversational-tone slice (chipchatdev lane). Three layers are checked:

- binding persona prompts (``config/chip_chat_personas.json``) carry the voice
  rules and no longer instruct robot vocabulary;
- the advisory learnings voice block loads for every chat persona;
- mock canned output — the deterministic stand-in for persona replies — passes
  the banned-phrase / length / link-count linters from the task sketch.
"""

from __future__ import annotations

import re

from chip import config, learnings, mock

CHAT_PERSONAS = ("pm", "dev", "buffrdev", "homelabdev", "opsdev", "chipchatdev")

# Banned in persona *output* (sketch list, generalized from "[pm] ack" to all
# chat personas). Applied to mock canned text; learnings ban-list lines are the
# only place the phrases live on purpose, so prompts are checked separately.
_BANNED = (
    r"lane ack",
    r"received \d+ chars",
    r"\[(?:pm|dev|buffrdev|homelabdev|opsdev|chipchatdev)\] ack",
    r"sitrep:",
)


def _lint_output(output: str) -> None:
    low = output.lower()
    for pattern in _BANNED:
        assert re.search(pattern, low) is None, f"banned phrase: {pattern}"
    assert len(output.strip().splitlines()) <= 5, "too many lines for chat"
    assert len(re.findall(r"https?://\S+", output)) <= 1, "one link max"


def test_persona_prompts_conversational() -> None:
    personas = config.load_personas()
    for pid in CHAT_PERSONAS:
        system = personas[pid]["system"].lower()
        assert f"you are {pid}" in system, pid  # mock._detect_persona contract
        assert "coworker" in system, pid  # voice rule present
        for banned in ("lane ack", "sitrep", "received", "ack `", "be brief."):
            assert banned not in system, (pid, banned)


def test_output_passes_voice_checks() -> None:
    personas = config.load_personas()
    samples = (
        "ping",
        "working the flake, pushing soon",
        "delegate:chipchatdev check spend gates",
        "Reply hop-ok-buffr only.",
    )
    for pid in CHAT_PERSONAS:
        system = personas[pid]["system"]
        for text in samples:
            payload = mock.mock_completion(
                "z-ai/glm-5.3-flash",
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
            )
            reply = payload["choices"][0]["message"]["content"]
            assert reply.strip(), (pid, text)
            if "delegate:" in text:
                assert "delegate:chipchatdev check spend gates" in reply  # passthrough contract
            elif pid == "buffrdev" and "hop-ok" in text:
                assert "hop-ok-buffr" in reply.lower()  # hop smoke contract
            _lint_output(reply)


def _law_section(path: str) -> str:
    text = open(path, encoding="utf-8").read().lower()
    start = text.index("## law")
    end = text.find("## memory", start)
    return text[start:] if end < 0 else text[start:end]


def test_prompts_and_channel_law_drop_status_board() -> None:
    personas = config.load_personas()
    for pid in CHAT_PERSONAS:
        system = personas[pid]["system"].lower()
        assert "done =" not in system, pid
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    for name in ("pm.md", "dev.md"):
        law = _law_section(str(repo / "config" / "chip_chat" / "channels" / name))
        assert "done =" not in law, name
        assert "pm lane:" not in law, name


def test_learnings_voice_block_loaded() -> None:
    for pid in CHAT_PERSONAS:
        block = learnings.render_learnings_block(pid)
        assert block, pid  # advisory voice block reaches the room prompt
        low = block.lower()
        assert "coworker" in low, pid
        assert "lane ack" in low, pid  # ban list present
        assert "sitrep" in low, pid
