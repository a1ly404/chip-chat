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
   * :class:`RoutineFailures` — new ``failed`` rows in scheduled-routine run history become
     ``warning`` alerts (``routine-failed:<id>``), with a persisted per-routine line cursor.
     ``pm-tick`` routines are excluded so the PM never opens jobs about its own loop.

   Real relay alerts often carry no severity (``{"key", "detail", "raw"}`` plus a ``gate``).
   :func:`infer_severity` derives one from the text with ordered regex rules (picker policy
   ``alert_severity_rules``; defaults: resolved/recovered -> info, outage/leak/data loss ->
   critical, down/unhealthy/failed/crash/... -> warning, no match -> not picked). Rows the relay
   marked ``gate=ignored`` are dropped, and alerts with no id get a stable text fingerprint.

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

import hashlib
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from chip import bg_jobs, bg_picker, budget_mode, routines
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


DEFAULT_SEVERITY_RULES: tuple[tuple[str, str], ...] = (
    (r"\b(resolved|recovered|back up|back online|healthy again|cleared)\b", "info"),
    (r"\b(critical|outage|data ?loss|leak(ed|ing)?|breach)\b", "critical"),
    (
        (
            r"\b(down|unhealthy|unreachable|exited|crash(ed|es|ing|loop)?|failed|failing|failure|error|oom|"
        r"timed? ?out|timeout|degraded|stuck|not running|restart(ing)? loop)\b"
        ),
        "warning",
    ),
)
SEVERITIES = frozenset({"critical", "warning", "info"})


def severity_rules(policy: Mapping[str, Any] | None = None) -> list[tuple[re.Pattern[str], str]]:
    """Compile ``alert_severity_rules`` (list of ``[regex, severity]``); bad entries are skipped."""
    raw = (policy or {}).get("alert_severity_rules")
    pairs = raw if isinstance(raw, list) and raw else list(DEFAULT_SEVERITY_RULES)
    out: list[tuple[re.Pattern[str], str]] = []
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        pat, sev = str(pair[0]), str(pair[1]).strip().lower()
        if sev not in SEVERITIES or len(pat) > 500:
            continue
        try:
            out.append((re.compile(pat, re.IGNORECASE), sev))
        except re.error:
            continue
    return out


def infer_severity(text: str, rules: list[tuple[re.Pattern[str], str]] | None = None) -> str:
    """First matching rule wins; ``""`` when nothing matches (the picker then skips the alert)."""
    blob = str(text or "")[:4000]
    for rx, sev in rules if rules is not None else severity_rules():
        if rx.search(blob):
            return sev
    return ""


def normalize_alert(
    row: Mapping[str, Any], *, rules: list[tuple[re.Pattern[str], str]] | None = None
) -> dict[str, Any] | None:
    """Map one alert event (flat, or a relay row with nested ``alert``) to a picker input."""
    if not isinstance(row, Mapping):
        return None
    if str(row.get("gate", "")).strip().lower() == "ignored" or row.get("ignored") is True:
        return None  # the relay already decided this alert is noise
    inner = row.get("alert")
    a: Mapping[str, Any] = inner if isinstance(inner, Mapping) else row
    title = _first(a, ("title", "summary", "message", "name", "key")) or ""
    detail = _first(a, ("detail", "description", "body", "text")) or ""
    raw_text = a.get("raw") if isinstance(a.get("raw"), str) else ""
    ident = _first(a, ("id", "fingerprint", "key", "alert_id", "name")) or _first(row, ("id", "fingerprint"))
    text = " ".join(str(x) for x in (title, detail, raw_text) if x).strip()
    if ident is None:
        if not text:
            return None
        ident = "fp-" + hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()[:16]
    sev = str(_first(a, ("severity", "level", "priority")) or "").strip().lower()
    out = {
        "kind": "alert",
        "id": str(ident)[:120],
        "title": str(title or text)[:300],
        "severity": sev,
        "detail": str(detail or (raw_text if raw_text != title else ""))[:2000],
    }
    if not sev:
        status = str(a.get("status") or "").strip().lower()
        out["severity"] = infer_severity(f"{ident} {text} {status}", rules)
        out["severity_source"] = "inferred"
    return out


class AlertStream:
    """Read new JSONL alert events since the last committed cursor (byte offset)."""

    def __init__(
        self,
        path: Path,
        *,
        cursor_name: str = "pm_tick_alert_cursor.json",
        rules: list[tuple[re.Pattern[str], str]] | None = None,
    ) -> None:
        self.path = Path(path)
        self.rules = rules
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
            item = normalize_alert(row, rules=self._rules()) if isinstance(row, dict) else None
            if item is None:
                info["ignored"] += 1
                continue
            items.append(item)
        self._pending = pos
        info["offset_from"], info["offset_to"] = offset, pos
        return items, info

    def _rules(self) -> list[tuple[re.Pattern[str], str]]:
        if self.rules is None:
            self.rules = severity_rules(bg_picker.load_policy())
        return self.rules

    def commit(self) -> None:
        if self._pending is None:
            return
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cursor_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"path": str(self.path), "offset": self._pending}), encoding="utf-8")
        os.replace(tmp, self.cursor_path)
        self._pending = None


