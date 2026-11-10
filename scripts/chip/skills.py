"""Reusable skills catalog (parity P1.3).

A skill is one markdown file with a small front-matter header::

    ---
    name: Checkout latency
    description: When checkout p95 latency alerts fire, verify health before restarting.
    triggers: [checkout, latency, p95]
    version: 1
    ---
    # Steps
    ...

Two sources are merged into one catalog:

* **pack** skills: the active system pack's ``skill_bodies_dir`` surface. Read-only.
* **user** skills: ``CHIP_CHAT_DATA_DIR/skills/<id>.md``, written by ``add_skill``.

The skill id is the file stem (lowercase slug). A user skill cannot shadow a pack skill with
the same id unless ``override=True`` is passed explicitly; without it the user file is skipped
with a reason. Invalid files are skipped with a reason instead of failing the whole catalog.

Pack skill bodies written before this module may lack front-matter; those are accepted with
``name`` taken from the first ``# heading`` and ``description`` from the first prose line.

``select(text)`` picks skills whose triggers (or name/description words) match the text, and
``injection_block(skills, budget_chars)`` renders selected bodies for a turn within a character
budget. Nothing here injects into turns by itself; callers gate that on ``CHIP_SKILLS=1``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from chip.config import data_dir

FLAG = "CHIP_SKILLS"
MAX_BODY_CHARS = 20_000
MAX_DESCRIPTION_CHARS = 300
DEFAULT_BUDGET_CHARS = 4000
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be by for from has have i in is it its of on or that the this to was we were when with you".split()
)


class SkillError(ValueError):
    """Raised for an invalid skill (bad id, missing fields, shadowing without override)."""


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    description: str
    body: str
    source: str  # "pack" or "user"
    path: str
    triggers: tuple[str, ...] = ()
    version: str = "1"

    def summary(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "triggers": list(self.triggers),
            "version": self.version,
            "source": self.source,
        }


@dataclass
class Catalog:
    skills: dict[str, Skill] = field(default_factory=dict)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def list(self) -> list[Skill]:
        return [self.skills[k] for k in sorted(self.skills)]

    def get(self, skill_id: str) -> Skill | None:
        return self.skills.get(skill_id)


def enabled(env: dict[str, str] | None = None) -> bool:
    src = os.environ if env is None else env
    return str(src.get(FLAG, "0")).strip().lower() in {"1", "true", "yes", "on"}


def user_dir() -> Path:
    return data_dir() / "skills"


def pack_dir() -> Path | None:
    try:
        from chip import system_pack

        return system_pack.optional_surface("skill_bodies_dir")
    except Exception:  # pragma: no cover - pack resolution is best-effort here
        return None


def _parse_list(raw: str) -> tuple[str, ...]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    items = [p.strip().strip("'\"").strip().lower() for p in raw.split(",")]
    return tuple(i for i in items if i)


def parse(text: str, *, skill_id: str, source: str, path: str = "", require_front_matter: bool = True) -> Skill:
    """Parse one skill file. Raises SkillError with a human-readable reason."""
    if not _ID.match(skill_id):
        raise SkillError(f"invalid skill id {skill_id!r} (lowercase letters, digits, dashes)")
    if len(text) > MAX_BODY_CHARS:
        raise SkillError(f"skill too large ({len(text)} > {MAX_BODY_CHARS} chars)")
    meta: dict[str, str] = {}
    body = text
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        try:
            end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        except StopIteration:
            raise SkillError("unterminated front-matter") from None
        for ln in lines[1:end]:
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            if ":" not in ln:
                raise SkillError(f"bad front-matter line: {ln.strip()[:60]!r}")
            k, v = ln.split(":", 1)
            meta[k.strip().lower()] = v.strip()
        body = "\n".join(lines[end + 1 :]).strip("\n")
    elif require_front_matter:
        raise SkillError("missing front-matter (--- name/description ---)")
    else:
        heading = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), "")
        prose = next((ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")), "")
        meta = {"name": heading, "description": prose}
    name = meta.get("name", "").strip().strip("'\"")
    desc = meta.get("description", "").strip().strip("'\"")
    if not name:
        raise SkillError("missing name")
    if not desc:
        raise SkillError("missing description")
    if len(desc) > MAX_DESCRIPTION_CHARS:
        raise SkillError(f"description too long ({len(desc)} > {MAX_DESCRIPTION_CHARS} chars)")
    if not body.strip():
        raise SkillError("empty body")
    return Skill(
        id=skill_id,
        name=name,
        description=desc,
        body=body,
        source=source,
        path=path,
        triggers=_parse_list(meta.get("triggers", "")),
        version=str(meta.get("version", "1")).strip() or "1",
    )


def _scan(directory: Path | None, source: str, *, require_front_matter: bool) -> Iterable[tuple[str, Path, Skill | str]]:
    if directory is None or not directory.is_dir():
        return
    for p in sorted(directory.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
            yield p.stem, p, parse(text, skill_id=p.stem, source=source, path=str(p), require_front_matter=require_front_matter)
        except (SkillError, OSError, UnicodeDecodeError) as exc:
            yield p.stem, p, str(exc)


def load_catalog(*, pack: Path | None | bool = True, user: Path | None = None) -> Catalog:
    """Merge pack (read-only) and user skills. ``pack=True`` resolves the active pack surface."""
    pdir = pack_dir() if pack is True else (pack or None)
    udir = user if user is not None else user_dir()
    cat = Catalog()
    for sid, path, res in _scan(pdir, "pack", require_front_matter=False):
        if isinstance(res, str):
            cat.skipped.append({"id": sid, "source": "pack", "path": str(path), "reason": res})
        else:
            cat.skills[sid] = res
    for sid, path, res in _scan(udir, "user", require_front_matter=True):
        if isinstance(res, str):
            cat.skipped.append({"id": sid, "source": "user", "path": str(path), "reason": res})
            continue
        existing = cat.skills.get(sid)
        if existing is not None and existing.source == "pack" and not _override_marked(path):
            cat.skipped.append(
                {"id": sid, "source": "user", "path": str(path), "reason": "shadows pack skill (add with override)"}
            )
            continue
        cat.skills[sid] = res
    return cat


_OVERRIDE_MARK = "override: true"


def _override_marked(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8").split("---", 2)
    except OSError:
        return False
    return len(head) >= 3 and any(ln.strip().lower() == _OVERRIDE_MARK for ln in head[1].splitlines())


def add_skill(
    skill_id: str,
    *,
    name: str,
    description: str,
    body: str,
    triggers: Iterable[str] = (),
    version: str = "1",
    override: bool = False,
    replace: bool = False,
    catalog: Catalog | None = None,
) -> Skill:
    """Write a user skill. Refuses to shadow a pack skill unless ``override``."""
    from chip.memory_store import looks_secret  # secret scan shared with memory

    for label, value in (("name", name), ("description", description), ("body", body)):
        if looks_secret(value):
            raise SkillError(f"{label} looks like it contains a secret; refusing to store")
    if "---" in name or "---" in description:
        raise SkillError("name and description may not contain front-matter delimiters")
    if "\n" in name or "\n" in description:
        raise SkillError("name and description must be single lines")
    trig = [t.strip().lower() for t in triggers if t.strip()]
    if any("," in t or "]" in t for t in trig):
        raise SkillError("triggers may not contain ',' or ']'")
    cat = catalog if catalog is not None else load_catalog()
    existing = cat.get(skill_id)
    if existing is not None and existing.source == "pack" and not override:
        raise SkillError(f"{skill_id!r} is a read-only pack skill; pass override to shadow it")
    target = user_dir() / f"{skill_id}.md"
    if target.exists() and not replace:
        raise SkillError(f"user skill {skill_id!r} already exists; pass replace to rewrite it")
    header = ["---", f"name: {name.strip()}", f"description: {description.strip()}"]
    if trig:
        header.append(f"triggers: [{', '.join(trig)}]")
    header.append(f"version: {version}")
    if override:
        header.append(_OVERRIDE_MARK)
    header.append("---")
    text = "\n".join(header) + "\n" + body.strip("\n") + "\n"
    skill = parse(text, skill_id=skill_id, source="user", path=str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    return skill


def delete_skill(skill_id: str) -> bool:
    """Delete a user skill. Pack skills are never touched."""
    if not _ID.match(skill_id):
        raise SkillError(f"invalid skill id {skill_id!r}")
    target = user_dir() / f"{skill_id}.md"
    if not target.is_file():
        return False
    target.unlink()
    return True


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


def search(catalog: Catalog, query: str, *, top_k: int = 5) -> list[Skill]:
    """Rank skills by word overlap with name, description and triggers."""
    q = _words(query)
    if not q:
        return []
    scored = []
    for s in catalog.list():
        hay = _words(" ".join((s.id.replace("-", " "), s.name, s.description, " ".join(s.triggers))))
        score = len(q & hay)
        if score:
            scored.append((-score, s.id, s))
    scored.sort()
    return [s for _, _, s in scored[: max(0, top_k)]]


def select(catalog: Catalog, text: str, *, top_k: int = 2) -> list[Skill]:
    """Pick skills for a turn: any trigger phrase found in the text wins; otherwise word search."""
    low = text.lower()
    hits = [s for s in catalog.list() if s.triggers and any(_trigger_in(t, low) for t in s.triggers)]
    if hits:
        hits.sort(key=lambda s: (-sum(_trigger_in(t, low) for t in s.triggers), s.id))
        return hits[: max(0, top_k)]
    return search(catalog, text, top_k=top_k)


def _trigger_in(trigger: str, low: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(trigger) + r"(?![a-z0-9])", low) is not None


def injection_block(skills: Iterable[Skill], *, budget_chars: int = DEFAULT_BUDGET_CHARS) -> str:
    """Render selected skill bodies for a turn, never exceeding ``budget_chars``.

    Whole skills are preferred; the first skill that does not fit is truncated with a marker,
    and later ones are dropped.
    """
    if budget_chars <= 0:
        return ""
    out: list[str] = []
    used = 0
    for s in skills:
        chunk = f"## Skill: {s.name} ({s.id})\n{s.body.strip()}\n"
        sep = 1 if out else 0
        if used + sep + len(chunk) <= budget_chars:
            out.append(chunk)
            used += sep + len(chunk)
            continue
        marker = "\n[truncated]\n"
        room = budget_chars - used - sep - len(marker)
        if room > 40:
            out.append(chunk[:room].rstrip() + marker)
        break
    block = "\n".join(out)
    assert len(block) <= budget_chars
    return block
