# Chip Relay

Discord gateway for Chip Chat rooms: inbound human/bot messages, outbound persona webhooks, inject, delegate handoffs, optional monitor feed hooks.

## Speakers and webhooks

Relay maps Discord snowflakes (and handles) to persona ids from the active pack (`personas` surface). Outbound AI speech uses one webhook URL per room with `username=<persona>`.

In the default **dev** room, the operator (human) maps to `pm`; specialists appear as distinct webhook usernames in the same channel.

`CHIP_RELAY_AUTO` defaults to false. **Topology (PM hub):** inject maps to **`pm`**. One shared webhook per room with **`username=<persona>`** per post. **`delegate:`** only for charter cross-lane work; specialist posts its receipt then stops — no reply chains. At most **3** nested delegate hops per handoff chain (reset on each human or inject-bot turn).

Example: `delegate:chipchatdev fix the flake in chip-selftest`. Relay posts the primary reply under the source persona, logs `relay_handoff` to `threads/<room>.jsonl`, then runs one follow-up `chip room` as the target persona.

## Monitor feed (optional)

When `CHIP_FEED_ENABLED=1`, the relay may poll probe receipts and post tell-tier lines to a configured monitoring channel. Operator-specific probe modules ship in separate plugins; the core feed API is pack-driven.

## Ladder outcome confirmations

The operator (or a speaker mapped to `pm`) can confirm a ladder verdict in the dev room with a one-line receipt format defined in pack `case_intake` config.

## Health

`chip-relay` exposes `http://127.0.0.1:48082/health` inside the container/process for orchestration checks.
