"""Seed the fixture capture volume from the baked golden snapshot (WI-353).

Prod mounts a named volume at ``/app/config/fixtures/case_intake_live`` so
same-day captures survive container recreation. The image bakes an unmounted
copy of the committed goldens at ``/app/fixtures_seed``; this module copies
any goldens missing from the volume and merges index rows by id.

Invariants:
- Git tree remains the SoT for golden fixtures (CI replays from the tree).
- The volume is the live capture surface; newer live files always win.
- The seed never deletes or overwrites anything in the volume.
- Seed failure is non-fatal for relay boot.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

SEED_DIR = Path("/app/fixtures_seed")


def _load_rows(path: Path) -> list[dict]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def seed_fixture_dir(
    source_dir: Path | None = None,
    target_dir: Path | None = None,
) -> dict[str, object]:
    """Idempotently copy baked goldens into the live fixture volume.

    Returns a summary dict: {"status", "copied", "index_rows_added"}.
    Never raises on missing dirs; callers log the summary.
    """
    src = Path(source_dir) if source_dir is not None else SEED_DIR
    dst = Path(target_dir) if target_dir is not None else Path("/app/config/fixtures/case_intake_live")
    if not src.is_dir():
        return {"status": "no_source", "copied": [], "index_rows_added": 0}
    dst.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for src_file in sorted(src.glob("*.json")):
        dst_file = dst / src_file.name
        if dst_file.is_file():
            continue  # live copy (or earlier seed) wins — never overwrite
        shutil.copyfile(src_file, dst_file)
        copied.append(src_file.name)

    index_rows_added = 0
    src_rows = _load_rows(src / "index.json")
    if src_rows:
        index_path = dst / "index.json"
        dst_rows = _load_rows(index_path)
        known = {str(r.get("id")) for r in dst_rows}
        merged = list(dst_rows)
        for row in src_rows:
            if str(row.get("id")) not in known:
                merged.append(row)
                index_rows_added += 1
        if index_rows_added or not index_path.is_file():
            index_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")

    return {
        "status": "seeded" if copied or index_rows_added else "clean",
        "copied": copied,
        "index_rows_added": index_rows_added,
    }
