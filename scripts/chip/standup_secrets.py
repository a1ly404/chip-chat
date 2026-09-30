"""Load standup collector keys from Infisical when they are unset.

Values are written only into the process environment. Callers may print key
names, never values. Absent keys stay absent so collectors fail closed.
"""

from __future__ import annotations

import os

from chip_relay.infisical_rest import STANDUP_KEYS, fetch_keys


def apply_standup_secrets(environ: dict[str, str] | None = None) -> list[str]:
    target = os.environ if environ is None else environ
    need = tuple(name for name in STANDUP_KEYS if not str(target.get(name, "")).strip())
    if not need:
        return []
    found = fetch_keys(need, target)
    applied: list[str] = []
    for name in need:
        value = found.get(name, "")
        if value.strip():
            target[name] = value
            applied.append(name)
    return applied
