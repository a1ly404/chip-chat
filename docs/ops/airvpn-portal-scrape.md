# AirVPN portal scrape (READ-ONLY)

verified-as-of: see PR body (UTC). Detection only — **no rebind**.

## What this is

Chip tool id `airvpn-portal-scrape` (`python3 -m chip.airvpn_portal`) attaches to an
**already-running** Chrome on **loopback only** via Chrome DevTools Protocol and
scrapes client-area **text** for the reserved forwarded port and bound public IP.
It emits a JSON receipt to stdout. It does **not** launch a browser, store
credentials/cookies/passwords, click, Save, or rewrite portal state.

## Theory (do not contradict)

AirVPN "static" port **54543** means the account keeps that port number. The DNAT
lives on the VPN server holding the **current** session, not forever on one public
IP. When egress moves (example mismatch signature: stale **192.30.89.27** vs live
**38.88.124.105**), the old mapping dies and gluetun's empty `/v1/portforward`
means the client never installed a new one. Portal Save is the control-plane
refresh — **out of scope for this slice**.

## Operator: start Chrome (Lyla / human)

On the machine that already has an AirVPN client-area session:

```bash
# Example — operator starts Chrome with remote debugging on loopback
google-chrome --remote-debugging-port=9222
# or chromium: chromium --remote-debugging-port=9222
```

Open and log into the AirVPN client area in that browser so an `airvpn.org` tab
exists. The agent **only attaches**; it will not open login or invent navigation.

Optional env (no secrets):

| Env | Meaning |
| --- | --- |
| `AIRVPN_CDP_PORT` | CDP port (default `9222`) |
| `AIRVPN_EXPECT_EXIT` | Expected public exit IP (document example `38.88.124.105`; set to live egress) |

```bash
export AIRVPN_EXPECT_EXIT=38.88.124.105   # example — use live egress
CHIP_TOOL_AUTO=1 ./bin/chip tool run airvpn-portal-scrape --execute
# or:
AIRVPN_CDP_PORT=9222 python3 -m chip.airvpn_portal
```

CDP host is always loopback (`127.0.0.1` / `localhost`). Any other host is refused.

## Receipt shape

```json
{
  "expected_port": 54543,
  "expected_exit": "38.88.124.105",
  "observed_port": 54543,
  "observed_exit": "192.30.89.27",
  "mismatch": true,
  "reason": "..."
}
```

- Scrape OK + match → `mismatch: false`, exit 0
- Scrape OK + diverge → `mismatch: true`, exit 0 (detection succeeded)
- Connection refused, no `airvpn.org` tab, or unreadable page → `mismatch: null`
  (unknown), exit non-zero, **no retry loop**

## Fail-loud

- CDP HTTP GET `/json/list` (5s timeout): fail once on connection error
- No `airvpn.org` target → clear error (login/page missing)
- Non-loopback CDP host → refuse
- Runtime.evaluate (if used) is a **source constant** that only reads
  `document.body.innerText` — no user-supplied JS

## Not in this slice

- Portal clicks, Save, rebind, credential handling
- Tier-1 rows (`airvpn-port-regab`, gluetun/qb restarts, etc.)
- Every-reconnect automation

## Future rebind trigger (predicate only — not implemented here)

Rebind is a **later** control-plane action. Candidate trigger:

- receipt `mismatch: true`, **or**
- `inbound_reachable: false` while the local listener (e.g. qB on 54543) is fine

**Not** every reconnect. Mismatch signature to learn: stale exit `192.30.89.27`
vs live `38.88.124.105`.

## Selectors

DOM selectors are **unknown until a real attach** documents them. This skill
scans body text only and must not invent CSS/XPath selectors.

## Audience

Global manifest, `audience: read-only`, risk `low`. **Not** on the pm persona
tool allowlist (`pytest` / `git-add` / `git-commit` only). Not tier-1 destructive.