class MultiAlertStream:
    """Several :class:`AlertStream` files read as one (each keeps its own cursor)."""

    def __init__(self, streams: list[AlertStream]) -> None:
        self.streams = list(streams)

    def read(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        items: list[dict[str, Any]] = []
        infos = []
        for st in self.streams:
            got, info = st.read()
            items.extend(got)
            infos.append(info)
        total = {k: sum(int(i.get(k, 0) or 0) for i in infos) for k in ("lines", "bad", "ignored")}
        return items, {**total, "streams": infos}

    def commit(self) -> None:
        for st in self.streams:
            st.commit()


def extra_alert_paths(env: Mapping[str, str] | None = None) -> list[Path]:
    """``CHIP_PM_ALERTS_EXTRA_PATHS``: more JSONL alert files (comma or os.pathsep separated),
    e.g. a deployment's own health probe output, read alongside the default alert log."""
    raw = str(_env(env).get("CHIP_PM_ALERTS_EXTRA_PATHS", "")).strip()
    parts = [p.strip() for p in re.split(r"[,%s]" % re.escape(os.pathsep), raw) if p.strip()]
    out: list[Path] = []
    for p in parts[:8]:
        path = Path(p)
        if path not in out:
            out.append(path)
    return out


def default_alert_stream(env: Mapping[str, str] | None = None) -> AlertStream | MultiAlertStream:
    """The default alert log plus any ``CHIP_PM_ALERTS_EXTRA_PATHS`` files (one cursor per file)."""
    base = AlertStream(default_alerts_path(env))
    extra = extra_alert_paths(env)
    if not extra:
        return base
    streams = [base]
    for path in extra:
        tag = hashlib.sha256(str(path).encode()).hexdigest()[:12]
        streams.append(AlertStream(path, cursor_name=f"pm_tick_alert_cursor_{tag}.json"))
    return MultiAlertStream(streams)


# ------------------------------------------------------------------ routine failure adapter


class RoutineFailures:
    """New ``failed`` routine run-history rows -> ``warning`` alert inputs (read once each).

    The cursor maps routine id -> history rows already seen. A routine seen for the first time
    starts at its current end unless ``backfill=True``, so enabling the adapter does not replay
    old failures. ``pm-tick`` routines are skipped (no jobs about the PM loop itself).
    """

    SKIP_KINDS = frozenset({ROUTINE_KIND})

    def __init__(self, *, cursor_name: str = "pm_tick_routine_cursor.json", backfill: bool = False) -> None:
        self.cursor_path = data_dir() / cursor_name
        self.backfill = backfill
        self._pending: dict[str, int] | None = None

    def _cursor(self) -> dict[str, int]:
        try:
            raw = json.loads(self.cursor_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): int(v) for k, v in raw.items() if isinstance(v, int) and v >= 0}

    def read(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        cur = self._cursor()
        first_run = not self.cursor_path.is_file()
        nxt: dict[str, int] = dict(cur)
        items: list[dict[str, Any]] = []
        info: dict[str, Any] = {"routines": 0, "new_rows": 0, "failed": 0}
        rdir = routines.routines_dir()
        files = sorted(rdir.glob("*.history.jsonl")) if rdir.is_dir() else []
        for path in files:
            rid = path.name[: -len(".history.jsonl")]
            try:
                lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            except OSError:
                continue
            info["routines"] += 1
            start = cur.get(rid)
            if start is None:
                start = 0 if (self.backfill or not first_run) else len(lines)
            if start > len(lines):  # history replaced: start over
                start = 0
            nxt[rid] = len(lines)
            for ln in lines[start:]:
                info["new_rows"] += 1
                try:
                    row = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict) or row.get("status") != "failed":
                    continue
                kind = str(row.get("kind") or "")
                if kind in self.SKIP_KINDS:
                    continue
                info["failed"] += 1
                items.append(
                    {
                        "kind": "alert",
                        "id": f"routine-failed:{rid}"[:120],
                        "title": f"Scheduled routine {rid} ({kind or '?'}) failed",
                        "severity": "warning",
                        "detail": str(row.get("summary") or "")[:2000],
                    }
                )
        self._pending = nxt
        return items, info

    def commit(self) -> None:
        if self._pending is None:
            return
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cursor_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._pending, sort_keys=True), encoding="utf-8")
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
    alerts: AlertStream | MultiAlertStream | None = None,
    linear: LinearClient | None = None,
    routine_failures: RoutineFailures | None = None,
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
    if routine_failures is not None:
        got, info = routine_failures.read()
        items.extend(got)
        out["routine_failures"] = info
    if linear is not None:
        try:
            got = linear_items(linear, label=(bg_picker.load_policy(env=e)["linear_labels"] or ["chip-job"])[0])
            items.extend(got)
            out["linear"] = {"issues": len(got)}
        except Exception as exc:  # a broken poll must not stop the alert path
            out["errors"].append(f"linear: {type(exc).__name__}: {exc}"[:300])
    out["inputs"] = len(items)
    picked = bg_picker.pick(items, now=now, dry_run=dry_run, env=e)
    if not dry_run:
        for adapter in (alerts, routine_failures):
            if adapter is not None:
                adapter.commit()
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
