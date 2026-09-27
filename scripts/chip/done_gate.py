"""Done-discipline gate (GOLDEN R1 / R13; RAZ-182 + RAZ-217).

Machine-checkable acceptance discipling for program tickets: a checkbox claimed
Done must carry a ``verified-as-of:`` binding in its section. ``claim done`` is
NOT Linear Done; and Done without receipts is premature-Done (reopen).

Offline + mock-first: parses Linear issue markdown (checkboxes + evidence
footers). No network. Used by every agent before touching Done state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_VERIFIED_RE = re.compile(r"verified-as-of:\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", re.IGNORECASE)
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s*(.+?)\s*$", re.MULTILINE)
_SECTION_SPLIT_RE = re.compile(r"^(#{2,})\s+(.*)$", re.MULTILINE)


@dataclass
class BoxState:
    text: str
    checked: bool
    section: str
    verified: bool = False
    verified_stamp: str | None = None


@dataclass
class DoneScan:
    boxes: list[BoxState] = field(default_factory=list)
    premature_done: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "boxes": [
                {
                    "text": b.text[:80],
                    "checked": b.checked,
                    "section": b.section[:60],
                    "verified": b.verified,
                    "verified_stamp": b.verified_stamp,
                }
                for b in self.boxes
            ],
            "premature_done": self.premature_done,
            "reasons": self.reasons,
        }


def parse_done_state(section_text: str, *, declare_done: bool = True) -> DoneScan:
    """One acceptance section → DoneScan.

    ``declare_done`` = the ticket/section claims Done status. Every checked box
    must carry a ``verified-as-of: <stamp>`` bound in the SAME section;
    unchecked boxes while declaring Done are premature too.
    """
    scan = DoneScan()
    lines = section_text
    sections: list[tuple[str, str]] = []
    matches = list(_SECTION_SPLIT_RE.finditer(lines))
    if not matches:
        sections.append(("root", lines))
    else:
        sections.append(("root", lines[: matches[0].start()]))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(lines)
            sections.append((m.group(2).strip(), lines[m.start():end]))

    for section_name, body in sections:
        for m in _CHECKBOX_RE.finditer(body):
            checked = m.group(1).lower() == "x"
            box_text = m.group(2).strip()
            # a verified-as-of line bound AFTER the checkbox (same section)
            tail = body[m.end(): m.end() + 600]
            vmatch = _VERIFIED_RE.search(tail)
            state = BoxState(
                text=box_text,
                checked=checked,
                section=section_name,
                verified=bool(vmatch),
                verified_stamp=vmatch.group(0) if vmatch else None,
            )
            scan.boxes.append(state)
            if declare_done and checked and not state.verified:
                scan.premature_done = True
                scan.reasons.append(
                    f"checked without verified-as-of: {box_text[:60]!r} (section {section_name!r})"
                )
    if declare_done and not scan.boxes:
        scan.premature_done = True
        scan.reasons.append("Done declared with zero acceptance boxes (vague AC)")
    return scan


def may_flip_done(issue_markdown: str) -> tuple[bool, str]:
    """Gate: allow Done only when every checked box has a verified-as-of receipt."""
    scan = parse_done_state(issue_markdown)
    if scan.premature_done:
        return False, "premature-Done: " + "; ".join(scan.reasons[:3])
    return True, "all boxes verified-as-of (or none checked)"


def reopen_required(issue_markdown: str) -> tuple[bool, str]:
    """Watchdog: same predicate as the gate, phrased for reopens (RAZ-217)."""
    ok, reason = may_flip_done(issue_markdown)
    return (not ok), reason


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Board sweep (RAZ-199 class) — severity-ranked findings from an issues export
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"reopen-required": 0, "flipping-risk": 1, "vague-done": 2}


def sweep_one(issue: dict[str, Any]) -> dict[str, Any] | None:
    """One Linear issue export → finding dict, or None when clean/not-a-Done-claim."""
    ident = str(issue.get("id") or "")
    status = str(issue.get("status") or "")
    statusType = str(issue.get("statusType") or "")
    desc = str(issue.get("description") or "")
    is_done_claim = statusType == "completed" or status.lower() in {"done", "closed", "complete"}

    scan = parse_done_state(desc, declare_done=is_done_claim)
    checked = [b for b in scan.boxes if b.checked]
    stamps = sum(1 for b in checked if b.verified)

    if is_done_claim and not scan.boxes:
        return {
            "id": ident, "status": status, "severity": "vague-done",
            "checked": 0, "stamps": 0,
            "reason": "Done with zero acceptance boxes (vague AC)",
        }
    if is_done_claim and checked and stamps < len(checked):
        return {
            "id": ident, "status": status, "severity": "reopen-required",
            "checked": len(checked), "stamps": stamps,
            "reason": f"{len(checked) - stamps} checked box(es) without verified-as-of receipts",
        }
    if not is_done_claim and checked and stamps < len(checked):
        return {
            "id": ident, "status": status, "severity": "flipping-risk",
            "checked": len(checked), "stamps": stamps,
            "reason": "boxes checked without receipts — will refuse at Done",
        }
    return None


def sweep(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Whole-export scan → findings ranked reopen-required > flipping-risk > vague-done."""
    findings = [f for f in (sweep_one(i) for i in issues) if f is not None]
    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f["severity"], 9), f["id"]))
    return findings
