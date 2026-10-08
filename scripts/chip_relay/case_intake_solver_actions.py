"""Solver action law — diagnostic + remediation ops with full receipts.

Binding hardlines (solver kit spec):
1. Pack-declared forbidden target fragments are REFUSED + escalate row, always — even if allowlisted above.
2. No delete-file/delete-library op exists in the allowlist at all.
3. Unknown ops fail closed + escalate row.
4. Every action writes a PRE row and a POST row to the case file AND the JSONL
   ledger — failures are post rows, never silent.
5. fix_script requires an explicit env allowlist (default empty => refuse+escalate).
6. Destructive/irreversible ops outside the established set are never improvised.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from chip import config as chip_config
from chip import pack_values, restart_policy

ACTION_CAP = 2
TOOL_CAP = 5
_SUBPROCESS_TIMEOUT = 20

RESTART_ALLOWLIST_ENV = "CASE_INTAKE_SOLVER_RESTART_ALLOW"
FIX_SCRIPTS_ENV = "CASE_INTAKE_SOLVER_FIX_SCRIPTS"

_BASE_RESTART_ALLOW = pack_values.service_allowlist()
_FORBIDDEN = pack_values.solver_forbidden_substrings()


def _escalate_to() -> str:
    return pack_values.operator_name() or "operator"


def _forbidden(target: str) -> str | None:
    t = (target or "").lower()
    return next((name for name in _FORBIDDEN if name in t), None)


def _ledger_path() -> Path:
    return Path(chip_config.data_dir()) / "case_intake" / "solver_action_log.jsonl"


def _synthetic_case(case_path: Path) -> bool:
    """Synthetic cases never execute host mutations (smoke / harness only)."""
    try:
        if not case_path.is_file():
            return False
        body = json.loads(case_path.read_text(encoding="utf-8"))
        return isinstance(body, dict) and bool(body.get("synthetic"))
    except (OSError, json.JSONDecodeError):
        return False


def _synthetic_dry_run(case_path: Path, base: dict[str, Any]) -> dict[str, Any]:
    return _receipt(
        case_path,
        **base,
        phase="pre-dry-run",
        allowed=False,
        reason="synthetic-case-no-mutations",
        dry_run=True,
    )


def write_action_rows(
    case_path: Path,
    rows: list[dict[str, Any]],
    *,
    ledger_path: Path | None = None,
) -> None:
    """Append receipt rows to the case file action_log AND the JSONL ledger."""
    try:
        body = json.loads(case_path.read_text(encoding="utf-8")) if case_path.is_file() else {}
        body.setdefault("action_log", []).extend(rows)
        case_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    path = ledger_path or _ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _receipt(case_path: Path, **fields: Any) -> dict[str, Any]:
    row = {"ts": time.time(), **fields}
    write_action_rows(case_path, [row])
    return row


def docker_restart(
    container: str,
    *,
    environ: dict[str, str],
    case_id: str,
    case_path: Path,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Restart an ALLOWLISTED container with pre/post receipts. Never raises."""
    target = str(container or "").strip()
    wanted = target.lower()
    base = {
        "action": "docker_restart", "target": target, "case_id": case_id,
    }
    allow = restart_policy.solver_restart_allowlist(environ)
    if allow is None:
        detail = "no system pack/restart policy loaded (fail-closed)"
        restart_policy._log_refusal("case_intake_solver", detail)
        return _receipt(
            case_path,
            **base,
            phase="pre-refused",
            allowed=False,
            reason=detail,
            escalate_to=_escalate_to(),
        )
    if wanted in restart_policy.solver_forbidden_container_names():
        return _receipt(
            case_path,
            **base,
            phase="pre-refused",
            allowed=False,
            reason=f"forbidden-target:{wanted}",
            escalate_to=_escalate_to(),
        )
    hit = _forbidden(target)
    if hit:
        return _receipt(case_path, **base, phase="pre-refused",
                        allowed=False, reason=f"forbidden-target:{hit}", escalate_to=_escalate_to())
    if wanted not in allow:
        return _receipt(case_path, **base, phase="pre-refused", allowed=False,
                        reason="not-in-restart-allowlist", escalate_to=_escalate_to())
    if _synthetic_case(case_path):
        return _synthetic_dry_run(case_path, base)

    _receipt(case_path, **base, phase="pre", allowed=True)
    try:
        run = runner or subprocess.run
        proc = run(["docker", "restart", target], capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT, check=False)
        post = _receipt(case_path, **base, phase="post", allowed=True,
                        ok=proc.returncode == 0, detail=(proc.stdout or proc.stderr or "")[:200])
        return post
    except Exception as exc:  # noqa: BLE001 — action faults are receipts, not crashes
        return _receipt(case_path, **base, phase="post", allowed=True,
                        ok=False, detail=f"{exc.__class__.__name__}: {exc}"[:200], escalate_to=_escalate_to())


