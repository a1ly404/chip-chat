# Infisical access check — chipchat-pc-vl (audit receipt 2026-09-30)

- Issued: 2026-09-30 ~21:12Z (~14:12 PT) · Operator: OCgo (cc audit block, Lyla instructions)
- Scope: m2m access attempt from WSL chip runtime context; KEY NAMES + presence only, values never printed.

## Method
Existing REST client reused verbatim: `scripts/chip_relay/infisical_rest.py`
(boot creds `INFISICAL_CLIENT_ID/SECRET` + Cloudflare Access headers
`CF_ACCESS_CLIENT_ID/SECRET`, login `POST /api/v1/auth/universal-auth/login`,
read `GET /api/v3/secrets/raw?workspaceId=<project>&environment=prod&secretPath=/`).
Detection-only wrapper (no values on stdout/stderr):
`/tmp/opencode/infisical_access_check.py`; creds sourced from `~/Tools/chip-chat/.env`
(key names observed in file: CF_ACCESS_CLIENT_ID, CF_ACCESS_CLIENT_SECRET,
INFISICAL_CLIENT_ID, INFISICAL_CLIENT_SECRET, OPENROUTER_CC_API_KEY — values unprinted).

## Verbatim output

```
INFISICAL_ACCESS_CHECK 2026-09-30 (audit wrapper, detection-only)
api_url: https://infisical.razzormail.com
project_id: cc1062f8…
boot_creds_missing: NONE (all 4 present)
login_http: 200 ctype: application/json; charset=utf-8
login: OK (accessToken withheld)
secrets_http: 200 ctype: application/json; charset=utf-8
secret_count: 5
secret_key_names:
  - CHIP_WEBHOOK_DEV
  - CHIP_WEBHOOK_SELFTEST
  - DISCORD_CHIP_RELAY_TOKEN
  - DISCORD_V1_TOKEN
  - OPENROUTER_CC_API_KEY
requested_keys:
  OPENROUTER_CC_API_KEY: PRESENT
  DISCORD_CHIP_RELAY_TOKEN: PRESENT
  CHIP_WEBHOOK_DEV: PRESENT
  CHIP_WEBHOOK_SELFTEST: PRESENT
  DISCORD_V1_TOKEN: PRESENT
  CHIP_STANDUP_GITHUB_TOKEN: ABSENT
  LINEAR_API_KEY: ABSENT
  CHIP_ALERT_INGEST_TOKEN: ABSENT
(values never printed)
```

## Result summary
- **Cloudflare Access m2m: WORKING from WSL.** Old failure mode (CF Access HTML
  block page → non-JSON login response) NOT hit; both responses were proper JSON.
- Machine identity + login + raw list all succeeded against project
  `cc1062f8…` (= chipchat prod per `infisical_rest.DEFAULT_PROJECT_ID`).
- Relay runtime keys all present for prod boot.

## Missing keys (consistency tie-ins)
| Key | In Infisical? | Consequence seen live |
| --- | --- | --- |
| CHIP_STANDUP_GITHUB_TOKEN | ABSENT | morning digest `Gate: FAIL — gha=no token` (09-29, 09-30) |
| LINEAR_API_KEY | ABSENT | digest `linear=no key`; Linear receipts collectors dark |
| CHIP_ALERT_INGEST_TOKEN | ABSENT | shadow ingest `/alert` bearer-refused → al-9501 ingest path can't authenticate (see al9501 receipt) |

These 3 are exactly Lyla's 2026-09-28 provisioning list; they are not in the
Infisical project yet, and they are NOT in `~/appdata/chip-chat/.env` (file does
not exist) nor in host env. The cron env fix (cron-env-audit receipt) is
necessary but **not sufficient** for a live payload until these keys exist.
