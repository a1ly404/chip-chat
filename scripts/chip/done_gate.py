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
