# Forge driver auth (dev-bot PR pipeline)

`python -m chip.forge_driver checkout|open|review|merge|ci` does the forge calls for `chip.dev_pr`. See the
module docstring for the step rules. This page covers how it authenticates.

## GitHub App mode (preferred)

Use two GitHub Apps, one per role, each installed only on the repos in `CHIP_DEVPR_REPOS`:

| Role | Steps | Env (all three required) | Permissions it asks for |
|---|---|---|---|
| dev (author) | `checkout`, `open`, `merge`, `ci` | `CHIP_FORGE_DEV_APP_ID`, `CHIP_FORGE_DEV_APP_INSTALLATION_ID`, `CHIP_FORGE_DEV_APP_KEY_FILE` | contents write, pull_requests write, checks/statuses read |
| pm (approver) | `review` | `CHIP_FORGE_PM_APP_ID`, `CHIP_FORGE_PM_APP_INSTALLATION_ID`, `CHIP_FORGE_PM_APP_KEY_FILE` | pull_requests write, contents/checks/statuses read |

How each step authenticates:

1. It signs an RS256 app JWT from the key file: `iat` is set 60 s in the past, `exp` is 9 minutes out, and `iss` is the app id.
2. It checks `GET /app` (the id must match) and learns the app's bot login.
3. It mints an installation token with `POST /app/installations/{id}/access_tokens`. The token is limited to the item's repo and the role's permissions.
4. It runs the step.
5. It revokes the token with `DELETE /installation/token`, whether the step succeeded or failed.

The JWT and the token exist only in memory. Neither is logged, and any output is redacted.

Author ≠ approver at the forge:

- `review` posts a real review (`APPROVE` / `REQUEST_CHANGES` with `commit_id` = the head sha), made as the PM app.
- The dev and PM apps must differ in app id, installation and key file.
- The review is refused when the PM app's bot opened the PR.
- `open` refuses to reuse an existing PR unless the dev app opened it.

Fail closed:

- If an app is only partly configured, or a step's role has no app while app mode is on, the step is refused before any network call. App mode never falls back to a static token.
- Tokens are never minted inside a job. `job_policy.job_env` strips every `CHIP_FORGE_*` var from job environments. Mount the key files read-only only into the service that runs the forge steps, never into job sandboxes.

This needs the `forge` extra: `pip install -e '.[forge]'` (PyJWT with cryptography).

## Static token (legacy)

`CHIP_FORGE_TOKEN` (else `GITHUB_TOKEN` / `GH_TOKEN`). One identity serves every step, so `review` is
only a PR comment, and the binding approval is the reviewer row in the dev_pr ledger. This mode is used
only when no `CHIP_FORGE_*_APP_*` var is set. With nothing configured, every step is refused.

## PM-driven steps (`chip.forge_steps`)

`chip.forge_steps` lets the PM run the five driver steps itself instead of a human. It runs on the
PM side (relay or scheduler), which never holds a key, token or app config. The orchestrator
sends only the step name, the item id and plain arguments to a pluggable **step runner**. It then
parses the single JSON line the driver prints. The usual runner starts a separate one-shot process
or container. That process is the only one with the key files mounted read-only, and it runs
`python -m chip.forge_driver`.

| Env | Default | Meaning |
|---|---|---|
| `CHIP_FORGE_STEPS` | empty (off) | Empty or `0` refuses every step. `1`/`all` enables all five steps; a comma list such as `ci,review` enables only those. |
| `CHIP_FORGE_STEP_CMD` | empty | Runner argv prefix as a JSON array, for example `["docker","compose","--profile","forge","run","--rm","-T","forge-svc"]`. No shell is used. The step and its args are appended. If this is empty, every step is refused. |
| `CHIP_FORGE_STEPS_TICK` | `0` | With `1`, the PM tick polls CI on submitted items and merges approved, green ones. It never opens or reviews. |
| `CHIP_FORGE_STEP_TIMEOUT_S` | `300` | Runner timeout, clamped to 10–1800 s. |

The order is enforced before the runner is called. The driver and `chip.dev_pr` check it again
on their side.

0. **`checkout`** runs as the item's author (the dev app, contents read). The item must be `assigned` or `changes_requested`. It shallow-clones the repo's base branch into the item's fixed worktree `<data dir>/devpr/<item>/wt` (built from the item id, never from caller input) and removes the clone's remote. The PM-authorized change job then edits, tests and commits there with no credential and nothing to push to, and `open` pushes that worktree's HEAD. An existing worktree is refused. A deployment whose forge container mounts only the data volume needs this step, because the job has no token to clone a private repo.
1. **`open`** runs as the item's author (the dev app). The item must be `assigned` or `changes_requested`. It needs the worktree, branch, title and the cited job decision id.
2. **`ci`** is a read-only poll of the PR's current head.
3. **`review`** runs as the item's reviewer, and the driver signs it with the PM app. The item must be `submitted`. An `approve` first runs a fresh `ci` poll, which must say `success` on the submitted head. The author can never review, and the PM app may not be the PR author.
4. **`merge`** runs as the PM. It needs an APPROVE from a reviewer other than the author, and a fresh `ci` poll that says `success` on the approved head. The driver re-gates with `dev_pr.merge_gate` and merges pinned to the gated sha.

If any check fails, the step is refused with a reason and nothing runs.

Fail closed:

- Steps are PM-only. They are refused inside a job or bot process (`CHIP_BGJOB_ID` / `CHIP_JOB_ACTOR`), so a dev bot cannot push its own PR through.
- Every step needs a PM reason.
- The runner gets a scrubbed environment: every `CHIP_FORGE_*` var, secret-looking var (token, key, webhook, password) and policy control is removed. Removing `CHIP_FORGE_*` matters for container runners whose config is interpolated from the deployment's own env file: a blank value inherited from the caller would otherwise override it.
- Only the driver's exit code and parsed JSON come back. Raw output is never returned or logged.

Every call, allowed or refused, appends a row to `<data dir>/policy/forge_steps.jsonl`. Each row holds the step, item, actor, the PM's reason, the status, the rc, and a whitelisted summary of the driver's result (worktree, base sha, pr, head sha, url, state, ci, verdict, gate id, merged sha). It holds no bodies and no tokens.

CLI: `python -m chip.forge_steps checkout|open|ci|review|merge|advance|status`. It prints JSON and exits 0 ok, 2 refused, 5 runner/forge error, or 64 usage. `review` takes `--verdict approve|changes`. The verdict comes from the assigned reviewer, and the PM only carries it to the forge.
