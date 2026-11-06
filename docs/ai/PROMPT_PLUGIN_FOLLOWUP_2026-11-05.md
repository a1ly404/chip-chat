# Standing brief for the plugin agent (2026-11-05)

Paste-ready. Audience: the agent that owns the private plugin repo `a1ly404/chip-chat-razzorstack-plugin`.

---

You own the **plugin**: homelab pack, deploy compose, #502 auto-remediation, Prowlarr recipes, secrets,
host-hook implementations, and the live `chip-relay` / `chip-scheduler`.

**Chip Chat owns the public engine** (`a1ly404/chip-chat`) and its 70% generic-parity track. Chip Chat may flip
engine and ALLY-PC-local flags. Read `docs/plans/engine-parity-70.md` in the engine repo for the interface.

Rules for you:
1. **Consume engine primitives via a pip pin** once Chip Chat publishes a SHA/tag:
   `chip-chat @ git+https://github.com/a1ly404/chip-chat@<full-40-char-sha>` (never a tag). Prefer depending on the engine package over
   rewriting the vendored `scripts/chip` / `scripts/chip_relay`.
2. **Do not reimplement** in the plugin: spend data-dir pin, sandbox exec, recipe schema / grounded GO,
   alert grounding (alert -> recipe id), relay memory (advisory learnings load). Keep homelab recipes as
   pack **data** that the engine schema reads.
3. Do **not** flip `CHIP_RELAY_ALERT_CHANNEL` live unless Ally already allowed it.
4. **Never touch** wireguard, qBittorrent, or gluetun. No secrets, ids, or webhooks in anything that may be exported.
5. Continue the four overnight items if still open: recipe demotion, shadow integration guard,
   Monitoring Bot fixtures, the $0.08 spend note.
6. Report every PR you open to Ally (link + one-line purpose + lumocheck line).
7. If you need an engine change, ask for it in an engine PR comment or via Ally; do not edit the engine repo.
