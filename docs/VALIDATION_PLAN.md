# Validation plan (Spec A/B/D stubs)

Dry-run validation uses mock tool execution only (`CHIP_CHAT_MOCK=1`, no live OpenRouter spend).

## Envelopes

| Stub | Path | Intent |
| --- | --- | --- |
| Homelab | `validation/homelab-envelope.json` | PM lane + media law tags |
| Buffr | `validation/buffr-envelope.json` | Evidence-gated CI url_regex |
| Oracle | `validation/oracle-envelope.json` | Thrift class + hop budget |

## Dry-run command

```bash
export CHIP_CHAT_MOCK=1
export CHIP_CHAT_DATA_DIR="$(mktemp -d)"
python3 -m chip task create --tool echo --args hello --json < validation/buffr-envelope.json
# Or load acceptance from stub and run with --dry-run (no --execute)
python3 -m chip tool run git-status --json
```

Ledger is checked before any live OR/Discord call; these stubs are mock-first.
