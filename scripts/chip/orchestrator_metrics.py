"""Log-only orchestrator metrics (pm-orchestrator-v1). No dashboards."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from chip.config import data_dir


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path():
    return data_dir() / "audit" / "orchestrator_metrics.jsonl"


def log_metric(kind: str, **fields: Any) -> None:
    record = {"ts": _ts(), "kind": kind, **fields}
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def note_go_outcome(
    *,
    room: str,
    chip_calls: int,
    delegate_hops: int,
    orchestrator_ticks: int,
    monthly_usd: float | None = None,
    pm_status: str | None = None,
) -> None:
    if pm_status and "false" in pm_status.lower() and "done" in pm_status.lower():
        log_metric("false_done_suspect", room=room, reason="pm_status_literal")
    log_metric(
        "hops_per_go",
        room=room,
        delegate_hops=delegate_hops,
        orchestrator_ticks=orchestrator_ticks,
        chip_calls=chip_calls,
    )
    if monthly_usd is not None and pm_status and pm_status.lower() == "done":
        log_metric("usd_per_done", room=room, monthly_usd=monthly_usd)
