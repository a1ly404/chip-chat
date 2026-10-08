"""System packs: one ``system.json`` names every config surface.

v0 manifest: pack resolution order — ``CHIP_SYSTEM_PACK`` env, then ``systems/DEFAULT_PACK``
(private monorepo file, excluded from export), then built-in ``DEFAULT_PACK`` (``acme-example``).
``CHIP_PACK_DIR`` points at an absolute pack directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from chip.paths import REPO_ROOT

SCHEMA = "chip-system-pack/v0"
DEFAULT_PACK = "acme-example"
DEFAULT_PACK_FILE = Path("systems") / "DEFAULT_PACK"
DIR_SURFACES = frozenset({"channel_law_dir", "persona_dir", "skill_bodies_dir"})
PACK_DIR_ENV = "CHIP_PACK_DIR"
SYSTEM_PACK_ENV = "CHIP_SYSTEM_PACK"


def default_pack_from_file(*, root: Path | None = None) -> str | None:
    """Pack name from ``systems/DEFAULT_PACK`` (private monorepo only; excluded from export)."""
    base = root if root is not None else REPO_ROOT
    path = (base / DEFAULT_PACK_FILE).resolve()
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if name and not name.startswith("#"):
            return name
    return None


def pack_name(environ: dict[str, str] | None = None, *, root: Path | None = None) -> str:
    env = os.environ if environ is None else environ
    raw = env.get(SYSTEM_PACK_ENV, "").strip()
    if raw:
        return raw
    from_file = default_pack_from_file(root=root)
    if from_file:
        return from_file
    return DEFAULT_PACK


def _pack_dir_override() -> Path | None:
    raw = os.environ.get(PACK_DIR_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{PACK_DIR_ENV} must be an absolute path, got {raw!r}")
    return path.resolve()


def manifest_path(name: str | None = None, *, root: Path | None = None) -> Path:
    """Path to ``system.json`` for the active or named pack."""
    if root is not None:
        pack = name or pack_name()
        return (root / "systems" / pack / "system.json").resolve()
    override = _pack_dir_override()
    if override is not None:
        return override / "system.json"
    pack = name or pack_name()
    return (REPO_ROOT / "systems" / pack / "system.json").resolve()


def resolution_root(*, root: Path | None = None, pack: dict[str, Any] | None = None) -> Path:
    """Default base directory for the active pack (used by tests)."""
    if root is not None:
        return root.resolve()
    override = _pack_dir_override()
    if override is not None:
        return override
    if pack is not None:
        return _surface_base_for_rel("", pack, root=None)
    return (REPO_ROOT / "systems" / pack_name()).resolve()


def _surface_base_for_rel(rel: str, pack: dict[str, Any], *, root: Path | None) -> Path:
    if root is not None:
        return root.resolve()
    override = _pack_dir_override()
    if override is not None:
        return override
    raw = Path(str(rel))
    if raw.parts and raw.parts[0] in ("systems", "docs"):
        return REPO_ROOT.resolve()
    name = str(pack.get("name") or pack_name())
    return (REPO_ROOT / "systems" / name).resolve()


_pack_cache: dict[tuple[Any, ...], dict[str, Any]] = {}


def clear_caches() -> None:
    _pack_cache.clear()


def load_pack(name: str | None = None, *, root: Path | None = None) -> dict[str, Any]:
    cache_key = (
        name,
        str(root.resolve()) if root is not None else None,
        os.environ.get(PACK_DIR_ENV, ""),
        os.environ.get(SYSTEM_PACK_ENV, ""),
    )
    if cache_key in _pack_cache:
        return _pack_cache[cache_key]
    path = manifest_path(name, root=root)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: schema {data.get('schema')!r} != {SCHEMA}")
    expected_name = name or pack_name()
    if data.get("name") != expected_name:
        raise ValueError(f"{path}: name {data.get('name')!r} != {expected_name!r}")
    _pack_cache[cache_key] = data
    return data


def allowlist(key: str, default: frozenset[str], pack: dict[str, Any] | None = None) -> frozenset[str]:
    """Pack ``allowlists[key]`` intersected with the code default: a pack may narrow, never widen."""
    try:
        pack = load_pack() if pack is None else pack
    except (OSError, ValueError):
        return default
    declared = pack.get("allowlists", {}).get(key)
    if declared is None:
        return default
    return default & frozenset(declared)


def _resolve_surface_path(
    key: str,
    pack: dict[str, Any],
    *,
    root: Path | None = None,
    must_exist: bool = True,
) -> Path:
    surfaces = pack.get("surfaces")
    if not isinstance(surfaces, dict) or key not in surfaces:
        raise KeyError(f"surface {key!r} not declared in pack")
    rel = surfaces[key]
    raw = Path(str(rel))
    base = _surface_base_for_rel(str(rel), pack, root=root)
    if raw.is_absolute():
        resolved = raw.resolve()
    else:
        resolved = (base / raw).resolve()
    allowed_roots = {base.resolve(), REPO_ROOT.resolve()}
    if not any(resolved == allowed or allowed in resolved.parents for allowed in allowed_roots):
        raise ValueError(f"surface {key} escapes allowed root: {rel}")
    if must_exist:
        if key in DIR_SURFACES:
            if not resolved.is_dir():
                raise FileNotFoundError(f"surface {key}: {rel}")
        elif not resolved.is_file():
            raise FileNotFoundError(f"surface {key}: {rel}")
    return resolved


def surface(key: str, pack: dict[str, Any], *, root: Path | None = None) -> Path:
    return _resolve_surface_path(key, pack, root=root, must_exist=True)


def optional_surface(key: str, pack: dict[str, Any] | None = None) -> Path | None:
    """Resolve a surface when present and on disk; otherwise None (no KeyError)."""
    try:
        data = load_pack() if pack is None else pack
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    surfaces = data.get("surfaces")
    if not isinstance(surfaces, dict) or key not in surfaces:
        return None
    try:
        path = _resolve_surface_path(key, data, must_exist=False)
    except ValueError:
        return None
    if key in DIR_SURFACES:
        return path if path.is_dir() else None
    return path if path.is_file() else None


def _pack_for_env(environ: dict[str, str] | None) -> dict[str, Any] | None:
    try:
        return load_pack(pack_name(environ))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def env_aliases(pack: dict[str, Any] | None = None) -> dict[str, list[str]]:
    """Map canonical env var -> legacy alias names (active pack only)."""
    data = pack if pack is not None else load_pack()
    raw = data.get("env_aliases")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for canonical, aliases in raw.items():
        if isinstance(aliases, list):
            out[str(canonical)] = [str(a) for a in aliases if str(a).strip()]
        elif isinstance(aliases, str) and aliases.strip():
            out[str(canonical)] = [aliases.strip()]
    return out


def env_get(name: str, environ: dict[str, str] | None = None, default: str = "") -> str:
    """Read env var, then pack-declared legacy aliases for the canonical name."""
    env = os.environ if environ is None else environ
    val = (env.get(name) or "").strip()
    if val:
        return val
    pack = _pack_for_env(env)
    if pack is None:
        return default
    for legacy in env_aliases(pack).get(name, []):
        val = (env.get(legacy) or "").strip()
        if val:
            return val
    return default


def cli_aliases(pack: dict[str, Any] | None = None) -> dict[str, str]:
    """Map legacy argparse dest/flag names to canonical names (active pack)."""
    data = pack if pack is not None else load_pack()
    raw = data.get("cli_aliases")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if str(k).strip() and str(v).strip()}


def operator_discord_id(environ: dict[str, str] | None = None) -> str:
    pack = _pack_for_env(os.environ if environ is None else environ)
    if pack is None:
        return ""
    ident = pack.get("identity") if isinstance(pack.get("identity"), dict) else {}
    return str(ident.get("operator_discord_id") or "").strip()


def pack_identity_string(key: str, environ: dict[str, str] | None = None, default: str = "") -> str:
    pack = _pack_for_env(os.environ if environ is None else environ)
    if pack is None:
        return default
    ident = pack.get("identity") if isinstance(pack.get("identity"), dict) else {}
    val = ident.get(key)
    return str(val).strip() if val is not None and str(val).strip() else default


def solver_forbidden_targets(environ: dict[str, str] | None = None) -> tuple[str, ...]:
    pack = _pack_for_env(os.environ if environ is None else environ)
    if pack is None:
        return ()
    rows = pack.get("solver_forbidden_targets")
    if isinstance(rows, list):
        return tuple(str(x) for x in rows if str(x).strip())
    return ()


def blocked_target_patterns(environ: dict[str, str] | None = None) -> tuple[str, ...]:
    pack = _pack_for_env(os.environ if environ is None else environ)
    if pack is None:
        return ()
    rows = pack.get("blocked_target_patterns")
    if isinstance(rows, list):
        return tuple(str(x) for x in rows if str(x).strip())
    return ()


def solver_kit_personas(environ: dict[str, str] | None = None) -> tuple[str, ...]:
    pack = _pack_for_env(os.environ if environ is None else environ)
    if pack is None:
        return ("pm", "chipchatdev")
    ident = pack.get("identity") if isinstance(pack.get("identity"), dict) else {}
    rows = ident.get("solver_kit_personas")
    if isinstance(rows, list):
        return tuple(str(x).strip() for x in rows if str(x).strip())
    return ("pm", "chipchatdev")


def reserved_worker_ids(environ: dict[str, str] | None = None) -> frozenset[str]:
    base = frozenset({"user", "inject", "relay", "operator"})
    pack = _pack_for_env(os.environ if environ is None else environ)
    if pack is None:
        return base
    ident = pack.get("identity") if isinstance(pack.get("identity"), dict) else {}
    extra = ident.get("reserved_worker_ids")
    if isinstance(extra, list):
        return base | frozenset(str(x).strip().lower() for x in extra if str(x).strip())
    return base
