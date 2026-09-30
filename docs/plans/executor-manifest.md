# Executor manifest

verified-as-of: 2026-09-30T19:30:00Z

## Recommendation

Keep execution on the existing non-model tool runner (`chip tool run` against `config/chip_tools.json`, lane root, and global denies). Planner and PM personas may only request an allowlisted tool id. They do not get a shell.

A `status: done` sitrep that claims a command ran (`ran`, `executed`, `crontab`, `shell`, or a backtick command) must cite `evidence: tool:<id>`. A doc-only turn may cite `sha:`, a GitHub PR URL, or `RAZ-<id>`. An execution claim without a tool receipt id is rejected by the same post gate (`execution-without-tool-receipt`).

No-route-around stays on the runner: ids and paths outside the allowlist and lane root refuse. A failed chip shot is not retried by the relay. A second planner sentence cannot spawn a process, because only the runner can.

## Trade-off

There is no conversational “ops shell” persona. Anything not in the manifest cannot be run by wording. That is the trust boundary. An LLM executor was rejected because one degraded model would then run every shell.

## Explicitly not built

- A dedicated executor persona.
- Natural-language command handoff between agents.
- New tools in `config/chip_tools.json` for this slice.
