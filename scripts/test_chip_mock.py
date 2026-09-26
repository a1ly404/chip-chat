"""Unit tests for offline mock completions (delegate hops)."""

from __future__ import annotations

from chip import config, mock


def _pm_system() -> str:
    return config.load_personas()["pm"]["system"]


def _buffrdev_system() -> str:
    return config.load_personas()["buffrdev"]["system"]


def test_mock_pm_echoes_delegate_line_from_user_task() -> None:
    user = "delegate:buffrdev Reply hop-ok-buffr only."
    payload = mock.mock_completion(
        "z-ai/glm-5.3-flash",
        [
            {"role": "system", "content": _pm_system()},
            {"role": "user", "content": user},
        ],
    )
    text = payload["choices"][0]["message"]["content"]
    assert "delegate:buffrdev Reply hop-ok-buffr only." in text


def test_mock_buffrdev_specialist_reply_non_empty() -> None:
    task = "Reply hop-ok-buffr only."
    payload = mock.mock_completion(
        "z-ai/glm-5.3-flash",
        [
            {"role": "system", "content": _buffrdev_system()},
            {"role": "user", "content": task},
        ],
    )
    text = payload["choices"][0]["message"]["content"]
    assert text.strip()
    assert "hop-ok-buffr" in text.lower()
    assert "buffr" in text.lower()
