# MANIFEST — Slice B skill bodies: chip-side drafts → razzorstack move plan

> Lane law (RAZ-237 / golden 16): razzorstack writes are Windows-SoT-only.
> Chip lane CANNOT move these files itself. The move executes on the owning
> lane (Cursor / homelab-dev on the Windows clone), or on an explicit OCgo
> lane-waiver from Ally (not granted as of 2026-09-28; this manifest is the
> paper trail either way).

| draft (chip repo) | destination (razzorstack Windows SoT) | template contract | status |
| --- | --- | --- | --- |
| `docs/skill-bodies/internal-loop-degrade.body.md` | `.cursor/skills/internal-loop-degrade.md` | `docs/skill-templates/internal-loop-degrade.template.md` | moved=no, ready=yes |
| `docs/skill-bodies/transport-blip.body.md` | `.cursor/skills/transport-blip.md` | `docs/skill-templates/transport-blip.template.md` | moved=no, ready=yes |
| `docs/skill-bodies/gold-signal-down.body.md` | `.cursor/skills/gold-signal-down.md` | `docs/skill-templates/gold-signal-down.template.md` | moved=no, ready=yes |
| `docs/skill-bodies/skill-runner-fault.body.md` | `.cursor/skills/skill-runner-fault.md` | `docs/skill-templates/skill-runner-fault.template.md` | moved=no, ready=yes |
| `docs/skill-bodies/suppression-saturation.body.md` | `.cursor/skills/suppression-saturation.md` | `docs/skill-templates/suppression-saturation.template.md` | moved=no, ready=yes |
| `docs/skill-bodies/airvpn-port-unhealthy.body.md` | `.cursor/skills/airvpn-port-unhealthy.md` | `docs/skill-templates/airvpn-port-unhealthy.template.md` | moved=no, ready=yes |

## Move procedure (owning lane; receipts to RAZ-16)

1. From the Windows SoT clone: `cp` the five `.body.md` files to the
   `.cursor/skills/<id>.md` paths above (strip the chip-repo banner line).
2. Replace each `skill_version` placeholder with the pinned razzorstack git
   SHA at move time (contract law; no placeholder survives the move).
3. Commit on their lane branch; receipts = one RAZ-16 comment with the five
   destination paths + pinned SHA + commit hash.
4. Chip-side guard tests re-run against the moved copies next agent session
   (lane-crossing read-only check) to prove contract survival.

## Verification-in-place law (this side)

`scripts/test_skill_bodies.py` enforces: every `.body.md` carries the
contract needles of its template (propose class, SHA pin line,
SERVICE_ALLOWLIST wall, verified-as-of done bar, claim-done law, zero
executor wiring). The movable set is exactly the table above; drift fails CI.