def fix_script(
    script: str,
    *,
    environ: dict[str, str],
    case_id: str,
    case_path: Path,
    runner: Callable[..., Any] | None = None,
    cwd: str = "/app/scripts",
) -> dict[str, Any]:
    """Run an ENV-ALLOWLISTED fix script with pre/post receipts. Never raises."""
    name = str(script or "").strip()
    base = {"action": "fix_script", "target": name, "case_id": case_id}
    hit = _forbidden(name)
    if hit:
        return _receipt(case_path, **base, phase="pre-refused", allowed=False,
                        reason=f"forbidden-target:{hit}", escalate_to=_escalate_to())
    allow = {x.strip() for x in (environ.get(FIX_SCRIPTS_ENV) or "").split(",") if x.strip()}
    if not name or name not in allow:
        return _receipt(case_path, **base, phase="pre-refused", allowed=False,
                        reason="not-in-fix-script-allowlist", escalate_to=_escalate_to())
    if _synthetic_case(case_path):
        return _synthetic_dry_run(case_path, base)

    _receipt(case_path, **base, phase="pre", allowed=True)
    try:
        run = runner or subprocess.run
        proc = run(["python3", name], capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT, cwd=cwd, check=False)
        return _receipt(case_path, **base, phase="post", allowed=True,
                        ok=proc.returncode == 0, detail=(proc.stdout or proc.stderr or "")[:400])
    except Exception as exc:  # noqa: BLE001
        return _receipt(case_path, **base, phase="post", allowed=True,
                        ok=False, detail=f"{exc.__class__.__name__}: {exc}"[:200], escalate_to=_escalate_to())


def run_probe(
    url: str,
    *,
    case_id: str,
    case_path: Path,
    fetch: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Full probe: pre receipt + bounded fetch + post receipt."""
    target = str(url or "").strip()
    base = {"action": "probe", "target": target[:120], "case_id": case_id}
    if not target.startswith(("http://", "https://")) or len(target) > 200:
        return _receipt(case_path, **base, phase="pre-refused", allowed=False,
                        reason="bad-url", escalate_to=_escalate_to())
    _receipt(case_path, **base, phase="pre", allowed=True)
    try:
        if fetch is not None:
            result = fetch(target)
        else:
            with urllib.request.urlopen(target, timeout=3) as resp:
                data = resp.read(512)
            result = {"status": resp.status, "bytes": len(data)}
        return _receipt(case_path, **base, phase="post", allowed=True,
                        ok=True, detail=json.dumps(result)[:200])
    except Exception as exc:  # noqa: BLE001
        return _receipt(case_path, **base, phase="post", allowed=True,
                        ok=False, detail=f"{exc.__class__.__name__}: {exc}"[:200], escalate_to=_escalate_to())


PROBE = run_probe  # canonical alias used by the kit


def docker_inspect(
    container: str,
    *,
    case_id: str,
    case_path: Path,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Read-only docker inspect with pre/post receipts. Never raises."""
    target = str(container or "").strip()
    base = {"action": "docker_inspect", "target": target[:120], "case_id": case_id}
    if not re.fullmatch(r"[\w.-]+", target):
        return _receipt(case_path, **base, phase="pre-refused", allowed=False,
                        reason="bad-container-name")
    if _synthetic_case(case_path):
        return _synthetic_dry_run(case_path, base)
    _receipt(case_path, **base, phase="pre", allowed=True)
    try:
        run = runner or subprocess.run
        proc = run(["docker", "inspect", "--format", "{{.State.Status}} {{.State.Health}}", target],
                   capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT, check=False)
        return _receipt(case_path, **base, phase="post", allowed=True,
                        ok=proc.returncode == 0, detail=(proc.stdout or proc.stderr or "")[:200])
    except Exception as exc:  # noqa: BLE001
        return _receipt(case_path, **base, phase="post", allowed=True,
                        ok=False, detail=f"{exc.__class__.__name__}: {exc}"[:200])


def unknown_action(op: str, target: str, *, case_id: str, case_path: Path) -> dict[str, Any]:
    """Fail-closed receipt for ops outside the law (incl. delete attempts)."""
    base = {"action": str(op)[:40], "target": str(target)[:120], "case_id": case_id}
    return _receipt(case_path, **base, phase="pre-refused", allowed=False,
                    reason="op-outside-allowlist", escalate_to=_escalate_to())
