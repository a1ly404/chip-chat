"""Reusable monitoring skills for #monitoring-logs — config-driven, no per-id code paths."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from chip import config
from chip_relay import alert_ingest

REGISTRY_FILE = config.REPO_ROOT / "config" / "chip_chat" / "monitoring_skills.json"

PLEX_FIX_CHANNEL_ID = "1553148704494714891"
FORBIDDEN_WEBHOOK_ENVS = frozenset({"CHIP_WEBHOOK_PLEX_FIX", "CHIP_WEBHOOK_PLEX"})
FORBIDDEN_POST_SUBSTRINGS = ("plex-fix", "/plex-fix")

VPN_MUTATION_PATTERNS = (
    "docker restart gluetun",
    "recreate gluetun",
    "compose up",
)


@dataclass(frozen=True)
class RegistryPolicy:
    forbidden_restart_containers: frozenset[str]
    allowed_restart_containers: frozenset[str]
    soak_seconds: float
    overlay_defaults: dict[str, Any]


@dataclass(frozen=True)
class SkillDef:
    id: str
    title: str
    match_any: tuple[str, ...]
    body_path: str
    fix_action: dict[str, Any]
    followup: str = ""


@dataclass(frozen=True)
class SkillRun:
    skill_id: str
    created: bool
    matched: bool
    receipt: str
    learning_line: str
    ignored: bool = False


@dataclass(frozen=True)
class FixResult:
    applied: bool
    detail: str
    homelab_followup: str = ""


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_registry_raw(path: Path | None = None) -> dict[str, Any]:
    file = path or REGISTRY_FILE
    return json.loads(file.read_text(encoding="utf-8"))


def load_policy(path: Path | None = None) -> RegistryPolicy:
    raw = _load_registry_raw(path)
    policy = raw.get("policy") if isinstance(raw.get("policy"), dict) else {}
    forbidden = frozenset(str(x).strip().lower() for x in (policy.get("forbidden_restart_containers") or []))
    allowed = frozenset(str(x).strip().lower() for x in (policy.get("allowed_restart_containers") or []))
    soak = float(raw.get("soak_seconds") or 600)
    overlay_defaults = raw.get("overlay_defaults") if isinstance(raw.get("overlay_defaults"), dict) else {}
    return RegistryPolicy(
        forbidden_restart_containers=forbidden,
        allowed_restart_containers=allowed,
        soak_seconds=soak,
        overlay_defaults=overlay_defaults,
    )


def load_ignore_patterns(path: Path | None = None) -> tuple[str, ...]:
    raw = _load_registry_raw(path)
    return tuple(str(p) for p in (raw.get("ignore_any") or []) if str(p).strip())


def should_ignore_alert(alert: Mapping[str, str], patterns: tuple[str, ...] | None = None) -> bool:
    patterns = patterns if patterns is not None else load_ignore_patterns()
    blob = f"{alert.get('key', '')} {alert.get('detail', '')} {alert.get('raw', '')}".strip()
    if not blob or not patterns:
        return False
    return any(_pattern_hits(p, blob) for p in patterns)


def _skill_from_row(row: dict[str, Any]) -> SkillDef | None:
    sid = str(row.get("id") or "").strip()
    if not sid:
        return None
    patterns = tuple(str(p) for p in (row.get("match_any") or []) if str(p).strip())
    fix_action = row.get("fix_action") if isinstance(row.get("fix_action"), dict) else {"kind": "watch_only"}
    return SkillDef(
        id=sid,
        title=str(row.get("title") or sid),
        match_any=patterns,
        body_path=str(row.get("body_path") or ""),
        fix_action=fix_action,
        followup=str(row.get("followup") or ""),
    )


def load_registry(path: Path | None = None) -> list[SkillDef]:
    raw = _load_registry_raw(path)
    out: list[SkillDef] = []
    for row in raw.get("skills") or []:
        if isinstance(row, dict):
            skill = _skill_from_row(row)
            if skill is not None:
                out.append(skill)
    return out


def overlay_registry_path() -> Path:
    return config.data_dir() / "chip_state" / "monitoring_skills_overlay.json"


def load_overlay_skills() -> list[SkillDef]:
    path = overlay_registry_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    out: list[SkillDef] = []
    for row in raw.get("skills") or []:
        if isinstance(row, dict):
            skill = _skill_from_row(row)
            if skill is not None:
                out.append(skill)
    return out


def all_skills() -> list[SkillDef]:
    """Registry file order is match priority (first hit wins); overlay appends new ids."""
    ordered = list(load_registry())
    known = {s.id for s in ordered}
    for skill in load_overlay_skills():
        if skill.id not in known:
            ordered.append(skill)
            known.add(skill.id)
    return ordered


def parse_monitoring_message(text: str) -> dict[str, str]:
    raw = (text or "").strip()
    if not raw:
        return {"key": "", "detail": "", "raw": ""}
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return {
                "key": str(payload.get("key") or ""),
                "detail": str(payload.get("detail") or ""),
                "raw": raw[:500],
            }
    except json.JSONDecodeError:
        pass
    key_match = re.search(r"\bkey[=:]\s*([^\s|]+)", raw, re.IGNORECASE)
    detail_match = re.search(r"\bdetail[=:]\s*(.+)$", raw, re.IGNORECASE)
    if key_match:
        return {
            "key": key_match.group(1).strip(),
            "detail": (detail_match.group(1).strip() if detail_match else raw)[:200],
            "raw": raw[:500],
        }
    return {"key": "", "detail": raw[:200], "raw": raw[:500]}


def _pattern_hits(pattern: str, haystack: str) -> bool:
    pat = pattern.strip()
    if not pat:
        return False
    if pat.startswith("(") or ".*" in pat or pat.endswith("$"):
        return re.search(pat, haystack, re.IGNORECASE) is not None
    return pat.lower() in haystack.lower()


def match_skill(alert: Mapping[str, str], skills: list[SkillDef] | None = None) -> SkillDef | None:
    skills = skills if skills is not None else all_skills()
    blob = f"{alert.get('key', '')} {alert.get('detail', '')} {alert.get('raw', '')}".strip()
    if not blob:
        return None
    for skill in skills:
        if any(_pattern_hits(p, blob) for p in skill.match_any):
            return skill
    ingest_key = str(alert.get("key") or "").strip()
    if ingest_key:
        skill_name, verdict = alert_ingest.classify(
            {"alert_id": "discord", "key": ingest_key, "detail": alert.get("detail", "")}
        )
        if not verdict.startswith("unmatched"):
            sid = re.sub(r"[^a-z0-9-]+", "-", skill_name.lower()).strip("-")[:64]
            if sid:
                policy = load_policy()
                defaults = policy.overlay_defaults
                fix_action = defaults.get("fix_action") if isinstance(defaults.get("fix_action"), dict) else {"kind": "watch_only"}
                return SkillDef(
                    id=sid,
                    title=skill_name,
                    match_any=(ingest_key,),
                    body_path="",
                    fix_action=fix_action,
                    followup=str(defaults.get("followup") or ""),
                )
    return None


def create_overlay_skill(alert: Mapping[str, str]) -> SkillDef:
    policy = load_policy()
    defaults = policy.overlay_defaults
    fix_action = defaults.get("fix_action") if isinstance(defaults.get("fix_action"), dict) else {"kind": "watch_only"}
    base = str(alert.get("key") or alert.get("detail") or "monitoring-alert")[:48]
    sid = re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-") or "monitoring-alert"
    pattern = str(alert.get("key") or alert.get("detail") or sid)[:120]
    skill = SkillDef(
        id=sid,
        title=f"auto:{sid}",
        match_any=(pattern,),
        body_path="",
        fix_action=fix_action,
        followup=str(defaults.get("followup") or ""),
    )
    path = overlay_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {"skills": []}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {"skills": []}
    rows = [r for r in (existing.get("skills") or []) if str(r.get("id")) != sid]
    rows.append(
        {
            "id": skill.id,
            "title": skill.title,
            "match_any": list(skill.match_any),
            "body_path": "",
            "fix_action": skill.fix_action,
            "followup": skill.followup,
            "created_from": alert.get("raw", "")[:160],
        }
    )
    path.write_text(json.dumps({"skills": rows}, indent=1, sort_keys=True), encoding="utf-8")
    return skill


def learnings_path(skill_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", skill_id)
    return config.data_dir() / "chip_state" / "monitoring_skills" / safe / "learnings.jsonl"


def load_skill_learnings(skill_id: str, *, limit: int = 5) -> list[str]:
    path = learnings_path(skill_id)
    if not path.is_file():
        return []
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line:
            lines.append(line)
    return lines[-limit:]


def append_skill_learning(skill_id: str, lesson: str, *, tag: str = "monitoring") -> str:
    lesson = lesson.strip()
    if not lesson:
        return ""
    path = learnings_path(skill_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = f"{_ts()} | monitoring-logs | {lesson} | {tag}"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(row + "\n")
    return row


def receipt_violates_vpn_law(text: str) -> str | None:
    """Scan only the ``next:`` line so policy prose in claim/evidence cannot false-refuse."""
    for line in (text or "").splitlines():
        if not line.lower().startswith("next:"):
            continue
        low = line.lower()
        for pat in VPN_MUTATION_PATTERNS:
            if pat in low:
                return f"forbidden VPN/container mutation: {pat}"
        if "docker restart gluetun" in low or "docker restart qbittorrent" in low:
            return "forbidden container restart"
    return None


def retired_probe_targets_path() -> Path:
    return config.data_dir() / "chip_state" / "monitoring_fixes" / "retired_probe_targets.json"


def _load_retired_probe_targets() -> list[str]:
    path = retired_probe_targets_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(raw, dict):
        rows = raw.get("targets")
        if isinstance(rows, list):
            return [str(x).strip().lower() for x in rows if str(x).strip()]
    return []


def _save_retired_probe_targets(targets: list[str]) -> None:
    path = retired_probe_targets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = sorted({t.strip().lower() for t in targets if t.strip()})
    path.write_text(json.dumps({"targets": normalized}, indent=1, sort_keys=True), encoding="utf-8")


def _docker_running(name: str) -> bool | None:
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return False
    return proc.stdout.strip().lower() == "true"


def _docker_restart(name: str, policy: RegistryPolicy) -> tuple[bool, str]:
    wanted = name.strip().lower()
    if wanted in policy.forbidden_restart_containers:
        return False, f"refused: {wanted} restart forbidden by policy"
    if wanted not in policy.allowed_restart_containers:
        return False, f"refused: {wanted} not in allowed_restart_containers"
    try:
        proc = subprocess.run(
            ["docker", "restart", name],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{exc.__class__.__name__}"
    ok = proc.returncode == 0
    tail = (proc.stderr or proc.stdout or "").strip()[:80]
    return ok, f"exit={proc.returncode} {tail}".strip()


def _gluetun_container_action_forbidden(action: dict[str, Any]) -> str | None:
    """Gluetun is not a live container — only retire stale probe labels."""
    kind = str(action.get("kind") or "").strip().lower()
    if kind == "record_retired_target":
        return None
    container = str(action.get("container") or "").strip().lower()
    if container == "gluetun":
        return "gluetun is a stale probe label only (live VPN is wireguard)"
    if kind in {"docker_restart_if_down", "docker_health_check", "docker_inspect"}:
        if "gluetun" in json.dumps(action).lower():
            return "forbidden gluetun container operation"
    return None


def execute_fix_action(
    skill: SkillDef,
    alert: Mapping[str, str],
    *,
    policy: RegistryPolicy | None = None,
) -> FixResult:
    policy = policy or load_policy()
    action = skill.fix_action or {"kind": "watch_only"}
    kind = str(action.get("kind") or "watch_only").strip().lower()
    followup = skill.followup

    gluetun_block = _gluetun_container_action_forbidden(action)
    if gluetun_block:
        return FixResult(applied=False, detail=gluetun_block, homelab_followup=followup)

    if kind == "watch_only":
        return FixResult(
            applied=True,
            detail=f"cataloged alert class {skill.id}; awaiting fix_action in registry",
            homelab_followup=followup or "pm: add fix_action for this class in monitoring_skills.json",
        )

    if kind == "record_retired_target":
        target = str(action.get("target") or "").strip().lower()
        if not target:
            return FixResult(applied=False, detail="record_retired_target missing target", homelab_followup=followup)
        targets = _load_retired_probe_targets()
        changed = target not in targets
        if changed:
            targets.append(target)
            _save_retired_probe_targets(targets)
        detail = f"retired_probe_targets updated ({target})" if changed else f"{target} already retired in chip_state"
        if target == "gluetun":
            detail += "; live VPN is wireguard — gluetun stays retired"
        return FixResult(applied=True, detail=detail, homelab_followup=followup)

    if kind == "verify_source_contains":
        rel = str(action.get("path") or "").strip()
        needles = [str(n) for n in (action.get("needles") or []) if str(n).strip()]
        file = config.REPO_ROOT / rel
        if not file.is_file():
            return FixResult(applied=False, detail=f"missing source {rel}", homelab_followup=followup)
        text = file.read_text(encoding="utf-8", errors="replace")
        missing = [n for n in needles if n not in text]
        ok = not missing
        detail = f"verified {rel}" if ok else f"{rel} missing needles {missing}"
        return FixResult(applied=ok, detail=detail, homelab_followup=followup)

    if kind == "docker_restart_if_down":
        container = str(action.get("container") or "").strip().lower()
        running = _docker_running(container)
        if running is True:
            return FixResult(
                applied=True,
                detail=f"{container} already running after soak",
                homelab_followup=followup,
            )
        ok, note = _docker_restart(container, policy)
        return FixResult(
            applied=ok,
            detail=f"{container} restart after soak ({note})",
            homelab_followup=followup,
        )

    if kind == "homelab_delegate":
        message = str(action.get("message") or "sustained outage after soak").strip()
        if "plex-fix" in message.lower():
            message = message.replace("plex-fix", "incidents channel")
        return FixResult(
            applied=True,
            detail=f"homelab delegate queued: {message[:100]}",
            homelab_followup=followup or f"delegate:homelabdev {message}",
        )

    if kind == "manual_note":
        message = str(action.get("message") or "manual operator action required").strip()
        return FixResult(
            applied=True,
            detail=f"note only: {message[:160]}",
            homelab_followup=followup or "no automated fix — operator manual step only",
        )

    return FixResult(
        applied=False,
        detail=f"unknown fix_action kind {kind!r}",
        homelab_followup=followup,
    )


def post_target_allowed(*, webhook_env: str, channel_id: str, webhook_url: str) -> tuple[bool, str]:
    if str(channel_id) == PLEX_FIX_CHANNEL_ID:
        return False, "plex-fix channel is forbidden for monitoring receipts"
    env = (webhook_env or "").strip()
    if env in FORBIDDEN_WEBHOOK_ENVS:
        return False, f"webhook env {env} is forbidden"
    url = (webhook_url or "").lower()
    if any(sub in url for sub in FORBIDDEN_POST_SUBSTRINGS):
        return False, "plex-fix webhook URL is forbidden"
    return True, "ok"


def run_skill_for_alert(
    alert: Mapping[str, str],
    *,
    skill: SkillDef | None = None,
    create_if_missing: bool = True,
    now: float | None = None,
) -> SkillRun:
    from chip_relay.monitoring_outage import note_outage_sighting

    if should_ignore_alert(alert):
        return SkillRun(
            skill_id="",
            created=False,
            matched=False,
            receipt="",
            learning_line="",
            ignored=True,
        )

    policy = load_policy()
    chosen = skill or match_skill(alert)
    created = False
    if chosen is None and create_if_missing:
        chosen = create_overlay_skill(alert)
        created = True
    if chosen is None:
        receipt = (
            "claim: monitoring alert triage\n"
            "status: blocked\n"
            "evidence: tool:monitoring-skill.unmatched\n"
            "next: pm: register a skill match for this alert class"
        )
        return SkillRun(
            skill_id="",
            created=False,
            matched=False,
            receipt=receipt,
            learning_line="",
        )

    prior = load_skill_learnings(chosen.id)
    sighting = note_outage_sighting(chosen.id, alert, now=now, soak_seconds=policy.soak_seconds)

    if not sighting.fix_ready:
        mins = int(sighting.remaining_s // 60)
        secs = int(sighting.remaining_s % 60)
        learning = append_skill_learning(
            chosen.id,
            f"soak watch class={sighting.class_key} elapsed={int(sighting.elapsed_s)}s",
            tag="soak",
        )
        receipt = (
            f"claim: pm skill {chosen.id} — soak watch\n"
            "status: blocked\n"
            "evidence: tool:monitoring-outage.watch\n"
            f"next: first sighting recorded; fix gate in {mins}m{secs:02d}s if still down"
        )
        return SkillRun(
            skill_id=chosen.id,
            created=created,
            matched=True,
            receipt=receipt,
            learning_line=learning,
        )

    fix = execute_fix_action(chosen, alert, policy=policy)

    # Receipt-law (RAZ-217 / #52): a done receipt's evidence must be a BARE
    # tool: token — prior=/soak= suffixes broke the anchored regex and made
    # every applied-fix skill post `[chip-relay] ERROR: receipt schema:
    # done-without-evidence` (live receipts 2026-10-04, one per bot message).
    # Suffix metadata rides the learning row only (the learning line below
    # already records fix kind/applied/detail; soak history lives in the
    # outage sightings log, not in the receipt).
    evidence = f"tool:monitoring-skill.{chosen.id}"

    next_line = fix.homelab_followup or fix.detail
    if fix.applied:
        status = "done"
        next_line = f"{fix.detail}; {next_line}"[:190]
    else:
        status = "blocked"
        next_line = f"chip-chat fix incomplete: {fix.detail}"[:190]

    receipt = (
        f"claim: pm skill {chosen.id} — {chosen.title}\n"
        f"status: {status}\n"
        f"evidence: {evidence}\n"
        f"next: {next_line}"
    )
    violation = receipt_violates_vpn_law(receipt)
    if violation:
        receipt = (
            f"claim: pm skill {chosen.id} refused\n"
            "status: refused\n"
            "evidence: tool:monitoring-skill.policy\n"
            f"next: {violation}"
        )

    learning = append_skill_learning(
        chosen.id,
        f"fix kind={chosen.fix_action.get('kind')} applied={fix.applied} detail={fix.detail[:100]}",
        tag="run",
    )
    return SkillRun(
        skill_id=chosen.id,
        created=created,
        matched=True,
        receipt=receipt,
        learning_line=learning,
    )
