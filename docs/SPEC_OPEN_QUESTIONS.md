# Spec open questions

Ambiguities deferred from Spec A/B/D implementation — no invented behavior.

## Spec C — Ally gates (pending)

Human Ally-gate enumeration for novel irreversible actions is **not** implemented this hour. Destructive media, credential flips, irreversible infra execute, and final cutover verdict are named in OPERATING_LAW rule 6 but mechanical GO-token parsers are out of scope until Spec C lands.

## Gap 4 — Cross-fleet tools

Cross-fleet tool surfaces are structural. When a required tool is not on the local allowlist, agents must stop with `NEED_TOOL:<name>` (OPERATING_LAW rule 19). No remote fleet proxy is implemented in chip-chat.

## Spec D — Thrift social intent

Reading “stand down thrift” or similar social intent from vague wording is **model quality**. Mechanical enforcement uses only exact phrases in the keyword table (`STAND DOWN`, `IDLE`) unless an `@mention` GO row is added to the table later. Do not infer stand-down from prose alone.

## law.md on-disk format

Standing rules use `### id:<rule-id>` with `severity:`, `owner:`, `applies_to:` metadata lines (comma-separated tags). If PM prefers a different serialization, align seed + parser together — do not dual-write.
