# Private operator pack and plugin

This document describes the **private companion repository** that ships operator-specific packs, deploy assets, and Python plugins. It is **not** part of the MIT-licensed engine tree produced by `tools/engine_export/export.py`.

## What lives in the plugin repo

| Area | Contents |
|------|----------|
| Operator system pack | Personas, rooms, channel law, tools, monitoring config |
| Plugin modules | Optional relay extensions (monitor feed, secret bootstrap, skills) |
| Deploy | Compose, relay deploy scripts, operator runbooks |
| Secrets | Per-host `.env` (gitignored); vault and webhook values |

## How it consumes the engine

1. Pin the public `chip-chat` engine package at a release tag or SHA.
2. Set `CHIP_PACK_DIR` (or ship `systems/<pack-id>` in the plugin repo).
3. Enable plugins with `CHIP_PLUGINS` (comma-separated module entrypoints).
4. Configure relay and OpenRouter env vars on the host (see engine `README.md`).

Secret-store boot variable **names** (vault clients, edge access proxies, etc.) are documented only in the private plugin repo, not in the public engine README.
