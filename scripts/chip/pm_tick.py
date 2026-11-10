"""PM tick: live inputs -> picker -> background jobs, on a schedule (parity P1.4c).

One ``tick()`` is the "PM runs on its own" loop body:

1. ``bg_jobs.recover`` marks jobs left ``running`` by a dead process as failed.
2. Budget mode is checked: at ``stopped`` nothing is picked or run (inputs are not consumed).
3. Inputs are gathered from the configured adapters:

   * :class:`AlertStream` — tails a JSONL file of alert events with a persisted byte cursor, so each
     event is read once. Default path is the relay's ``chip_state/monitoring_dispatch.jsonl``
     (rows carry a nested ``alert`` dict); any JSONL of flat alert dicts works too
     (``CHIP_PM_ALERTS_PATH``). Field names are mapped generically (id/fingerprint/key,
     title/summary/message, severity/level/priority).
   * :func:`linear_items` — polls an injected Linear client for open issues carrying a picker label
     (default ``chip-job``). :func:`linear_http_client` is a small GraphQL client used only when
     ``CHIP_PM_LINEAR=1`` and ``CHIP_LINEAR_API_KEY`` are set; tests inject fakes.

4. ``bg_picker.pick`` enqueues read-only jobs under its policy, dedupe and daily pick cap.
5. ``bg_jobs.run_pending`` runs startable jobs under the job caps (daily jobs/spend, hop/spend
   ceilings, read concurrency) with the supplied step (mock by default; model step in thrift mode
   still refuses mutate jobs, which the picker never creates anyway).

The alert cursor advances only after the pick step succeeds. Each tick appends one row to
``CHIP_CHAT_DATA_DIR/pm_tick_history.jsonl``.

Gated by ``CHIP_PM_TICK=1`` (default off) on top of the existing ``CHIP_PICKER`` / ``CHIP_JOBS`` flags.
``dry_run=True`` works with every flag off and writes nothing (cursor included).
Routines with ``kind=pm-tick`` are dispatched here by ``chip routine run-due --via-jobs``.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from chip import bg_jobs, bg_picker, budget_mode
from chip.config import data_dir

FLAG = "CHIP_PM_TICK"
ROUTINE_KIND = "pm-tick"
MAX_ALERT_LINES = 500
MAX_ALERT_BYTES = 2_000_000
LINEAR_URL = "https://api.linear.app/graphql"

LinearClient = Callable[[str, int], list[Mapping[str, Any]]]


class PmTickDisabled(RuntimeError):
    """Raised when tick() would act while CHIP_PM_TICK is not enabled."""


def _env(env: Mapping[str, str] | None) -> dict[str, str]:
    return dict(os.environ if env is None else env)


def _on(env: Mapping[str, str], name: str) -> bool:
    return str(env.get(name, "0")).strip().lower() in {"1", "true", "yes", "on"}


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return _on(_env(env), FLAG)


def history_path() -> Path:
    return data_dir() / "pm_tick_history.jsonl"


def default_alerts_path(env: Mapping[str, str] | None = None) -> Path:
    raw = str(_env(env).get("CHIP_PM_ALERTS_PATH", "")).strip()
    return Path(raw) if raw else data_dir() / "chip_state" / "monitoring_dispatch.jsonl"


# ------------------------------------------------------------------ alert stream adapter


def _first(d: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def normalize_alert(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Map one alert event (flat, or a relay row with nested ``alert``) to a picker input."""
    if not isinstance(row, Mapping):
        return None
    inner = row.get("alert")
    a: Mapping[str, Any] = inner if isinstance(inner, Mapping) else row
    ident = _first(a, ("id", "fingerprint", "key", "alert_id", "name")) or _first(row, ("id", "fingerprint"))
    title = _first(a, ("title", "summary", "message", "name", "key")) or ""
    sev = _first(a, ("severity", "level", "priority", "status")) or ""
    if ident is None:
        return None
    detail = _first(a, ("detail", "description", "body", "text")) or ""
    return {
        "kind": "alert",
        "id": str(ident)[:120],
        "title": str(title)[:300],
        "severity": str(sev).strip().lower(),
        "detail": str(detail)[:2000],
    }


