"""Persona-level model routing (Ally law 2026-10-05).

PM = z-ai/glm-5.3-flash (low reasoning auto-applied for z-ai/glm-* prefixes);
dev bots = qwen/qwen3.7-flash (cheapest qwen on the chipchat key). A persona
without an explicit model falls back to default_model(); a BLOCKED persona
model falls back to default as well (fail-safe, never raises).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chip import config


def _write_personas(tmp_path: Path, personas: dict[str, Any]) -> Path:
    path = tmp_path / "personas.json"
    path.write_text(json.dumps({"personas": personas}), encoding="utf-8")
    return path


def test_pm_persona_gets_glm(monkeypatch, tmp_path):
    path = _write_personas(
        tmp_path,
        {
            "pm": {"display_name": "PM", "model": "z-ai/glm-5.3-flash"},
            "dev": {"model": "qwen/qwen3.7-flash"},
        },
    )
    monkeypatch.setattr(config, "PERSONAS_FILE", path)
    assert config.model_for_persona("pm") == "z-ai/glm-5.3-flash"
    assert config.model_for_persona("dev") == "qwen/qwen3.7-flash"


def test_unknown_persona_falls_back_to_default(monkeypatch, tmp_path):
    path = _write_personas(tmp_path, {"dev": {"model": "qwen/qwen3.7-flash"}})
    monkeypatch.setattr(config, "PERSONAS_FILE", path)
    monkeypatch.setattr(config, "DEFAULT_MODEL", "z-ai/glm-5.3-flash")
    assert config.model_for_persona("chipwriter") == "z-ai/glm-5.3-flash"
    assert config.model_for_persona(None) == "z-ai/glm-5.3-flash"


def test_blocked_persona_model_fails_back(monkeypatch, tmp_path):
    path = _write_personas(
        tmp_path,
        {"dev": {"model": "deepseek/deepseek-chat"}},
    )
    monkeypatch.setattr(config, "PERSONAS_FILE", path)
    monkeypatch.setattr(config, "DEFAULT_MODEL", "z-ai/glm-5.3-flash")
    assert config.model_for_persona("dev") == "z-ai/glm-5.3-flash"
