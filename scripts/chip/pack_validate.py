"""Pass/fail validation for chip-system-pack/v0 manifests.

``chip pack validate`` loads a pack (by name, ``CHIP_PACK_DIR``, or ``--pack-dir``),
checks schema/name/surface resolution/allowlist shapes, and exits non-zero on errors.
Does not invent engine APIs; packs may declare a subset of known surfaces.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from chip.paths import REPO_ROOT
from chip.system_pack import (
    DIR_SURFACES,
    PACK_DIR_ENV,
    SCHEMA,
    pack_name,
)

# Surfaces seen in acme-example / engine callers. Unknown keys warn; declared paths must exist.
KNOWN_SURFACES = frozenset(
    {
        "personas",
        "models",
        "rooms",
        "channel_law_dir",
        "standing_law",
        "persona_dir",
        "tools",
        "lanes",
        "topology",
        "case_intake",
        "case_intake_escalation",
        "case_taxonomy",
        "monitoring_skills",
        "runbooks",
        "never_auto_done",
        "counter_registry",
        "skill_bodies_dir",
        "alert_fixtures",
    }
)

# Tokens that must not appear in public engine fixture packs (homelab no-leak).
PUBLIC_NO_LEAK_TOKENS = frozenset(
    {
        "homelab",
        "razzorstack",
        "razzor",
        "gluetun",
        "qbittorrent",
        "wireguard",
        "prowlarr",
        "flaresolverr",
    }
)


@dataclass
class Issue:
    level: str  # "error" | "warning"
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class Result:
    ok: bool
    path: str
    name: str
    issues: list[Issue] = field(default_factory=list)
    surfaces_ok: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "path": self.path,
            "name": self.name,
            "surfaces_ok": self.surfaces_ok,
            "issues": [i.to_dict() for i in self.issues],
            "errors": sum(1 for i in self.issues if i.level == "error"),
            "warnings": sum(1 for i in self.issues if i.level == "warning"),
        }


def _err(issues: list[Issue], code: str, message: str) -> None:
    issues.append(Issue("error", code, message))


def _warn(issues: list[Issue], code: str, message: str) -> None:
    issues.append(Issue("warning", code, message))


def _load_json(path: Path, issues: list[Issue]) -> dict[str, Any] | None:
    if not path.is_file():
        _err(issues, "missing_manifest", f"system.json not found: {path}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _err(issues, "invalid_json", f"{path}: {exc}")
        return None
    if not isinstance(data, dict):
        _err(issues, "invalid_json", f"{path}: root must be a JSON object")
        return None
    return data


def _check_string_list(issues: list[Issue], label: str, value: Any) -> None:
    if not isinstance(value, list):
        _err(issues, "bad_list", f"{label}: expected list of strings")
        return
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            _err(issues, "bad_list_item", f"{label}[{i}]: expected non-empty string")


def _check_allowlists(issues: list[Issue], pack: dict[str, Any]) -> None:
    raw = pack.get("allowlists")
    if raw is None:
        return
    if not isinstance(raw, dict):
        _err(issues, "bad_allowlists", "allowlists must be an object")
        return
    for key, declared in raw.items():
        _check_string_list(issues, f"allowlists.{key}", declared)


def _check_aliases(issues: list[Issue], pack: dict[str, Any]) -> None:
    env = pack.get("env_aliases")
    if env is not None:
        if not isinstance(env, dict):
            _err(issues, "bad_env_aliases", "env_aliases must be an object")
        else:
            for canonical, aliases in env.items():
                if not str(canonical).strip():
                    _err(issues, "bad_env_aliases", "env_aliases key must be non-empty")
                    continue
                if isinstance(aliases, str):
                    if not aliases.strip():
                        _err(issues, "bad_env_aliases", f"env_aliases.{canonical}: empty string")
                elif isinstance(aliases, list):
                    _check_string_list(issues, f"env_aliases.{canonical}", aliases)
                else:
                    _err(issues, "bad_env_aliases", f"env_aliases.{canonical}: expected string or list")

    cli = pack.get("cli_aliases")
    if cli is not None:
        if not isinstance(cli, dict):
            _err(issues, "bad_cli_aliases", "cli_aliases must be an object")
        else:
            for legacy, canonical in cli.items():
                if not str(legacy).strip() or not isinstance(canonical, str) or not canonical.strip():
                    _err(issues, "bad_cli_aliases", f"cli_aliases.{legacy!r}: expected non-empty string→string")


def _check_identity_values(issues: list[Issue], pack: dict[str, Any]) -> None:
    for key in ("identity", "values"):
        raw = pack.get(key)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            _err(issues, f"bad_{key}", f"{key} must be an object")


def _resolve_surface(
    key: str,
    rel: Any,
    pack: dict[str, Any],
    *,
    pack_dir: Path,
    repo_root: Path,
    issues: list[Issue],
) -> Path | None:
    if not isinstance(rel, str) or not rel.strip():
        _err(issues, "bad_surface", f"surfaces.{key}: expected non-empty relative path string")
        return None
    raw = Path(rel)
    # Prefer pack_dir as base for fixture / CHIP_PACK_DIR packs.
    base = pack_dir.resolve()
    if raw.parts and raw.parts[0] in ("systems", "docs"):
        base = repo_root.resolve()
    if raw.is_absolute():
        resolved = raw.resolve()
    else:
        resolved = (base / raw).resolve()
    allowed = {pack_dir.resolve(), repo_root.resolve()}
    if not any(resolved == root or root in resolved.parents for root in allowed):
        _err(issues, "surface_escape", f"surfaces.{key}: path escapes pack/repo root: {rel}")
        return None
    if key in DIR_SURFACES:
        if not resolved.is_dir():
            _err(issues, "missing_surface", f"surfaces.{key}: directory not found: {rel}")
            return None
    else:
        if not resolved.is_file():
            _err(issues, "missing_surface", f"surfaces.{key}: file not found: {rel}")
            return None
    return resolved


def _check_no_leak(issues: list[Issue], text: str, *, where: str) -> None:
    lower = text.lower()
    for token in sorted(PUBLIC_NO_LEAK_TOKENS):
        if token in lower:
            _err(issues, "no_leak", f"{where}: public pack must not contain {token!r}")


def validate_pack_data(
    pack: dict[str, Any],
    *,
    manifest_path: Path,
    pack_dir: Path,
    repo_root: Path | None = None,
    expected_name: str | None = None,
    check_no_leak: bool = False,
) -> Result:
    """Validate an already-parsed pack dict. Returns Result (ok iff no errors)."""
    issues: list[Issue] = []
    root = (repo_root or REPO_ROOT).resolve()
    pack_dir = pack_dir.resolve()
    name = str(pack.get("name") or "").strip()

    if pack.get("schema") != SCHEMA:
        _err(issues, "bad_schema", f"schema {pack.get('schema')!r} != {SCHEMA}")

    if not name:
        _err(issues, "bad_name", "name must be a non-empty string")
    else:
        want = expected_name
        if want is None and pack_dir.name and pack_dir.name not in (".", ""):
            want = pack_dir.name
        if want and name != want:
            _err(issues, "name_mismatch", f"name {name!r} != expected {want!r}")

    surfaces = pack.get("surfaces")
    surfaces_ok: list[str] = []
    if not isinstance(surfaces, dict) or not surfaces:
        _err(issues, "bad_surfaces", "surfaces must be a non-empty object")
    else:
        for key, rel in surfaces.items():
            if key not in KNOWN_SURFACES:
                _warn(issues, "unknown_surface", f"surfaces.{key}: not in engine known set (still resolved)")
            path = _resolve_surface(key, rel, pack, pack_dir=pack_dir, repo_root=root, issues=issues)
            if path is not None:
                surfaces_ok.append(key)

    _check_allowlists(issues, pack)
    _check_aliases(issues, pack)
    _check_identity_values(issues, pack)

    skn = pack.get("secrets_key_names")
    if skn is not None:
        _check_string_list(issues, "secrets_key_names", skn)

    for key in ("solver_forbidden_targets", "blocked_target_patterns"):
        if key in pack:
            _check_string_list(issues, key, pack.get(key))

    if check_no_leak:
        _check_no_leak(issues, json.dumps(pack, ensure_ascii=False), where=str(manifest_path))
        for key in surfaces_ok:
            rel = surfaces[key]
            resolved = _resolve_surface(key, rel, pack, pack_dir=pack_dir, repo_root=root, issues=[])
            if resolved is None:
                continue
            if resolved.is_file():
                try:
                    _check_no_leak(issues, resolved.read_text(encoding="utf-8"), where=f"surface {key}")
                except OSError as exc:
                    _err(issues, "read_surface", f"surfaces.{key}: {exc}")

    errors = [i for i in issues if i.level == "error"]
    return Result(
        ok=not errors,
        path=str(manifest_path),
        name=name or "(unnamed)",
        issues=issues,
        surfaces_ok=sorted(surfaces_ok),
    )


def resolve_manifest(
    *,
    pack: str | None = None,
    pack_dir: str | Path | None = None,
    environ: dict[str, str] | None = None,
    root: Path | None = None,
) -> tuple[Path, Path, str | None]:
    """Return (manifest_path, pack_directory, expected_name)."""
    env = os.environ if environ is None else environ
    base = root if root is not None else REPO_ROOT

    if pack_dir is not None:
        directory = Path(pack_dir).expanduser().resolve()
        return directory / "system.json", directory, directory.name

    override = (env.get(PACK_DIR_ENV) or "").strip()
    if override and pack is None:
        directory = Path(override).expanduser()
        if not directory.is_absolute():
            raise ValueError(f"{PACK_DIR_ENV} must be an absolute path, got {override!r}")
        directory = directory.resolve()
        return directory / "system.json", directory, directory.name

    name = pack or pack_name(env, root=base)
    directory = (base / "systems" / name).resolve()
    return directory / "system.json", directory, name


def validate_pack(
    *,
    pack: str | None = None,
    pack_dir: str | Path | None = None,
    environ: dict[str, str] | None = None,
    root: Path | None = None,
    check_no_leak: bool | None = None,
) -> Result:
    """Validate a pack by name, ``--pack-dir``, or env (``CHIP_PACK_DIR`` / ``CHIP_SYSTEM_PACK``)."""
    issues: list[Issue] = []
    try:
        manifest, directory, expected = resolve_manifest(
            pack=pack, pack_dir=pack_dir, environ=environ, root=root
        )
    except ValueError as exc:
        _err(issues, "bad_pack_dir", str(exc))
        return Result(ok=False, path="", name=pack or "", issues=issues)

    data = _load_json(manifest, issues)
    if data is None:
        return Result(ok=False, path=str(manifest), name=pack or directory.name, issues=issues)

    # Public packs under systems/ or scripts/fixtures/packs/ get no-leak by default.
    if check_no_leak is None:
        parts = {p.lower() for p in directory.parts}
        check_no_leak = "fixtures" in parts or directory.parent.name == "systems"

    return validate_pack_data(
        data,
        manifest_path=manifest,
        pack_dir=directory,
        repo_root=root,
        expected_name=expected,
        check_no_leak=bool(check_no_leak),
    )


def format_result(result: Result, *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    lines = [
        f"pack validate: {'OK' if result.ok else 'FAIL'} name={result.name} path={result.path}",
        f"  surfaces_ok={len(result.surfaces_ok)} errors="
        f"{sum(1 for i in result.issues if i.level == 'error')} "
        f"warnings={sum(1 for i in result.issues if i.level == 'warning')}",
    ]
    for issue in result.issues:
        lines.append(f"  [{issue.level}] {issue.code}: {issue.message}")
    if result.ok and not result.issues:
        lines.append("  (no issues)")
    return "\n".join(lines)

