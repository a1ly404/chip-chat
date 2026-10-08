from __future__ import annotations
"""Unit tests for offline mock completions (delegate hops)."""


from chip import config, mock
from test_pack_utils import first_specialist_persona


def _pm_system() -> str:
    return config.load_personas()["pm"]["system"]


def _specialist_system(persona: str) -> str:
    return config.load_personas()[persona]["system"]


def test_mock_pm_echoes_delegate_line_from_user_task() -> None:
    specialist = first_specialist_persona()
    user = f"delegate:{specialist} Reply hop-ok-{specialist} only."
    payload = mock.mock_completion(
        "z-ai/glm-5.3-flash",
        [
            {"role": "system", "content": _pm_system()},
            {"role": "user", "content": user},
        ],
    )
    text = payload["choices"][0]["message"]["content"]
    assert f"delegate:{specialist}" in text


def test_mock_specialist_reply_non_empty() -> None:
    specialist = first_specialist_persona()
    task = f"Reply hop-ok-{specialist} only."
    payload = mock.mock_completion(
        "z-ai/glm-5.3-flash",
        [
            {"role": "system", "content": _specialist_system(specialist)},
            {"role": "user", "content": task},
        ],
    )
    text = payload["choices"][0]["message"]["content"]
    assert text.strip()
    assert f"hop-ok-{specialist}".lower() in text.lower()
