# Sprint log

## Phase 1 — Containerize chip-relay (Freeze)

| Field | Value |
| --- | --- |
| Date | 2026-09-25 |
| Intent | Dockerfile + compose for Hyper-V Docker; internal `/health` on 48082; persistent `chip-chat-data` volume; boot `.env` mount; document Infisical/heartbeat env. |
| PR | #13 |
| Merge hash | `f4cdfb6` |

## Phase 2 — Speaker maps + selftest persona (Freeze)

| Field | Value |
| --- | --- |
| Date | 2026-09-25 |
| Intent | Populate `rooms.json` `speakers` for `#chip-dev` / `#chip-relay` (TODO placeholders until ops paste Discord ids); loud failure when maps are unset; selftest resolves `--as` from speakers (deprecate forced `pm`); unit tests + `docs/SECRETS.md` restart policy. |
| PR | #14 |
| Merge hash | TBD |
