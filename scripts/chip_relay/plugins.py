"""Relay plugin registry — pack plugins register hooks without engine imports."""

from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

AlertHandler = Callable[[Mapping[str, Any], dict[str, str] | None], dict[str, Any] | None]
FeedCollectPack = Callable[[dict[str, str]], list[dict[str, object]]]
FeedPostableVerdictHook = Callable[[Mapping[str, object], dict[str, str] | None], dict[str, Any] | None]
FixActionHandler = Callable[..., Any]  # returns FixResult | None from monitoring_skills
SecretsApply = Callable[[], int | None]
SecretsMissingBoot = Callable[[dict[str, str] | None], list[str]]


@dataclass
class PluginRegistry:
    """Mutable hook table populated by ``register()`` in plugin modules."""

    feed_collect_pack: FeedCollectPack | None = None
    feed_is_pack_feed_alert: Callable[[Mapping[str, object]], bool] | None = None
    feed_display_sentence: Callable[[str], str] | None = None
    feed_on_postable_verdict: FeedPostableVerdictHook | None = None
    feed_default_host: str = ""
    feed_default_targets: dict[str, int] = field(default_factory=dict)
    alert_handlers: list[AlertHandler] = field(default_factory=list)
    alert_route_table: dict[str, tuple[str, str]] = field(default_factory=dict)
    secrets_missing_boot: SecretsMissingBoot | None = None
    secrets_apply: SecretsApply | None = None
    hard_forbidden_restart_containers: frozenset[str] = frozenset()
    network_guard_mutation_patterns: tuple[str, ...] = ()
    forbidden_webhook_envs: frozenset[str] = frozenset()
    forbidden_post_substrings: tuple[str, ...] = ()
    forbidden_post_channel_id: str = ""
    network_guard_restart_substrings: tuple[str, ...] = ()
    fix_action_handlers: dict[str, FixActionHandler] = field(default_factory=dict)
    forbidden_container_action: Callable[[dict[str, Any]], str | None] | None = None
    record_retired_target_detail_suffix: Callable[[str, str], str] | None = None
    standup_secret_names: tuple[str, ...] = ()
    standup_fetch_keys: Callable[[tuple[str, ...], dict[str, str]], dict[str, str]] | None = None
    feed_health_detail: Callable[[str, dict[str, object]], str | None] | None = None
    feed_post_no_receipt_suffix: str = " — dark: log-only, no automated recovery without operator ack"
    monitoring_webhook_id_prefixes: tuple[str, ...] = ()
    monitoring_embed_title_pattern: str = ""
    monitoring_adapter_extra: Callable[[str, str, str], Any] | None = None

    def clear(self) -> None:
        self.feed_collect_pack = None
        self.feed_is_pack_feed_alert = None
        self.feed_display_sentence = None
        self.feed_on_postable_verdict = None
        self.feed_default_host = ""
        self.feed_default_targets.clear()
        self.alert_handlers.clear()
        self.alert_route_table.clear()
        self.secrets_missing_boot = None
        self.secrets_apply = None
        self.hard_forbidden_restart_containers = frozenset()
        self.network_guard_mutation_patterns = ()
        self.forbidden_webhook_envs = frozenset()
        self.forbidden_post_substrings = ()
        self.forbidden_post_channel_id = ""
        self.network_guard_restart_substrings = ()
        self.fix_action_handlers.clear()
        self.forbidden_container_action = None
        self.record_retired_target_detail_suffix = None
        self.standup_secret_names = ()
        self.standup_fetch_keys = None
        self.feed_health_detail = None
        self.feed_post_no_receipt_suffix = " — dark: log-only, no automated recovery without operator ack"
        self.monitoring_webhook_id_prefixes = ()
        self.monitoring_embed_title_pattern = ""
        self.monitoring_adapter_extra = None


registry = PluginRegistry()
_loaded = False
_load_disabled = False
_loaded_env_key: tuple[str, str, str] | None = None
_legacy_compat_installed = False


