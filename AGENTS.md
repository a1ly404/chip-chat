# AGENTS.md — contributor guide

Audience: humans and automated agents working on the Chip Chat **engine** repository.

## Scope

- Core Python packages under `scripts/chip/` and `scripts/chip_relay/`
- Generic docs, `systems/acme-example`, and shared tests
- **Not** in this repo: private operator packs, deploy compose, or operator-specific secrets (those live in a separate plugin repository)

## Development

1. Use **mock mode** (`CHIP_CHAT_MOCK=1` or `--mock`) for routine work; avoid live OpenRouter spend without an explicit GO.
2. Run tests locally before pushing: `pip install -e '.[dev,relay]'` then `python3 -m pytest -q`.
3. Never commit `.env`, tokens, webhooks, or real Discord snowflakes in engine code — use pack `identity` JSON in private packs.
4. Keep changes minimal and match existing style in the touched module.
5. Commits should be reviewable slices; reference your tracker in commit messages without auto-closing issues unless asked.

## Packs and plugins

- Default pack for CI: `acme-example` (`CHIP_SYSTEM_PACK=acme-example`).
- Operator-specific configuration must not be reintroduced into the public engine tree; use `CHIP_PACK_DIR` / `CHIP_PLUGINS` at deploy time.

## CI

GitHub Actions on `ubuntu-latest` only: gitleaks, Python 3.11/3.12, `pytest -q`. No self-hosted runners or repository secrets required for green CI.

## Export / publish gate

The upstream monorepo provides `tools/engine_export/export.py` to build a publishable engine tree. The export **strict** gate must pass (denylist scan, gitleaks, trufflehog, MIT `LICENSE`, `.gitignore`, no self-hosted `runs-on`) before tagging a public release.

`--report` still writes a full copy of the tree to `--out` (scanners optional). **Never publish or tag a release from a `--report` output directory** — use a green `--strict` run only.
