"""Acme pack default CHIP_CHAT_DATA_DIR."""

from __future__ import annotations

import pytest

from chip import config, system_pack


@pytest.fixture(autouse=True)
def _clear_pack_cache():
    system_pack.clear_caches()
    from chip import pack_values

    pack_values._pack.cache_clear()
    yield
    system_pack.clear_caches()
    pack_values._pack.cache_clear()


def test_data_dir_acme_when_pack_set(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIP_SYSTEM_PACK", "acme-example")
    monkeypatch.delenv("CHIP_CHAT_DATA_DIR", raising=False)
    system_pack.clear_caches()
    from chip import pack_values

    pack_values._pack.cache_clear()
    path = config.data_dir()
    assert path == tmp_path / "home" / ".local" / "share" / "chip-chat"
