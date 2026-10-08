"""Paths, personas, and model defaults for Chip Chat."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from chip import system_pack
from chip.paths import REPO_ROOT

PERSONAS_FILE = system_pack.surface("personas", system_pack.load_pack())
MODELS_FILE = system_pack.surface("models", system_pack.load_pack())
DOTENV_FILE = REPO_ROOT / ".env"

DEFAULT_MODEL = "z-ai/glm-5.3-flash"
# Room turns (large system+history): GLM 5.3 can spend completion budget on reasoning first.
ROOM_COMPLETION_MAX_TOKENS = 1024
ESCALATE_MODEL = "deepseek/deepseek-v4.1-flash"
PING_MODEL = DEFAULT_MODEL
BLOCKED_MODELS = frozenset({"deepseek/deepseek-chat"})
FUTURE_ROUTER_MODEL = "typesafe/jev-1.13"

DEFAULT_PERSONAS: dict[str, dict[str, str]] = {
    "default": {
        "display_name": "Chip",
        "system": "You are Chip, a concise helpful assistant in the Chip Chat CLI.",
    },
    "planner": {
        "display_name": "Planner",
        "system": "You are Planner: break problems into short numbered steps. Be brief.",
    },
    "builder": {
        "display_name": "Builder",
        "system": "You are Builder: implement or refine one step at a time. Be brief and practical.",
    },
}

_dotenv_loaded = False


def load_dotenv() -> None:
    """Load KEY=VALUE lines from repo `.env` into os.environ (does not override existing)."""
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    if not DOTENV_FILE.is_file():
        return
    for line in DOTENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _read_config_json() -> dict[str, Any]:
    """Personas file first; model file overrides model keys when present."""
    merged = _load_json(PERSONAS_FILE)
    models = _load_json(MODELS_FILE)
    for key in ("default_model", "escalate_model", "blocked_models", "future_router_model"):
        if key in models:
            merged[key] = models[key]
    if not merged and not models:
        return {}
    return merged


def data_dir() -> Path:
    load_dotenv()
    override = os.environ.get("CHIP_CHAT_DATA_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        home = os.environ.get("HOME", "").strip()
        if home:
            from chip import pack_values

            rel = pack_values.default_data_dir_relative()
            path = Path(home) / rel
        else:
            path = REPO_ROOT / "var" / "chip-chat"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_personas() -> dict[str, dict[str, str]]:
    raw = _read_config_json()
    personas = raw.get("personas")
    if isinstance(personas, dict):
        return {str(k): dict(v) for k, v in personas.items() if isinstance(v, dict)}
    return dict(DEFAULT_PERSONAS)


def default_model() -> str:
    model = (_read_config_json().get("default_model") or "").strip()
    return model or DEFAULT_MODEL


def model_for_persona(name: str | None) -> str:
    """Persona-level model override (personas file 'model' key), else default.

    Operator routing law (2026-10-05): PM = z-ai/glm-5.3-flash (low reasoning is
    auto-applied by the z-ai/glm- prefix), dev bots = cheapest qwen
    (qwen/qwen3.7-flash). Blocked personas models fail back to default.
    """
    if name:
        entry = load_personas().get(name)
        if isinstance(entry, dict):
            model = str(entry.get("model", "")).strip()
            if model and model not in blocked_models():
                return model
    return default_model()


def escalate_model() -> str:
    model = (_read_config_json().get("escalate_model") or "").strip()
    return model or ESCALATE_MODEL


def blocked_models() -> frozenset[str]:
    raw = _read_config_json().get("blocked_models")
    if isinstance(raw, list):
        return frozenset(str(m).strip() for m in raw if str(m).strip())
    return BLOCKED_MODELS


def resolve_model(slug: str | None, *, escalate: bool = False) -> str:
    if escalate:
        chosen = escalate_model()
    elif slug:
        chosen = slug.strip()
    else:
        chosen = default_model()
    if chosen in blocked_models():
        raise ValueError(
            f"Model {chosen!r} is blocked for Chip Chat (OpenRouter allow-list). "
            f"Use default {default_model()!r} or escalate {escalate_model()!r}."
        )
    return chosen


def mock_mode() -> bool:
    load_dotenv()
    return os.environ.get("CHIP_CHAT_MOCK", "").strip().lower() in ("1", "true", "yes")


def memory_top_k(override: int | None = None) -> int:
    if override is not None:
        return max(0, int(override))
    load_dotenv()
    raw = os.environ.get("CHIP_CHAT_MEMORY_TOP_K", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return 5


def memory_token_budget(override: int | None = None) -> int:
    if override is not None:
        return max(0, int(override))
    load_dotenv()
    raw = os.environ.get("CHIP_CHAT_MEMORY_TOKEN_BUDGET", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return 500


def room_history_limit(override: int | None = None) -> int:
    """Max prior user/assistant transcript lines to load into room context."""
    if override is not None:
        return max(0, int(override))
    load_dotenv()
    raw = os.environ.get("CHIP_CHAT_ROOM_HISTORY_LIMIT", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    cfg = _read_config_json().get("room_history_limit")
    if isinstance(cfg, int):
        return max(0, cfg)
    return 10


load_dotenv()
