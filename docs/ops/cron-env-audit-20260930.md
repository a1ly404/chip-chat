# Cron env audit — 11:25 standup line (audit-only, NOT implemented)

- Issued: 2026-09-30 ~21:12Z · Operator: OCgo (cc audit block)
- Scope: locate the failing crontab row, document exact cause. **No fix applied
  (Cursor owns the fix per instruction). No crontab writes performed.**

## Sources checked (read-only)
- `crontab -l` (daffy) — contains the razzorstack PGS timer note, a 09-09
  standup-sh one-shot, the 15:15 watchdog, and the 11:25 standup line below.
- `/etc/cron.d/` and `/etc/crontab` — no chip/standup/watchdog rows.

## Exact current row (verbatim)

```
25 11 * * * TZ=America/Los_Angeles CHIP_CHAT_STANDUP_SIGNAL_FILE=/home/daffy/appdata/chip-chat/standup_signal.json CHIP_CHAT_DATA_DIR=/home/daffy/appdata/chip-chat cd /home/daffy/Tools/chip-chat && ./bin/chip-standup-morning --receipts-dir /home/daffy/appdata/chip-chat/receipts >> /home/daffy/appdata/chip-chat/cron/morning_digest.log 2>&1
```

## Why it fails
POSIX assignment prefixes bind to the **immediately following command only** —
here that is `cd /home/daffy/Tools/chip-chat`, not the `&&` successor
`./bin/chip-standup-morning`. Cron runs the command with `/bin/sh` (dash on
Debian/Ubuntu family), where this semantics holds exactly.

Repro (clean shell, same shape):

```
$ FOO=bar true && sh -c 'echo "env after && : ${FOO:-unset}"'
env after && : unset
```

## Live receipts of the failure
- `~/appdata/chip-chat/cron/morning_digest.log` — runs fired both days, each ends:

```
signal: signal file skipped: no path/no env
```

- `~/appdata/chip-chat/standup_signal.json` — mtime frozen
  `2026-09-28 16:14`, newest key `2026-09-28` (signal contract consumers keyed
  on this file see state-a = page every morning since 09-29).
- Same-day signal skip is code-owned at
  `scripts/chip/standup_signal.py:144`
  (`{"wrote": "", "note": "signal file skipped: no path/no env"}`) — it fired
  because `CHIP_CHAT_STANDUP_SIGNAL_FILE` was empty **in the script's env**.
- Also envless at runtime: `CHIP_CHAT_DATA_DIR` — standup receipts still wrote
  via default `~/appdata` (harmless today, but any future DATA_DIR change from
  the documented default would silently diverge).

## Non-broken sibling
The 15:15 watchdog row needs no env prefix and passes all config as CLI flags,
so it is unaffected:

```
15 15 * * * cd /home/daffy/Tools/chip-chat && .venv/bin/python -m chip.monitor_watchdog --issues-file /home/daffy/appdata/chip-chat/exports/issues_current.json --baseline data/baselines/done_gate_baseline_2026-09-27.json --state-file /home/daffy/appdata/chip-chat/monitoring/wd_state.json >> /home/daffy/appdata/chip-chat/cron/watchdog.log 2>&1
```

## One-line before/after (documentation diff only — not applied)

```diff
-25 11 * * * TZ=America/Los_Angeles CHIP_CHAT_STANDUP_SIGNAL_FILE=/home/daffy/appdata/chip-chat/standup_signal.json CHIP_CHAT_DATA_DIR=/home/daffy/appdata/chip-chat cd /home/daffy/Tools/chip-chat && ./bin/chip-standup-morning --receipts-dir /home/daffy/appdata/chip-chat/receipts >> /home/daffy/appdata/chip-chat/cron/morning_digest.log 2>&1
+25 11 * * * TZ=America/Los_Angeles cd /home/daffy/Tools/chip-chat && CHIP_CHAT_STANDUP_SIGNAL_FILE=/home/daffy/appdata/chip-chat/standup_signal.json CHIP_CHAT_DATA_DIR=/home/daffy/appdata/chip-chat ./bin/chip-standup-morning --receipts-dir /home/daffy/appdata/chip-chat/receipts >> /home/daffy/appdata/chip-chat/cron/morning_digest.log 2>&1
```

(Alternatives: export the two vars inside `bin/chip-standup-morning`, or bake
them into the script defaults. Resolver precedence is already
env → `.env`-style defaults in `scripts/chip/standup_signal.py:51-52`.)

## Dependency note (blocks a "fix = live signal" claim)
Until `CHIP_STANDUP_GITHUB_TOKEN` / `LINEAR_API_KEY` land (see Infisical
receipt), a corrected line still produces an honest-miss digest + signal state
(a) silent-miss page — NOT a green digest. Fix + provision are both required.
