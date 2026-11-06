# Chip system pack — authoring + install guide (v0)

Audience: client-pack authors (plugin maintainers, fictional reference clients).
Public engine contract; nothing here imports operator data. Homelab details and
recipes stay in the private plugin repo.

## Pack layout

```text
<your-repo>/systems/<pack-name>/system.json      (the manifest — required)
config/personas.json        optional surface
config/models.json          optional surface
skill-bodies/               optional dir surface
```

`system.json` minimal fields:

| Field | Required | Meaning |
| --- | --- | --- |
| `schema` | required | literal `chip-system-pack/v0` |
| `name` | required | must equal the pack's directory name (`name_mismatch` otherwise) |
| `surfaces` | required | non-empty object `surface_key -> relative path`; see resolution rules |
| `identity` | optional | operator identity values (never Discord ids in public packs; keep real ids private) |
| `values` | optional | generic values; the engine reads e.g. `default_data_dir_relative` |
| `allowlists` | optional | string lists; runtime narrows (never widens) them via intersections — validate checks the list-of-strings shape only, never the contents |
| `env_aliases` / `cli_aliases` | optional | alias shapes; validate checks the shape (docs/conformance) |
| `secrets_key_names` | optional | list of strings; never actual secret values |
| `solver_forbidden_targets` / `blocked_target_patterns` | optional | string lists |

## Surface resolution (what `chip pack validate` accepts)

Runtime resolution lives in `chip/system_pack.py`; validation mirrors it (`chip/pack_validate.py`):

1. **Pack-relative** (`config/...`, `skill-bodies/`): resolved from the pack directory.
2. **Repo-root-anchored** (paths starting `systems/` or `docs/`): resolved from the *pack's repo root*.
   - by default that root is the engine checkout the CLI came from — correct for packs shipped inside
     the engine (`acme-example`, `scripts/fixtures/packs/*`), **wrong for out-of-tree client packs**;
   - pass `--repo-root <client repo>` (CLI) or `validate_pack(root=...)` (module) to point the
     resolver at the client repo that actually contains those paths.

## Surfaces: known, extension, unknown

- **Known contract** (`KNOWN_SURFACES`): `personas`, `models`, `rooms`, `channel_law_dir`,
  `standing_law`, `persona_dir`, `tools`, `lanes`, `topology`, `case_intake`,
  `case_intake_escalation`, `case_taxonomy`, `monitoring_skills`, `runbooks`, `never_auto_done`,
  `counter_registry`, `skill_bodies_dir`, `alert_fixtures`, `event_cases`.
- **Extension surfaces** (`EXTENSION_SURFACES`, client-specific deployments): `relay_alert_routes`,
  `relay_monitoring_policy`, `relay_feed_defaults`, `relay_feed_health_rules`,
  `relay_monitoring_adapter`, `bot_alert_rules` — `validate` reports an `extension_surface`
  **warning** ("resolves here"). Extensions never silently vanish; the engine decides when one
  becomes part of the contract (never the client, never a warning-silence rule).
- Anything else still resolves but warns as `unknown_surface`.
- `chip pack conformance` makes both classes **errors**: conformance = the documented contract only.

## No-leak scan

Public packs (a pack dir under `systems/` of the repo, or under `scripts/fixtures/packs/`) get
the fictional-operator token scan (`homelab`, `razzor*`, `gluetun`, …) by default: manifest text
is scanned, plus each resolved **file** surface. Private `CHIP_PACK_DIR` packs skip the scan
unless `--no-leak` is passed. A private pack scanned as if public will self-trigger — that is
the intended distinction between the plugin's private pack and anything shipped in-tree.

## Command cheat sheet

```bash
chip pack validate --pack acme-example                         # in-repo pack, default root
chip pack validate --pack-dir /abs/path --repo-root /abs/root  # out-of-tree client pack
chip pack init --pack-dir /abs/path/foo                        # fictional scaffold, refuses overwrite
chip pack init --pack-dir /abs/path/foo --force                # on purpose only: replaces scaffold files
chip pack conformance --pack acme-example                      # documented-contract surfaces only
chip pack validate --json ...                                  # machine output (Result.to_dict)
```

Exit codes: `0` OK, `2` FAIL/usage-recoverable, `64` on bad CLI usage patterns.

## Installing a pinned engine package with an out-of-tree pack

Fresh fictional client, no homelab files, no credentials anywhere:

1. `python -m venv .venv && .venv/bin/pip install git+https://github.com/a1ly404/chip-chat@<40-char SHA>`
   (never a tag/moving ref).
2. Scaffold + fill your pack: `chip pack init --pack-dir systems/my-pack` (fictional fixture content
   only), then edit the copy.
3. Validate against your own repo root:
   `chip pack validate --pack-dir systems/my-pack --repo-root "$PWD"` — `systems/**` and `docs/**`
   surfaces resolve against your repo, the rest against the pack dir.
4. Run mock-only smoke: `CHIP_SYSTEM_PACK=my-pack CHIP_PACK_DIR="$PWD/systems/my-pack" CHIP_CHAT_MOCK=1 chip ping`.
5. The engine never writes into your repo beyond what you pass explicitly; secrets come from your
   environment (or a private pack's `secrets_key_names`), never from the engine tree.
