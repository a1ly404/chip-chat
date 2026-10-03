"""Reusable monitoring skills for #monitoring-logs (pm lane, read-only).

Skills are matched deterministically from alert text, executed without OpenRouter
spend, and learnings append under ``CHIP_CHAT_DATA_DIR`` for the next run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from chip import config
from chip_relay import alert_ingest

REGISTRY_FILE = config.REPO_ROOT / "config" / "chip_chat" / "monitoring_skills.json"

# Discord channel id for #plex-fix — receipts must never target this channel.
PLEX_FIX_CHANNEL_ID = "1553148704494714891"

FORBIDDEN_WEBHOOK_ENVS = frozenset({"CHIP_WEBHOOK_PLEX_FIX", "CHIP_WEBHOOK_PLEX"})
FORBIDDEN_POST_SUBSTRINGS = ("plex-fix", "/plex-fix")

VPN_MUTATION_PATTERNS = (
    "docker restart gluetun",
    "docker restart wireguard",
    "docker restart qbittorrent",
    "compose up",
    "recreate gluetun",
)


@dataclass(frozen=True)
class SkillDef:
    id: str
    title: str
    match_any: tuple[str, ...]
    body_path: str


@dataclass(frozen=True)
class SkillRun:
    skill_id: str
    created: bool
    matched: bool
    receipt: str
    learning_line: str


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_registry(path: Path | None = None) -> list[SkillDef]:
    file = path or REGISTRY_FILE
    raw = json.loads(file.read_text(encoding="utf-8"))
    out: list[SkillDef] = []
    for row in raw.get("skills") or []:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "").strip()
        if not sid:
            continue
        patterns = tuple(str(p) for p in (row.get("match_any") or []) if str(p).strip())
        out.append(
            SkillDef(
                id=sid,
                title=str(row.get("title") or sid),
                match_any=patterns,
                body_path=str(row.get("body_path") or ""),
            )
        )
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
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "").strip()
        if not sid:
            continue
        patterns = tuple(str(p) for p in (row.get("match_any") or []) if str(p).strip())
        out.append(
            SkillDef(
                id=sid,
                title=str(row.get("title") or sid),
                match_any=patterns or (sid,),
                body_path=str(row.get("body_path") or ""),
            )
        )
    return out


def all_skills() -> list[SkillDef]:
    merged: dict[str, SkillDef] = {s.id: s for s in load_registry()}
    for s in load_overlay_skills():
        merged.setdefault(s.id, s)
    return list(merged.values())


def parse_monitoring_message(text: str) -> dict[str, str]:
    """Best-effort structured alert from a Discord monitoring line."""
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
                return SkillDef(id=sid, title=skill_name, match_any=(ingest_key,), body_path="")
    return None


def create_overlay_skill(alert: Mapping[str, str]) -> SkillDef:
    base = str(alert.get("key") or alert.get("detail") or "monitoring-alert")[:48]
    sid = re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-") or "monitoring-alert"
    pattern = str(alert.get("key") or alert.get("detail") or sid)[:120]
    skill = SkillDef(id=sid, title=f"auto:{sid}", match_any=(pattern,), body_path="")
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


def _read_body(skill: SkillDef) -> str:
    if not skill.body_path:
        return ""
    p = config.REPO_ROOT / skill.body_path
    if p.is_file():
        return p.read_text(encoding="utf-8", errors="replace")
    return ""


def receipt_violates_vpn_law(text: str) -> str | None:
    low = text.lower()
    for pat in VPN_MUTATION_PATTERNS:
        if pat in low:
            return f"forbidden VPN/container mutation: {pat}"
    return None


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
) -> SkillRun:
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
    body = _read_body(chosen)
    law_note = ""
    if "gluetun" in chosen.id or "gluetun" in body.lower():
        law_note = (
            "gluetun 404/no-container = stale probe target on wireguard stack; "
            "never recreate gluetun"
        )
    if chosen.id == "qb-firewalled-not-success" or "firewalled" in f"{alert}".lower():
        law_note = (law_note + "; " if law_note else "") + "qBittorrent firewalled is not a successful probe"

    evidence = f"tool:monitoring-skill.{chosen.id}"
    if prior:
        evidence += f" prior={prior[-1][:80]}"

    receipt = (
        f"claim: pm skill {chosen.id} — {chosen.title}\n"
        "status: blocked\n"
        f"evidence: {evidence}\n"
        f"next: read-only triage; no VPN container restarts; detail={str(alert.get('detail', ''))[:100]}"
    )
    violation = receipt_violates_vpn_law(receipt)
    if violation:
        receipt = (
            f"claim: pm skill {chosen.id} refused\n"
            "status: refused\n"
            f"evidence: tool:monitoring-skill.policy\n"
            f"next: {violation}"
        )

    learning = append_skill_learning(
        chosen.id,
        f"matched alert key={alert.get('key', '')!r} class={chosen.id}",
        tag="match",
    )
    return SkillRun(
        skill_id=chosen.id,
        created=created,
        matched=True,
        receipt=receipt,
        learning_line=learning,
    )
