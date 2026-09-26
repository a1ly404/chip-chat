# Validation plan (Spec A/B/D stubs)

Dry-run validation uses mock tool execution only (`CHIP_CHAT_MOCK=1`, no live OpenRouter spend).

## Envelopes

| Stub | Path | Intent |
| --- | --- | --- |
| Homelab | `validation/homelab-envelope.json` | PM lane + media law tags |
| Buffr | `validation/buffr-envelope.json` | Evidence-gated CI url_regex |
| Oracle | `validation/oracle-envelope.json` | Thrift class + hop budget |

The JSON files are **fixtures** (shape examples for Spec B/C). The CLI does not ingest them via stdin; create tasks with `chip task create` and copy fields as needed.

## Dry-run commands

From the repo root with a throwaway data dir:

```bash
export CHIP_CHAT_MOCK=1
export CHIP_CHAT_DATA_DIR="$(mktemp -d)"
export PYTHONPATH="$(pwd)/scripts"

# Allowlisted tools (offline)
python3 -m chip tool list
python3 -m chip tool run git-status --json
# exit 3 = gated dry-run (no subprocess)
python3 -m chip tool run git-status --execute --json
# exit 0/4 = actually ran git status in cwd

# Task envelope: create, dry-run run, then execute
TASK_ID="$(python3 -m chip task create --tool git-status --note 'validation dry-run' --json | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')"
python3 -m chip task show "$TASK_ID" --json
python3 -m chip task run "$TASK_ID" --json
# exit 3 = gated (execution_result.gated)
python3 -m chip task run "$TASK_ID" --execute --json

# Room turn (mock): --once TEXT satisfies the message when --as is set
python3 -m chip room demo --as pm --once "validation ping" --json --mock

# Relay inject (mock): Ally snowflake → pm via rooms.json speakers map
export CHIP_WEBHOOK_DEV=https://example.invalid/dev
bin/chip-relay inject --as-ally -m "validation inject" --mock
```

Ledger is checked before any live OpenRouter/Discord call; these flows stay mock-first.
