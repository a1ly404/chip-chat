"""Read-only al-9501 soak. Closed until 2026-10-01T02:11:00Z.

A pass prints two checks and exits. It does not restart, deploy, or edit.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

NOT_BEFORE = datetime(2026, 10, 1, 2, 11, tzinfo=timezone.utc)


def main() -> int:
    now = datetime.now(timezone.utc)
    if now < NOT_BEFORE:
        print(f"soak closed until {NOT_BEFORE.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        return 2
    logs = subprocess.check_output(
        ["docker", "logs", "monitoring-api", "--since", "4h"],
        text=True,
        stderr=subprocess.STDOUT,
    )
    hits = [line for line in logs.splitlines() if "No such image" in line]
    snap = Path.home() / "appdata" / "chip-chat" / "monitoring"
    print(f"no_such_image_lines={len(hits)}")
    print(f"feed_snapshot_dir_exists={snap.is_dir()}")
    if hits:
        print("soak fail: image 404 still present")
        return 1
    print("soak quiet window observed; this is one data point; runbook stays propose")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
