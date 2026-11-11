"""Alert remediations: operator-pinned alert -> fix-job templates for the PM (parity D slice 4).

The PM picker (:mod:`chip.bg_picker`) normally turns an alert into a *read-only* investigation job.
A **remediation** lets the operator say, ahead of time, "when an alert with this id shows up, the
PM may queue this exact fix job". The fix job is a ``mutate`` background job whose
``params.policy_request`` goes through the job policy (:mod:`chip.job_policy`) and the executor
(:mod:`chip.policy_exec`) like any other dev-bot request, so every rule still applies: hard denies,
job-type command/target/script allowlists, caps, the sudo decision (operator rule, YOLO grant or
ask), decision-bound tickets and receipts.

Remediations live in the **pinned policy file** (``CHIP_POLICY_FILE``) under ``remediations``, so
only the operator can add or change one (an edit without a fresh ``chip policy pin`` disables them
all, and :func:`job_policy.evaluate` denies anyway)::

    {
      "job_types": {...},
      "remediations": [
        {
          "id": "container-heal",
          "match": "^container-down:(?P<target>[a-z0-9][a-z0-9_.-]{0,62})$",
          "requested_by": "dev:remediator",
          "job_type": "container-heal",
          "action": "restart-container",
          "command": ["/opt/chip/heal.sh", "{target}"],
          "target": "{target}",
          "script_path": "/opt/chip/heal.sh",
          "sudo": true,
          "max_seconds": 180,
          "reason": "container reported down; restart it and verify health"
        }
      ]
    }

Rules the template cannot change:

* ``match`` is a regex matched in full against the alert **id** only (never title/detail text);
  the only placeholder is ``{target}``, filled from the named group ``target``, which must also
  match :data:`SAFE_TARGET` (no paths, spaces, shell syntax or leading dashes);
* ``requested_by`` must be a dev bot (``dev:<name>``); the PM still decides;
* the script hash is read from disk at plan time and must match the policy pin at decision time;
* a planned job is single-hop, spends no model money (``max_spend_usd`` 0) and runs only via the
  executor (``params.exec``).

Gated by ``CHIP_PICKER_REMEDIATE=1`` on top of ``CHIP_JOB_POLICY=1`` and ``CHIP_POLICY_EXEC=1``
(all default off). With any of them off, alerts stay read-only investigations.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from chip import job_policy as jp

FLAG = "CHIP_PICKER_REMEDIATE"
SAFE_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
PLACEHOLDER = "{target}"
MAX_REMEDIATIONS = 50
_TEMPLATE_KEYS = frozenset(
    {"id", "match", "requested_by", "job_type", "action", "command", "target", "context", "script_path",
     "sudo", "max_seconds", "reason"}
)


def enabled(env: Mapping[str, str] | None = None) -> bool:
    e = jp._env(env)
    if not jp._truthy(e.get(FLAG, "0")) or not jp.enabled(e):
        return False
    from chip import policy_exec

    return policy_exec.exec_enabled(e)


def load(env: Mapping[str, str] | None = None) -> tuple[list[dict[str, Any]], str]:
    """Remediation templates from the pinned policy file: ``(templates, why_empty)``.

    Returns no templates when the policy file is missing, unpinned, changed since the pin, or the
    authority store does not verify (``why_empty`` says which). Malformed templates are skipped.
    """
    e = jp._env(env)
    try:
        rows = jp.verify_chain(e)
    except jp.AuthorityTampered as exc:
        return [], f"authority store tampered: {exc}"
    try:
        policy, sha = jp.load_policy(e)
    except jp.PolicyError as exc:
        return [], f"policy unavailable: {exc}"
    if sha is None:
        return [], "no policy file"
    if jp.pinned_policy_sha(rows) != sha:
        return [], "policy file changed or not pinned by the operator"
    raw = policy.get("remediations") or []
    if not isinstance(raw, list):
        return [], "remediations must be a list"
    out = []
    for t in raw[:MAX_REMEDIATIONS]:
        if _valid(t):
            out.append(dict(t))
    return out, "" if out else "no valid remediations"


def _valid(t: Any) -> bool:
    if not isinstance(t, Mapping) or set(t) - _TEMPLATE_KEYS:
        return False
    for k in ("id", "match", "requested_by", "job_type", "action"):
        if not isinstance(t.get(k), str) or not t.get(k):
            return False
    if not str(t["requested_by"]).lower().startswith(jp.DEV_PREFIX):
        return False
    cmd = t.get("command")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x for x in cmd):
        return False
    if not isinstance(t.get("sudo", False), bool):
        return False
    try:
        rx = re.compile(t["match"])
    except re.error:
        return False
    return "target" in rx.groupindex or not any(PLACEHOLDER in str(v) for v in (*cmd, t.get("target", ""), t.get("context", "")))


def _fill(value: str, target: str) -> str:
    return value.replace(PLACEHOLDER, target)


def plan(item: Mapping[str, Any], env: Mapping[str, str] | None = None,
         templates: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The fix plan for one picker input, or None. Pure apart from hashing the script file.

    Returns ``{"remediation": id, "policy_request": {...}, "pm_reason": str}``; the request's
    ``job_id`` is filled by the picker (it is forced to the bg job id at gate time anyway).
    """
    if str(item.get("kind", "")).strip().lower() != "alert":
        return None
    ident = str(item.get("id", "")).strip()
    if not ident:
        return None
    if templates is None:
        templates, _ = load(env)
    for t in templates:
        m = re.fullmatch(t["match"], ident)
        if not m:
            continue
        target = m.groupdict().get("target") or ""
        if target and not SAFE_TARGET.match(target):
            return None  # an unsafe capture never falls through to a looser template
        req: dict[str, Any] = {
            "job_id": "",
            "requested_by": t["requested_by"],
            "job_type": t["job_type"],
            "action": t["action"],
            "command": [_fill(x, target) for x in t["command"]],
            "target": _fill(str(t.get("target") or ""), target),
            "context": _fill(str(t.get("context") or f"alert:{ident}"), target)[:200],
            "sudo": bool(t.get("sudo", False)),
            "reason": str(t.get("reason") or f"remediation {t['id']} for alert {ident}")[:500],
            "max_spend_usd": 0.0,
            "max_seconds": int(t.get("max_seconds") or 0),
        }
        sp = str(t.get("script_path") or "")
        if sp:
            path = Path(sp)
            if not path.is_file():
                return None
            req["script_path"] = sp
            req["script_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        reason = f"PM: alert {ident} matches pinned remediation {t['id']}; queue {t['action']} on {req['target'] or '-'}"
        return {"remediation": t["id"], "policy_request": req, "pm_reason": reason[:500]}
    return None


__all__ = ["FLAG", "SAFE_TARGET", "enabled", "load", "plan"]