def _parse_plugin_list(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _env_key(environ: dict[str, str]) -> tuple[str, str, str]:
    return (
        environ.get("CHIP_PLUGINS", "__unset__"),
        environ.get("CHIP_SYSTEM_PACK", ""),
        environ.get("CHIP_PACK_DIR", ""),
    )


def plugin_module_names(environ: dict[str, str] | None = None) -> list[str]:
    """Resolve plugin module paths from ``CHIP_PLUGINS`` or pack ``plugins`` (lazy read)."""
    env = os.environ if environ is None else environ
    if "CHIP_PLUGINS" in env:
        return _parse_plugin_list(env.get("CHIP_PLUGINS", ""))
    try:
        from chip import system_pack

        pack = system_pack.load_pack(system_pack.pack_name(env))
        rows = pack.get("plugins")
        if isinstance(rows, list):
            return [str(x).strip() for x in rows if str(x).strip()]
    except Exception:
        return []
    return []


def ensure_loaded(environ: dict[str, str] | None = None) -> None:
    global _loaded, _loaded_env_key
    env = dict(os.environ if environ is None else environ)
    key = _env_key(env)
    if _load_disabled:
        return
    if _loaded and _loaded_env_key == key:
        return
    if _loaded:
        registry.clear()
    for name in plugin_module_names(env):
        mod = importlib.import_module(name)
        register_fn = getattr(mod, "register", None)
        if callable(register_fn):
            register_fn(registry)
    _loaded = True
    _loaded_env_key = key


def install_legacy_compat_if_ready() -> None:
    """Pack-only API aliases; safe after relay modules finish importing."""
    global _legacy_compat_installed
    if _legacy_compat_installed or _load_disabled:
        return
    import sys

    needed = ("chip_relay.routing", "chip_relay.inject", "chip_relay.case_intake", "chip_relay.monitoring_skills")
    if any(name not in sys.modules for name in needed):
        return
    for name in plugin_module_names():
        mod = importlib.import_module(name)
        fn = getattr(mod, "install_legacy_engine_compat", None)
        if callable(fn):
            fn()
            _legacy_compat_installed = True
            return


def reset_for_tests() -> None:
    """Clear registry and allow ``ensure_loaded`` to run again (unit tests)."""
    global _loaded, _load_disabled, _loaded_env_key, _legacy_compat_installed
    _loaded = False
    _load_disabled = False
    _loaded_env_key = None
    _legacy_compat_installed = False
    registry.clear()
    try:
        from chip import system_pack

        system_pack.clear_caches()
    except Exception:
        pass


def disable_loading_for_tests() -> None:
    """Skip all plugin discovery (engine-only smoke tests)."""
    global _load_disabled
    _load_disabled = True
    registry.clear()


def load_pack_json_surface(surface_key: str, environ: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Read a JSON surface from the active pack when declared (never at import time)."""
    env = os.environ if environ is None else environ
    try:
        from chip import system_pack

        path = system_pack.optional_surface(surface_key)
        if path is None or not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def missing_boot_names(environ: dict[str, str] | None = None) -> list[str]:
    ensure_loaded(environ)
    if registry.secrets_missing_boot is None:
        return []
    return registry.secrets_missing_boot(environ)


def apply_runtime_secrets() -> int | None:
    ensure_loaded()
    if registry.secrets_apply is None:
        return None
    return registry.secrets_apply()


def collect_pack_probe_alerts(environ: dict[str, str]) -> list[dict[str, object]]:
    ensure_loaded(environ)
    if registry.feed_collect_pack is None:
        return []
    return registry.feed_collect_pack(environ)


def is_pack_feed_alert(verdict: Mapping[str, object]) -> bool:
    ensure_loaded()
    if registry.feed_is_pack_feed_alert is None:
        return False
    return registry.feed_is_pack_feed_alert(verdict)


def feed_display_sentence(receipt_body: str) -> str:
    ensure_loaded()
    if registry.feed_display_sentence is None:
        return receipt_body.splitlines()[0] if receipt_body else ""
    return registry.feed_display_sentence(receipt_body)


def feed_on_postable_verdict(
    verdict: Mapping[str, object], environ: dict[str, str] | None
) -> dict[str, Any] | None:
    ensure_loaded(environ)
    if registry.feed_on_postable_verdict is None:
        return None
    return registry.feed_on_postable_verdict(verdict, environ)


def run_alert_handlers(
    alert: Mapping[str, Any],
    *,
    environ: dict[str, str] | None = None,
) -> list[tuple[str, dict[str, Any] | None]]:
    ensure_loaded(environ)
    out: list[tuple[str, dict[str, Any] | None]] = []
    for idx, handler in enumerate(registry.alert_handlers):
        key = getattr(handler, "__name__", f"handler_{idx}")
        try:
            receipt = handler(alert, environ)
        except Exception as exc:  # noqa: BLE001 — ingest must not crash
            receipt = {"status": "error", "error": f"{exc.__class__.__name__}: {exc}"[:160]}
        out.append((key, receipt))
    return out


def merged_alert_routes() -> dict[str, tuple[str, str]]:
    ensure_loaded()
    return dict(registry.alert_route_table)


def feed_health_detail(name: str, health: dict[str, object]) -> str | None:
    ensure_loaded()
    if registry.feed_health_detail is None:
        return None
    return registry.feed_health_detail(name, health)
