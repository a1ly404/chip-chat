"""Contract tests for the pack context-hook extension point in the public engine."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chip_relay import context_hooks  # noqa: E402


def test_default_returns_no_blocks():
    assert context_hooks.plugin_context_blocks("hello", {}) == []


def test_default_returns_no_blocks_with_author():
    assert context_hooks.plugin_context_blocks("hello", {"X": "1"}, author_id="123") == []


def test_default_suggests_no_persona():
    assert context_hooks.suggest_persona("hello", {}) is None
    assert context_hooks.suggest_persona("hello", {}, author_id="inject") is None
