# chipchatdev learnings (append-only)

Loaded into the room system prompt (advisory only) since PR #39. Format: YYYY-MM-DD | context | lesson | tag
2026-09-26 | openrouter | GLM 5.3 Flash can stream empty content when reasoning starves tokens — fail-open handled in #35; keep maxTokens headroom | model
2026-09-26 | relay | load Infisical in-process for inject — subprocess secret-skips post empty webhooks (#32) | relay
2026-09-26 | discord | one webhook URL per room with username=<persona> is how specialists stay distinct — never share webhooks | relay
2026-09-26 | relay | stacked [persona] prefixes in replies are transcript-echo artifacts — stripped at post sites + render guard since PR #39 | relay
