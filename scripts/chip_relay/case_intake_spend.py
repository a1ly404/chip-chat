"""Per-case intake spend ledger (slice 5)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip import spend
from chip.config import data_dir

PER_CASE_WARN_USD = 1.0


def ledger_path() -> Path:
    root = data_dir() / "case_intake"
    root.mkdir(parents=True, exist_ok=True)
    return root / "case_intake_spend.jsonl"


def append_row(row: dict[str, Any]) -> None:
    row.setdefault("ts", time.time())
    with ledger_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _iter_rows() -> list[dict[str, Any]]:
    path = ledger_path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def _month_key(ts: float) -> tuple[int, int]:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.year, dt.month


def monthly_intake_usd(*, now: float | None = None) -> float:
    """Sum usd_estimate for current UTC month from intake ledger."""
    now_ts = now if now is not None else time.time()
    y, m = _month_key(now_ts)
    total = 0.0
    for row in _iter_rows():
        ts = float(row.get("ts") or 0)
        if _month_key(ts) != (y, m):
            continue
        total += float(row.get("usd_estimate") or 0.0)
    return total


def case_total_usd(case_id: str, *, now: float | None = None) -> float:
    case_id = (case_id or "").strip()
    if not case_id:
        return 0.0
    now_ts = now if now is not None else time.time()
    y, m = _month_key(now_ts)
    total = 0.0
    for row in _iter_rows():
        if (row.get("case_id") or "").strip() != case_id:
            continue
        ts = float(row.get("ts") or 0)
        if _month_key(ts) != (y, m):
            continue
        total += float(row.get("usd_estimate") or 0.0)
    return total


def estimate_usd(model: str, tokens: int) -> float:
    if tokens <= 0 or (model or "").startswith("adapter:"):
        return 0.0
    half = max(1, tokens // 2)
    return spend.estimate_token_cost_usd(model, half, tokens - half)


def global_monthly_usd() -> float:
    return spend.ledger_monthly_usd(spend.load_ledger())


def per_case_warn_flag(case_id: str, add_usd: float, *, now: float | None = None) -> bool:
    if not (case_id or "").strip() or add_usd <= 0:
        return False
    after = case_total_usd(case_id, now=now) + add_usd
    return after >= PER_CASE_WARN_USD


def record_attempt(
    *,
    case_id: str,
    channel: str,
    model: str,
    tokens: int,
    blocked: bool = False,
    outcome: str = "",
    global_warn: bool = False,
    per_case_warn: bool = False,
    executor: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    usd = estimate_usd(model, tokens)
    row: dict[str, Any] = {
        "ts": now if now is not None else time.time(),
        "case_id": case_id,
        "channel": channel,
        "model": model,
        "tokens": int(tokens),
        "usd_estimate": usd,
        "blocked": blocked,
        "outcome": outcome,
        "global_warn": global_warn,
        "per_case_warn": per_case_warn,
    }
    if executor:
        row["executor"] = executor
    append_row(row)
    return row