class AlertStream:
    """Read new JSONL alert events since the last committed cursor (byte offset)."""

    def __init__(self, path: Path, *, cursor_name: str = "pm_tick_alert_cursor.json") -> None:
        self.path = Path(path)
        self.cursor_path = data_dir() / cursor_name
        self._pending: int | None = None

    def _cursor(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.cursor_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def read(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return ``(items, info)``; call :meth:`commit` to advance past what was read."""
        info: dict[str, Any] = {"path": str(self.path), "lines": 0, "bad": 0, "ignored": 0, "reset": False}
        self._pending = None
        if not self.path.is_file():
            info["missing"] = True
            return [], info
        cur = self._cursor()
        offset = int(cur.get("offset", 0)) if cur.get("path") == str(self.path) else 0
        size = self.path.stat().st_size
        if offset > size:  # truncated / rotated: start over
            offset, info["reset"] = 0, True
        items: list[dict[str, Any]] = []
        with self.path.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read(MAX_ALERT_BYTES)
        pos = offset
        for raw in chunk.splitlines(keepends=True):
            if not raw.endswith(b"\n"):
                break  # partial line still being written: leave it for the next tick
            if info["lines"] >= MAX_ALERT_LINES:
                break
            pos += len(raw)
            info["lines"] += 1
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                info["bad"] += 1
                continue
            item = normalize_alert(row) if isinstance(row, dict) else None
            if item is None:
                info["ignored"] += 1
                continue
            items.append(item)
        self._pending = pos
        info["offset_from"], info["offset_to"] = offset, pos
        return items, info

    def commit(self) -> None:
        if self._pending is None:
            return
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cursor_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"path": str(self.path), "offset": self._pending}), encoding="utf-8")
        os.replace(tmp, self.cursor_path)
        self._pending = None


# ------------------------------------------------------------------ Linear poll adapter


def _names(v: Any) -> list[str]:
    if isinstance(v, Mapping) and "nodes" in v:
        v = v["nodes"]
    out: list[str] = []
    for x in v or []:
        n = x.get("name") if isinstance(x, Mapping) else x
        if n:
            out.append(str(n))
    return out


def normalize_issue(issue: Mapping[str, Any]) -> dict[str, Any] | None:
    ident = _first(issue, ("identifier", "id"))
    if ident is None:
        return None
    state = issue.get("state")
    if isinstance(state, Mapping):
        state = state.get("name") or state.get("type")
    return {
        "kind": "linear",
        "id": str(ident)[:80],
        "title": str(issue.get("title") or "")[:300],
        "labels": _names(issue.get("labels")),
        "state": str(state or ""),
        "detail": str(issue.get("description") or "")[:2000],
    }


def linear_items(client: LinearClient, *, label: str = "chip-job", limit: int = 25) -> list[dict[str, Any]]:
    """Poll ``client(label, limit)`` and normalize; the picker policy still filters label/state."""
    rows = client(label, max(1, min(int(limit), 100)))
    return [n for n in (normalize_issue(r) for r in rows or []) if n is not None]


_LINEAR_QUERY = (
    "query($label: String!, $first: Int!) { issues(first: $first, filter: {labels: {name: {eq: $label}}, "
    "state: {type: {nin: [\"completed\", \"canceled\"]}}}) { nodes { identifier title description "
    "state { name type } labels { nodes { name } } } } }"
)


def linear_http_client(api_key: str, *, transport: Callable[..., Any] | None = None, timeout: float = 20.0) -> LinearClient:
    """Minimal Linear GraphQL client (read-only query). ``transport`` defaults to ``urllib.request.urlopen``."""
    if not api_key:
        raise ValueError("Linear API key is required")
    opener = transport or urllib.request.urlopen

    def _client(label: str, limit: int) -> list[Mapping[str, Any]]:
        body = json.dumps({"query": _LINEAR_QUERY, "variables": {"label": label, "first": limit}}).encode()
        req = urllib.request.Request(
            LINEAR_URL, data=body, method="POST", headers={"Content-Type": "application/json", "Authorization": api_key}
        )
        with opener(req, timeout=timeout) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
        if doc.get("errors"):
            raise RuntimeError("linear query failed: " + str(doc["errors"])[:300])
        return list(((doc.get("data") or {}).get("issues") or {}).get("nodes") or [])

    return _client


def linear_client_from_env(env: Mapping[str, str] | None = None) -> LinearClient | None:
    e = _env(env)
    if not _on(e, "CHIP_PM_LINEAR"):
        return None
    key = str(e.get("CHIP_LINEAR_API_KEY", "")).strip()
    return linear_http_client(key) if key else None


# ------------------------------------------------------------------ tick


def _append_history(row: dict[str, Any]) -> None:
    p = history_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def tick(
    now: datetime,
    *,
    alerts: AlertStream | None = None,
    linear: LinearClient | None = None,
    extra_items: Iterable[Mapping[str, Any]] = (),
    step: bg_jobs.Step | None = None,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One PM loop pass. Returns a summary dict (also appended to history unless ``dry_run``)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    e = _env(env)
    if not dry_run and not enabled(e):
        raise PmTickDisabled(f"{FLAG} is not enabled; refusing to run the PM tick")
    now = now.astimezone(timezone.utc)
    out: dict[str, Any] = {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "dry_run": dry_run, "errors": []}
    if not dry_run:
        out["recovered"] = bg_jobs.recover(now)
    mode = budget_mode.current_mode(e)
    out["budget_mode"] = mode
    if mode == budget_mode.STOPPED:
        out.update(status="skipped", reason="budget stopped: no picks, no runs")
        if not dry_run:
            _append_history(out)
        return out
    items: list[dict[str, Any]] = [dict(x) for x in extra_items]
    if alerts is not None:
        got, info = alerts.read()
        items.extend(got)
        out["alerts"] = info
    if linear is not None:
        try:
            got = linear_items(linear, label=(bg_picker.load_policy(env=e)["linear_labels"] or ["chip-job"])[0])
            items.extend(got)
            out["linear"] = {"issues": len(got)}
        except Exception as exc:  # a broken poll must not stop the alert path
            out["errors"].append(f"linear: {type(exc).__name__}: {exc}"[:300])
    out["inputs"] = len(items)
    picked = bg_picker.pick(items, now=now, dry_run=dry_run, env=e)
    if alerts is not None and not dry_run:
        alerts.commit()
    out["picked"] = [j["id"] for j in picked["enqueued"]]
    out["skipped"] = picked["skipped"]
    out["picks_today"], out["daily_max"] = picked["picks_today"], picked["daily_max"]
    if dry_run:
        out["status"] = "dry-run"
        return out
    if not bg_jobs.enabled(e):
        out.update(status="picked-only", reason=f"{bg_jobs.FLAG} off: jobs queued, not run")
    else:
        res = bg_jobs.run_pending(now, step, env=e)
        out["ran"] = [{"id": j["id"], "status": j["status"], "reason": j.get("reason", "")} for j in res["ran"]]
        out["deferred"] = res["deferred"]
        out["spend_usd"] = round(sum(float(j.get("spend_usd") or 0.0) for j in res["ran"]), 6)
        out["status"] = "ok"
    _append_history(out)
    return out


def routine_dispatch(
    fallback: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    make_tick: Callable[[], dict[str, Any]],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Routine runner: ``kind=pm-tick`` routines run ``make_tick()``; others go to ``fallback``."""

    def _run(routine: dict[str, Any]) -> dict[str, Any]:
        if routine.get("kind") != ROUTINE_KIND:
            return fallback(routine)
        res = make_tick()
        summary = (
            f"pm-tick {res.get('status')}: inputs={res.get('inputs', 0)} picked={len(res.get('picked', []))} "
            f"ran={len(res.get('ran', []))}" + (f" ({res['reason']})" if res.get("reason") else "")
        )
        if res.get("errors"):
            summary += " errors=" + "; ".join(res["errors"])
        return {
            "status": "ok" if res.get("status") in {"ok", "picked-only", "skipped"} else "failed",
            "summary": summary[:2000],
            "spend_usd": float(res.get("spend_usd", 0.0) or 0.0),
        }

    return _run
