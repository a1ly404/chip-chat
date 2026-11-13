# Forge driver auth (dev-bot PR pipeline)

`python -m chip.forge_driver open|review|merge|ci` does the forge calls for `chip.dev_pr`. See the
module docstring for the step rules. This page covers how it authenticates.

## GitHub App mode (preferred)

Use two GitHub Apps, one per role, each installed only on the repos in `CHIP_DEVPR_REPOS`:

| Role | Steps | Env (all three required) | Permissions it asks for |
|---|---|---|---|
| dev (author) | `open`, `merge`, `ci` | `CHIP_FORGE_DEV_APP_ID`, `CHIP_FORGE_DEV_APP_INSTALLATION_ID`, `CHIP_FORGE_DEV_APP_KEY_FILE` | contents write, pull_requests write, checks/statuses read |
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
