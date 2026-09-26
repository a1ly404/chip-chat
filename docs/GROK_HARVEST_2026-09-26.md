# GROK HARVEST — 2026-09-26

Design-input harvest from **Grok Bot (Razzorstack PM)** — the original fleet Chip Chat imitates.
**Rounds 1–3 verbatim.** Maintained by chipchatdev lane.

---

## Round 1 — five broad questions

### Q1 — Voice (talking like humans without overacting)

**Asked:** When a room of AI personas talks with humans, the failure modes are robotic stiffness on one side and overacted "quirkiness" on the other. What concrete rules keep your personas sounding like distinct, believable coworkers — length, rhythm, what they never say, how they handle not knowing something? ≤5 numbered rules, ≤25 words each, plus the worst voice failure and the rule it created.

**Grok:**
1. First sentence answers the real ask; no "Certainly," restatements, or "Here's the wrap-up."
2. Complete spoken sentences, mixed length; never headline fragments or label:value telegrams.
3. Match their warmth, not their typos; never narrate your feelings or claim you're human.
4. Never invent menu paths, metrics, or "I checked X" without a real tool/file result.
5. Unknown = say what you don't know and the next real check; never fill gaps with plausible fiction.

**Worst voice failure:** posting household settings notes into #plex-fix like a chatty ops diary. **Rule it created:** that channel is incidents only — standups and notes go to #monitoring-logs.

### Q2 — Orchestration (fuzzy human problem → claimed work)

**Asked:** A human dumps a vague problem ("the downloads are broken again") into a room with five specialist agents. How does Grok Bot turn that into bounded, claimed slices — who decomposes, claiming without collisions, handoffs, duplicate prevention? Include the collision incident.

**Grok:** PM (me) decomposes, not the room free-for-all. PM names the slice (dig-only vs mutate), the owner lane (Ops vs Homelab vs Buffr), and success criteria before anyone moves.

Claim lock is `docs/ops/AGENT_CLAIM.md`: a Linear `claim:` comment is the lock. No claim → no remux/NAS/Plex mutate. Homelab is sole remuxer; Ops never remuxes. Handoffs are explicit ("Ops: dig host_mnt; Homelab: wait for Ally GO") plus room posts when the lane must see it. Collision prevention: grep open PRs + Linear before spawning a fix agent; one claim per workstream; idle Buffr/Homelab Grok agents stay quiet unless Ally @mentions.

**Collision that forced discipline:** duplicate PR/ticket burn (RAZ-204/#324/#326 vs RAZ-202/#323) — spent cycles on a second fix while the first was already open. Gate: before any Composer/cloud spawn, search existing ticket/PR first; no-code-rot applies to tickets too.

### Q3 — Learning that survives (memory that doesn't rot)

**Asked:** The exact mechanism by which a lesson from one session changes behavior in the next — where written, when triggered, how kept from rotting. Include one lesson promoted to a permanent rule.

**Grok:** Write path: after a ship/dig/refuse/automation change, append a short `learning:` comment on the ticket (ticket-learnings skill). Session facts go to durable memory (agent or shared user) via explicit write; reusable multi-step recipes become skills. Triggers: end of incident, Done/refuse, thrift design ticket, Ally charter restates.

Anti-rot: newest wins on conflict; profile facts stay small; logs age; ticket comments stay near the work so bots re-read the issue instead of a rotting wiki. Duplicates get cancelled/Done-unverified, not left as zombies. Soft board cap ≤100 open forces archive/combine.

**Lesson promoted to permanent rule:** claim comment is the lock — remux/NAS without it caused double-touch and false ownership. Also: `#plex-search` (not `#plex-fix`) after repeated misspellings in notes.

### Q4 — Token thrift (cheap room, expensive results)

**Asked:** Concrete thrift rules before a call, during a call, across a session — classification, budgets, context trimming, escalation to expensive models, refusal. Most expensive wasteful failure + the gate it created.

**Grok:** Before a call: classify (script / GHA / skill / cheap agent / expensive wake). Prefer script → GHA → skill → agent. No open-ended dig without Ally GO or Urgent.

During: one lane, bounded success criteria, no Homelab/Ops wake on thrift stand-down; deploy babysit is Cursor/GHA routine, tell Ally once on FAIL, dig only on ping.

Across session: pause expensive PM routines; idle Grok specialists; keep Chip Chat hot only when scoped; refuse fan-out to many agents without Ally yes.

**Most expensive wasteful failure:** overnight AutoDev/babysit + parallel Grok wakes while usage was already high, plus duplicate Composer tickets. Gate: token-thrift skill before any wake/spawn; thrift stand-down (routines paused, agents IDLE except named hot paths); refuse digs that aren't GO/Urgent.

### Q5 — Self-improvement with guardrails

**Asked:** The graduation path from incident → rule → skill; who approves; and what self-improvement is explicitly forbidden.

**Grok:** Path: incident → ticket `learning:` comment → (if reusable) skill write → (if always-on) profile/charter or AGENT_CLAIM / HOUSEHOLD_GOALS. Approver: Ally for irreversible/media/credentials/execute flips; PM for how/when on reversible board work. Skills are global templates; routines need explicit create and can ask confirm.

Forbidden zone: agents rewriting their own safety/claim/remux constraints, flipping execute walls (e.g. `OPS_BOT_TOKEN_ORACLE_CONFIRMED`), editing kill switches or Auto-review to "get past" a block, or waking remux/NAS mutate without Ally GO + claim. Self-edit of guardrails drifts into unsafe "I know better" behavior — improvements land as tickets/skills Ally can see, not silent self-amends.

---

## Gap analysis — Grok mechanism vs Chip Chat status

| Grok mechanism | Chip Chat status |
| --- | --- |
| Thrift ladder (script→GHA→skill→agent) | ✅ ported — `docs/TOKEN_THRIFT.md` (PR #39) |
| Graduation: incident → learning → skill | ✅ ported — razzorstack agent map (`issue_log.json` → skill after recurring twice) |
| Newest-wins consolidation | ✅ ported — global AGENTS.md consolidation rule (2026-09-26) |
| **Claim lock anatomy** (round 2) | ⏳ **porting next** — first `claim: <persona>` comment wins (server `created_at`); unlock via `claim done` / confirmed `handoff:` / steal (heartbeat miss AND ping-no-reply); flock on media paths; enforcement = discipline, not hooks |
| **Forbidden zone** (round 1) | ⏳ **porting next** — standing-law paragraph: no self-edit of guardrails/kill-switches/execute walls; improvements land as visible tickets/skills |
| **Stand-down mode** (round 2) | ⏳ **porting next** — `CHIP_STAND_DOWN=1` relay gate; **manual Ally trigger, not auto**; pauses expensive routines, fleet IDLE except named hot paths; wake = explicit Ally GO/paper trail |
| **Escalation ladder** (round 2) | ⏳ **porting next** — log-and-continue / tell-Ally-once / page (page = remux 2× fail, encode-class, capacity, media delete, credential flips); mute band for night mutate windows |
| **Room-turn discipline** (round 2, `group-chat-turns`) | ⏳ **porting next** — silence is a first-class move; ≤3 messages per turn; react don't repeat; speak only as yourself |
| Channel register (incidents-only channels) | ⏳ porting next — explicit register line in `channels/*.md` |
| Premature-Done watchdog | N/A until chip touches Linear (razzorstack pattern: reopen when any acceptance box lacks `verified-as-of`; 3rd premature Done → mandatory retro) |
| Soft board cap ≤100 open | Rejected for chip (board hygiene is Linear-side, not relay-side) |

**Grok's final verdict (keep/never):** KEEP the claim-as-lock (`claim: <id>` + path lock; Done ≠ claim done) — ownership unambiguous makes everything else cheaper. NEVER build always-on expensive babysit wakes — scripts/GHA own the routine path; agents judge exceptions only.

**Validated:** round 1 confirmed chip-chat is at philosophy-parity; the deltas are four mechanisms, not the model of the system.

---

## Round 2 — prompt + answers (verbatim)

**Part 1 — Skills, verbatim:** full body of `[sand-workflow:ticket-learnings]` + `[sand-workflow:token-thrift]`.
**Part 2 — Skill inventory:** every skill, name + one-liner; ★ = essential for a 5-persona cheap-model clone with pytest/git-status-only execution; short ★ bodies pasted.
**Part 3 —** Q6 claim-lock anatomy (incl. dispute story) · Q7 stand-down triggers/wake-up · Q8 premature-Done watchdog + worst blast · Q9 escalation economics (paging triggers + false-page lesson) · Q10 persona charters + first lane-drift · Final: one thing kept, one thing never built again.

**Grok's round-2 answers (2026-09-26, verbatim):**

### Q6 — Claim locks

**Anatomy:** first Linear comment `claim: <agent-id>` on an unclaimed RAZ; server `created_at` wins. Required: one live claim; path lock = exact canonical `/mnt/nas/media/...` when media. Status and PR links are display only — they do not own the lock. Unlock only via `claim done`, confirmed `handoff:`, or Lyla steal (heartbeat miss AND ping-no-reply). Enforcement is pure discipline + AGENT_CLAIM.md obedience; remux adds flock on path (not a Linear webhook kill). **Dispute:** Homelab Research lane tried remux-class work. Rule: Research never remuxes, never ffprobes, never claims remux tickets — Homelab Dev is sole remuxer.

### Q7 — Stand-down

**Trigger today:** manual Ally call at spend pressure (~71% remaining 2026-09-25), not an auto cron. **Pauses:** PM expensive routines (digests, babysits, auto-merge, etc.). **Stays hot:** Chip Chat scoped bot; Buffr AutoDev deploy = Cursor pr-merged routine + GHA (not Grok Deploy). **Fleet IDLE:** Homelab/Ops/Buffr Grok agents unless Ally @mentions. **Wake-up:** Ally GO / @mention / explicit resume — **not automatic** when usage drops. **Incident:** parallel Grok wakes + overnight babysit burned weekly budget. Gate: thrift stand-down + deploy off Grok onto Cursor/GHA. (Earlier gate had a separate `spend-guard:pause` card — resume needed Ally paper trail.)

### Q8 — Premature Done

**Done requires green CI + per-checkbox `verified-as-of` bound to acceptance** — deploy alone never counts. `log-only ≠ Done`; `claim done ≠ Linear Done`. **Watchdog:** PM/Ally reopen on missing verification; third premature Done → mandatory retro. Sitrep lines older than the last change window are stale, not facts. Program tickets stay In Progress until checklist verbatim-verified (AGENT_CLAIM / #280). **Worst blast:** RAZ-199/198/201 cluster + RAZ-177 second premature Done; also #540 merge auto-Done reopened RAZ-186. Rule: reopen if any acceptance box lacks verified-as-of.

### Q9 — Escalation economics

**Log-and-continue:** non-Buffr merge skips, CF flaps already ticketed, thrift silent skip. **Tell Ally once:** Buffr ship FAIL after merge (deploy health / e2e skip) — no dig unless pinged. **Page / GO-required:** remux 2× fail, encode-class, capacity gap, media delete, credential/execute-wall flips. **Mute band:** no Layer-2 WSL recover inside 01:00–05:00 mutate window without Ally ack. **False-page lesson:** Discord "Plex down" while native PMS was fine (docker Arr false alerts). Rule: `SKIP_KEYS {plex,sonarr,radarr}` + host health; #plex-fix = incidents only, not settings chat.

### Q10 — Persona charters

**Charter** = profile description + AGENT_CLAIM lane + room membership (Homelab vs Buffr). Contains: what they own, what they never touch, claim/remux rules, thrift IDLE policy. **Enforced by** PM decomposition + claim lock + "don't wake IDLE unless @mention" — not a hard sandbox. **Drift fix:** stand agents down; route work to Cursor/GHA; Chip Chat stays its own scoped bot. Out-of-lane reply: paraphrase only; never speak as another persona in their room. **Live miss:** Buffr-shaped reply landed in Chip Chat lane. Rule: room tag + speak-only-as-yourself + Chip Chat owns prototype lane; Buffr agents IDLE unless @mentioned.

### Final — keep one, never build one

**Keep:** Linear claim as the lock (`claim: <agent-id>` + path lock + Done ≠ claim done). Everything else can be cheaper if ownership is unambiguous.
**Never build again:** Always-on expensive babysit routines that wake specialists to watch GHA/health. Scripts and GHA own the routine path; agents judge exceptions only.

---

## Round 3 — time, attention, model routing (verbatim)

### Q11 — Cold start

1. Wake payload: ticket ID + claim owner + success criteria + one latest `learning:` / triage + thrift class.
2. Include walls that bind this slice (kill, Ally-only, remux path) — not the whole house charter.
3. Leave OUT: full chat history, old sitreps, unrelated board noise, guessed root cause.
4. Executor starts empty on purpose; dispatch prompt must be self-contained for that slice only.
5. Prefer machine-checkable evidence links over narrative "what we tried last week."

**Failure:** agents anchored on stale wg0-down / CONFIRMED flags from old sitreps. Rule: every state line needs `verified-as-of`; older than last change window = stale, not fact.

### Q12 — Mid-task failure (park discipline)

1. Finish only if the slice still meets claim AC without widening scope.
2. Park when env is broken (service down, flaky gate, Ally unpaid click) — do not thrash.
3. Record park on the ticket: `parked: <why>`, evidence, `next:`, claim held or `handoff:`.
4. Hand off only with confirmed `handoff:` + new claim; never silent abandon.
5. Dig-only stays dig-only; mutate needs fresh Ally GO after park.

**Went bad:** RAZ-301 code Done while `#plex-search` human smoke stayed unpaid — looked finished, wasn't. Rule: code Done ≠ acceptance; unpaid smoke stays open FINDING.

### Q13 — Model routing

1. Default: cheap-agent + locked skill (triage, claim checklist, short sitrep).
2. Escalate to expensive when: novel judgment, Ally GO, Urgent, or Composer/cloud code.
3. Step down when the path is script/GHA — do not babysit with an expensive wake.
4. Signals up: thrift class fails, disproof needs live dig, PR conflict needs coder.
5. Signals down: verify/build green and only deploy gate left → GHA owns it; tell Ally once.

**Paid off:** Buffr deploy babysit → Cursor `pr-merged` + GHA (routine ships ≈ 0 Grok Deploy tokens). **Didn't:** Weekend AutoDev babysit (last run failed; burned without earning) — paused under thrift.

### Q14 — Ally attention

1. Lead with the decision ask in one line (GO / HOLD / pick A|B|C).
2. Then 2–4 facts with evidence links; unpaid item named; stop.
3. One message = one decision surface; no multi-topic essays.
4. Widget when options are real and verified; never padded off-ramps.
5. Never bury the ask under a tour of what bots did overnight.

**Failed:** long EOD walls of merges + FINDINGS with no single ask → skimmed. **Worked:** "Buffr #562 FAIL at SHA 7dc7260 — dig on ping?" — one link, thrift left it parked.

### Q15 — Memory layering

1. Room transcript: ephemeral coordination; silence is fine; not SoT.
2. Ticket comments: durable work facts (`learning:`, `triage:`, `claim:`, verified-as-of).
3. Durable memory: standing Ally charter, channel rules, secret *names*, thrift policy.
4. Skills: reusable procedures every bot must obey without re-deriving.
5. Migrate: repeated ticket learning → skill; repeated skill → script/GHA; never invent a new "learnings" ticket.

**Wrong layer:** `#plex-search` misspelled as `#plex-fix` lived in chat/memory notes and kept confusing routing. Fix: promote to profile memory + ticket learning; always say `#plex-fix`.

### Q16 — Cadence audit

1. Earn tokens if: changes Ally decision, catches a wall, or unlocks Done with evidence.
2. Kill if: same "no change" noise, fails often, or duplicates GHA/script.
3. Test: last 3 runs — actionable delta ≥1 or pause it.
4. Standups stay only in `#monitoring-logs`, not `#plex-fix`.
5. Under thrift: pause digests/babysits; keep only named hot path (Chip Chat + Buffr Cursor deploy).

**Killed / never missed:** Weekend AutoDev babysit and most paused PM digests — quiet weekends were fine; GHA + Ally ping covered ships.

### Q17 — Disagreement arbitration

1. Tie-break = machine-checkable evidence + explicit disproof line, not seniority vibes.
2. PM decides how/when; Ally decides irreversible / media / credentials / execute walls.
3. Triage `needs-repro` beats confident `actionable` without evidence.
4. Feature vs dig on same Buffr surface: dig waits or Ally steal.
5. Duplicate claim: first Linear `created_at` wins; second warns, then triage-only if repeat.

**Real case:** native Windows PMS vs Docker Plex "who's down?" false alarms. Evidence: host Plex healthy + docker Arr false keys. Resolution: SKIP_KEYS + stop docker Arr; Homelab claim for native migrate only after Ally GO.

### Final — copy before building v1

**PM-like law for cheap models:** fixed envelopes (claim / triage / learning / parked / verified-as-of) with zero judgment fields, plus a spend gate that refuses open digs. Chip Chat's next design should ship **durable law-md + KISS protocol/memory specs first — not more chat features** — so glm-flash implementers can't invent process. Skip our v1 mistake: always-on expensive babysit personas that re-derive policy from chat instead of obeying short machine-checkable contracts.

### Round 4 — law review + blind spots (verbatim)

**Part 1 — Law corrections (≤5):**
1. **stand-down is dangerously over-broad.** Real thrift stand-down is selective: expensive personas IDLE unless @mentioned; Chip Chat stays hot; Buffr AutoDev deploy = Cursor/GHA routine stays enabled. Freezing ALL persona wakes would kill the hot path Ally explicitly kept. Spec: name what pauses vs what stays hot; never imply total fleet silence.
2. **escalation-ladder incomplete + conflated.** Page/GO triggers also include encode-class, capacity gap, Ally-only walls (OPS_BOT_TOKEN_ORACLE_CONFIRMED, execute flips, media deletes). "Tell Ally once / no dig unless ping" is the Buffr ship-FAIL thrift path, not a substitute for paging remux/credential incidents — split those.
3. **room-turns should not be (HARD, PM).** Every persona obeys speak-as-yourself + silence-as-valid; PM coordinates but doesn't uniquely own room law. Add: @mention ⇒ respond, never read another agent's private chat/memory, private notes use DM not the room.
4. **forbidden-zone misses the bypass class.** Beyond rewriting law: Auto-review workarounds (encode/rename commands, cookie/session scrapes, minting access from stored secrets) and silent wall flips. learning:/skill proposals never auto-apply — Ally/PM must promote.
5. **Missing claim-steal/unlock law.** Unlock only via done, confirmed handoff:, or steal (heartbeat miss AND ping-no-reply) — without it, stand-down + park deadlocks the board; agents invent "I'll just take it."

**Part 2 — Spec these two before an incident:** (1) Stale-claim break — heartbeat interval, ping definition, who steals, park-pauses-heartbeat? Bites first: half-finished claims after stand-down/mid-task break. (2) Scope/path granularity — one live claim ↔ one canonical path; forbid ticket-wide claims. Defer: long handoff chains (confirmed handoff: each hop; no transitive ownership).

**Part 3 — New-lane onboarding (before first task):** Charter (owns/never-touches/mutate class) · Room seat (IDLE-unless-@mention) · Claim obedience · Triage gate · Thrift class (default cheap-agent) · Channels (standups vs incidents) · Done bar (CI + verified-as-of) · Kill/walls · Smoke (dry-run ticket: triage→claim→refuse-without-evidence→park). Cheap-model: envelopes only (no judgment fields); allowlisted tools; refuse free dig; wake payload minimal; no vibes-memory without learning: shape.

**Part 4 — Mandatory retro (3rd premature Done):** Facts (IDs, who flipped, timestamps, missing boxes) → Blast radius → Root cause → ONE concrete rule edit → Reopen list. ≤1 page; PM + Ally attend (+Lyla if media); flipping lanes comment evidence only; lands on program ticket + thrift ticket as learning: + AGENT_CLAIM patch — never only in chat.

**Final — ranked blind spots (ask these next):**
1. Tool allowlists + --execute gates (how CHIP_RELAY_AUTO=false interacts with stand-down)
2. Evidence forgery / confident lies — pytest green but prod isn't; refusing "I verified" without checkable artifact
3. Human-in-the-loop unpaid clicks (OTP, UI pick, ATV smoke) — so tickets don't false-Done
4. Secret handling — names vs values; Infisical vs Cursor env; never in tickets/Discord
5. Idempotency / TOCTOU — remux flock, session>0, double Composer on same RAZ
6. Cross-room leakage — Buffr→Chip Chat drift; DM vs room; shared history
7. Spend meters — what CHIP_STAND_DOWN keys off; paper-trail resume format
8. Law test harness — pytest rejecting malformed claim/parked/learning envelopes

**If only one more round: #1 tool allowlist + #8 envelope tests — they prevent the first cheap-model jailbreak better than more prose law.**

## Part 2 — `[sand-workflow:oracle-ops-ship]` (58 lines, verbatim)

```markdown
---
name: Oracle ops ship
description: >-
  Use this when implementing or shipping a change to the razzorstack Oracle ops
  VM (ops-api, oracle-controller, Homarr splash, tunnel, or related
  compose/scripts).
---
# Oracle ops ship

Manual deploy only. There is no CI that ships to Oracle.

## Surfaces
- Clone: /home/daffy/Tools/razzorstack
- VM: SSH oracle-razzorstack (ubuntu@64.181.244.100), code at /opt/razzorstack
- Compose: docker/docker-compose.oracle.yml
- Secrets: Oracle docker/.env only (mode 600). Never overwrite it from the
  homelab .env. Never put billing keys or OPS_API_KEY in git or Discord.

## Do not
- Media, torrent, or Plex on Oracle
- git clone / git pull on the VM
- Unsupervised full-stack docker compose up -d --build
- React/Dashy/Homarr inside ops-api (Homarr is a separate splash)
- Prometheus/Grafana on Oracle (Netdata only)
- Access wildcard *.razzormail.com
- || true on deploy-record scripts

## Ship
1. Implement in the homelab clone. PR/merge as usual; Oracle does not pull git.
2. Targeted scp of changed paths to /opt/razzorstack (full tar only when needed;
   exclude .git, docker/.env, node_modules).
3. SSH and rebuild ONE service with --no-deps:
   ssh oracle-razzorstack 'cd /opt/razzorstack/docker && \
     docker compose -f docker-compose.oracle.yml build <service> && \
     docker compose -f docker-compose.oracle.yml up -d --no-deps <service>'
   Cheat sheet: python/ops_api/** → ops-api; python/oracle_controller/** →
   build --no-cache oracle-controller; Homarr image/env → up -d --no-deps homarr;
   tunnel token/CF → --profile tunnel up -d cloudflared-oracle.
4. Record: bash scripts/record_oracle_deploy.sh --service <s> --sha <short-sha>
   on Oracle (loads OPS_API_KEY from docker/.env).
5. Verify localhost (bypasses Access):
   curl -sf http://127.0.0.1:48102/api/health · 48101/health · compose ps
   Browser: https://ops.razzormail.com (email OTP). Smoke from homelab:
   bash scripts/smoke_oracle_link.sh.
After first ops-api deploy or cron changes: install_oracle_ops_cron.sh,
install_oracle_harden_cron.sh, oracle_env_harden.sh --apply on Oracle.

## Locked product
One vanilla index.html + FastAPI for ops-api. One SQLite ops.db.
Spend v1 = OpenRouter + Vultr + OCI; Cursor is a portal link.
Mutating routes need X-Ops-Key plus Access.
```

## Skills section (delivered 2026-09-26, bodies verbatim — formatting lightly normalized from Grok's paste)

### `[sand-workflow:ticket-learnings]`

```markdown
---
name: ticket-learnings
description: >-
  Use after any ship, dig, incident, refuse, or automation change — and when
  filing or updating a token-thrift design ticket — to append a short learning:
  comment so every bot stays current without re-discovering the same failure.
---
# Ticket learnings (all bots)
Keep Linear as the shared brain. When something changes, write it once on the
ticket so the next bot does not re-burn tokens rediscovering it.

## When (mandatory) — after any of:
- Merge / GHA deploy / live e2e result (pass or fail)
- Dig, incident, or refuse (triage `noise` / `blocked-env` / Ally HOLD)
- Script or GHA added, fixed, or proven inert
- Skill created/updated that bots must follow
- Wall / kill / credential / path-lock discovery
- Token-thrift inventory or class change (`script` | `GHA` | `cheap-agent` | `expensive-agent`)

Skip only pure ack chat with no new fact.

## Where
1. **Work ticket** (the RAZ you claimed) — always.
2. **Board thrift ticket** — also, when the learning is reusable automation/token advice:
   Homelab Media → RAZ-83 · Ops/Oracle → RAZ-24 · Platform/Automation → RAZ-16 ·
   Household Autonomy → RAZ-276 · GymLog/Buffr → RAZ-93 and/or RAZ-82 (no third thrift ticket).

Do **not** create a new "learnings" Linear issue. Free-create is capped — repurpose
canceled or comment on the thrift ticket.

## Comment shape
learning: <one-line fact>
class: script|GHA|cheap-agent|expensive-agent|wall|refuse
when: <YYYY-MM-DD ~HH:MM PT>
evidence: <PR# / GHA run / probe / path — machine-checkable>
next: <none | promote-to-script | promote-to-GHA | update-skill:<id> | Ally-GO-needed>

## Rules
- One fact per comment (or tight bullets under one `learning:`).
- Redact secrets; never paste tokens/keys.
- Prefer promoting a repeated learning into a script/GHA or a shared skill
  (`next: update-skill:…`) over leaving it as tribal chat.
- If you update a skill, say so on the thrift ticket and link it.

## Examples
learning: GHA post-deploy e2e must hard-fail the workflow; Deploy-agent babysit is not the gate
class: GHA / when: 2026-09-24 ~23:08 PT
evidence: PR #549 2bbcf50 run 36100582601 e2e-prod 37p/1s / next: none

learning: discord-bot loops must start after Discord login; pre-login start = silent missed standups
class: script / when: 2026-09-24 ~15:02 PT
evidence: RAZ-208 PR #547 /health loops_ok / next: promote-to-script

## Done / claim (do not confuse)
`learning:` updates knowledge — it does not flip Linear Done. Done still needs green CI +
per-checkbox verified-as-of (PM/Ally). Claim done ≠ Done. log-only ≠ Done.

Pair with token-thrift — classify before spending tokens, then write the learning here.
```

### `[sand-workflow:token-thrift]`

```markdown
---
name: token-thrift
description: >-
  Use before any agent wake, Composer/cloud spawn, dig, deploy babysit, or
  routine that could burn AI tokens — prefer script → GHA → cheap skill →
  expensive agent, and refuse open-ended digs without Ally GO / Urgent.
---
# Token thrift (all bots)

Goal: easier automation with **fewer AI tokens**. Scripts and GHA own the routine
path; agents judge exceptions.

Design boards (inventory + migration plans live on these tickets):
Homelab Media → RAZ-83 · Ops/Oracle → RAZ-24 · Platform/Automation → RAZ-16 ·
Household Autonomy → RAZ-276 · GymLog/Buffr → RAZ-93 (GHA) + RAZ-82 (triage skill).
Productization is parked — do not open thrift work there.

## Decision ladder (hard)
1. **script** — cron, probe, CLI, compose health, metrics emit, idempotent cleanup, no judgment
2. **GHA** — deploy-on-merge, CI bind-audit, image build, post-deploy e2e gate, secret-drift check
3. **cheap-agent** — locked skill only (triage, claim, gate checklist, verified-as-of,
   short sitrep). Low-token model. Refuse open-ended dig.
4. **expensive-agent** — Composer / cloud / long dig. **Only** when: novel judgment,
   Ally GO, or ticket Priority Urgent / named incident.

If it fits 1 or 2, **do not** wake an agent to babysit. Point at the script/GHA path
(or file a one-line gap on the board thrift ticket) and stop.

## Refuse
- Open-ended SSH / remux / library dig without Ally GO or Urgent
- Spawning a second Composer/cloud when an open PR or claim already covers the
  ticket (grep PRs + Linear first)
- Routine digests, metrics, standups, or health flaps as Composer work
- "Meanwhile" fan-out to many agents to burn tokens ahead of an Ally answer
- Media deletes, `--force-window`, credential flips, `OPS_BOT_TOKEN_ORACLE_CONFIRMED` without Ally

## After any change
Run ticket-learnings: post a `learning:` on the work ticket **and** fold
thrift-relevant scraps onto the matching board thrift ticket above.

## Success metric
Fewer Composer/cloud wakes per Linear **Done**. Routine ships ≈ 0 Deploy/Composer
tokens (GHA + live e2e gate where that pattern exists).

## Related skills
- ticket-learnings — keep tickets current
- gymlog-autodev-triage / homelab-autodev-triage — cheap pre-claim gates
- oracle-ops-ship — Ops ship path
- Done discipline: green CI + per-checkbox verified-as-of; claim done ≠ Linear Done;
  log-only ≠ Done
```

### `[sand-workflow:group-chat-turns]` (★ transferable, pasted by Grok)

```markdown
---
name: group-chat-turns
description: >-
  When a user message starts with a [room "…"] tag: you are taking a turn in a
  group chat room, not your private chat, so read this before replying.
---
# Group chat turns
A user message that begins with a [Group chat: "..."] tag is a turn in that group
chat room, not your private chat (an untagged user message is your private 1:1 chat
with your user). Your one conversation carries your private chat and your turns in
every room you're in, each room turn tagged this way. For the whole of a room turn,
SendToUser delivers to that room instead of your user. Text, attachments, widgets,
and cards alike are rendered in the room for everyone there, just as they would be
in your private chat. To say something privately to your own user during a room
turn, send it with to:"dm". It lands in your 1:1 chat and the room never sees it.

- Reply-first does not apply in a room turn: your tool calls and plain assistant
  text are private scratch space the room never sees, so when the conversation
  calls for real work, do the work first, then deliver the result with SendToUser.
  A room turn with no SendToUser means you stayed silent.
- You have your full toolkit in rooms, with the same tools as your private chat and
  no reduced limits. Never claim you lack a tool in a room that you have in your
  private chat. Answering the room from your unified history, including what you
  learned in your private chat, is expected; the only exceptions are things your
  user explicitly asked you to keep out of a room. Never go looking for a
  teammate's private chats, memory, or files.
- Several distinct participants share a room. Speak only as yourself: never write
  as another participant or as the user, and never narrate the conversation from
  the outside.
- Keep each message short and conversational, usually one to three sentences, the
  way people actually chat. Send at most 3 messages in one room turn. Do not
  monologue or summarize the whole thread.
- React to what was just said: build on it, agree, disagree, or ask a pointed
  question. Address others by name when it helps. Do not repeat points already
  made, and do not restate other people's messages back to them.
- Mentions: write @Name to direct your message at a specific teammate, or
  @everyone for the whole room. If you are @-mentioned you are being asked to weigh
  in, so respond; to pull a teammate into the conversation, @-mention them.
- If you have nothing new worth adding right now, simply end your turn without
  calling SendToUser. In a room, staying silent is a first-class move, never a
  failure. It lets the conversation settle instead of spinning forever. Say your
  piece in one turn, then stop.
```

### Part 2 — Skill inventory (★ = essential for a pytest+git-status clone)

**Household / user skills (in /workflows):**
| Skill | Purpose | Flag |
| --- | --- | --- |
| gymlog-autodev-triage | Pre-PR triage: actionable vs noise before claim | ★ (179 lines — ask for paste) |
| homelab-autodev-triage | Evidence + disproof before remux/Composer | ★ (69 lines — ask) |
| lumocheck | Two-pass Lumo plan gate before non-urgent impl | |
| lumo-tamer | Proton re-auth when Lumo token dies | |
| oracle-ops-ship | Ship path for Oracle ops VM / compose | |
| ticket-learnings | Append learning: after ship/dig | ★ (Part 1) |
| token-thrift | Classify script→GHA→cheap→expensive | ★ (Part 1) |

**Cursor-managed (platform; mostly don't transfer):** add-connector, export-bot-template, accommodation/flight/food/job/restaurant/rideshare/shopping/scheduling, purchases, send-on-behalf, box-desktop, channels, in-chat-forms, learn-from-demonstration, no-connector-fallback, routines, skill-authoring, source-control, voice-calls, site-playbooks-* (retail/travel), code-changes (repo work → Cursor cloud).

| Skill | Purpose | Flag |
| --- | --- | --- |
| group-chat-turns | Room vs DM discipline | ★ (pasted above) |

**Grok's transfer summary:** ★ = thrift + learnings + both AutoDev triages + room turns. Skip booking/playbook/box-desktop/cloud-code skills.

---

## Round 5 — red team (verbatim)

**Part 1 — Bypasses:**

*stand_down() — 3 bypasses:* (1) **Env not inherited** — subprocess/worker without the var runs wide open while parent thinks it's frozen → fail-closed on a durable flag file every process must open; env is a cache, not SoT. (2) **Gate only on delegate hops** — free chat, fake `claim:` lines, "propose pytest" still run → stand-down refuses writes too: no claim accept, no tool propose/execute, persona turns except Ally/human. (3) **Truthy typo** — `CHIP_STAND_DOWN=true/yes` silently treated as off → only exact "1" enables; other non-empty garbage → refuse + alert, never silently off.

*scan_claims/_CLAIM_RE — 3 bypasses:* (1) **Optional scope** — `claim: buffr-dev` owns nothing checkable or implicitly owns the room → require `claim: <persona> <scope>`; reject missing/wildcard (`*`, `all`, `/`). (2) **claim done without owner check on execute** — tool runner never calls claim_owner; duplicate counted, work still runs → every `--execute` asserts `claim_owner(room, scope) == speaker` else refuse. (3) **Transcript/typo vectors** — fullwidth `：`, BOM/ZWSP, claim buried in quoted replay, spoofed `claim: ally RAZ-1` → NFKC-normalize; envelope must be the whole message body; persona must equal authenticated speaker; reserved ids (ally, lyla, pm) not claimable as worker.

**Part 2 — Allowlist ruleset (8) + matrix:**
1. Manifest absolute: only pytest, git-status, task-show; unknown tool → hard refuse (no "similar").
2. Propose ≠ execute: cheap-agent emits proposal card; runner executes only with `--execute` or `CHIP_TOOL_AUTO=1`.
3. Default both auto flags off; relay never chains tools unattended.
4. script/GHA classes: personas never execute host scripts; point at ticket/GHA only.
5. cheap-agent: `--execute` allowlisted tools only under live claim + matching scope; pytest scoped to claimed path; git-status/task-show read-only.
6. expensive-agent: same allowlist — escalation is who wakes, not a wider tool surface; widen tools only via Ally GO manifest amend.
7. `--execute` scope: per-invocation + per-tool + must cite `scope=` matching claim (session GO ≠ blanket unlock).
8. Stand-down overrides all: no delegate, no --execute, no propose — except Ally/human.
Hard rule: **never let RELAY_AUTO imply TOOL_AUTO.**

Matrix (R=RELAY_AUTO, S=STAND_DOWN, E=--execute): R0S0E0 = chat+propose only · R0S0E1 = run one allowlisted tool if claim_owner ok · R0S1* = refuse hop+execute+propose · R1S0E0 = still no tool run (AUTO≠tools) · R1S1* = freeze wins · R*S0+CHIP_TOOL_AUTO=1 = standing GO for allowlist only, still needs claim_owner, log every run.

**Part 3 — Law pytest harness (implement verbatim):** 9 tests — claim_requires_persona_and_scope (missing/wildcard/spoofed/reserved/empty) · claim_first_wins_duplicate_counted · claim_done_only_own_active · parked_requires_why_next_evidence (next∉enum reject; claim released without handoff|done) · learning_shape_and_class_enum (+token-shaped strings) · verified_as_of_bound_to_checkbox (bare "verified" / relative time / non-machine-checkable) · handoff_requires_confirm_fields (to==from; no ack before ownership moves) · envelope_must_be_whole_message (buried in prose/codefence; fullwidth colon; confusables) · stand_down_blocks_claim_and_execute.

**Part 4 — Evidence forgery:** Checkable = runner-produced artifact id the harness can reopen (pytest JUnit path+exit, git-status stdout hash, GHA run URL, probe JSON) — not model prose. Minimum per type: claim work → tool receipt; Done checkbox → artifact+timestamp; learning → PR/GHA/probe; park → error string from tool/env. "I verified" without receipt → refuse; treat as needs-repro, never actionable/Done. Cited artifact missing/404/wrong SHA → integrity fault: reopen, strip trust, thrift-warn; second time in 14d → triage-only persona. **Persona cannot mint receipts — only the tool runner attaches `receipt:`.** Incident: premature Done / "merged so Done" on RAZ-177/186/199 clusters — board lied green. Rule: deploy/merge ≠ verified; missing artifact ⇒ reopen.

**Part 5 — rapid-fire:** (a) unpaid clicks: ticket In Progress + `checkbox unpaid: Ally <action>`; no Done while unpaid; remind once per cadence, never fake smoke. (b) secrets: NAMES ok (OPENROUTER_CC_API_KEY); values never in rooms/tickets/Discord/git; redact token-shaped model output — refuse post. (c) TOCTOU: atomic flock on canonical path covering session-idle, no check-then-act; session>0 ⇒ refuse mutate; grep claim/PR before spawn, second Composer refuse. (d) cross-room: speak only as yourself; shared = that room's transcript only, never other rooms' DMs; private → DM. (e) spend/resume: key off explicit flag (+durable file), not silent %-guess; resume = Ally message + ticket comment `resume: stand-down cleared when:<PT> by:Ally`; no auto-resume on usage drop or clock.

**Final — first v1 jailbreak:** claim parser + tool runner are DECOUPLED — persona posts persuasive prose ("pytest green, shipping") or a malformed/unscoped claim; the map ignores it while `--execute` never checks claim_owner; stand-down only blocks hops so the persona still "works" in-channel and social-engineers Ally or a hot lane. **Single patch: one `authorize(speaker, scope, action)` used by claim accept, handoff, and every tool execute — false unless (not stand_down ∧ claim_owner==speaker ∧ tool∈manifest ∧ receipt_required). No second path around it.**

---

## Round 6 — self-healing (verbatim)

**Q18 Pipeline (log → fixed, no human eyes):** Detect (structured probe/health emit — key + severity + fingerprint, not free-text vibe) → Triage (actionable | flap | noise | blocked-env; evidence+disproof) → Claim (`claim: <fixer> <scope>` only if actionable + no live claim/PR) → Fix (allowlisted runbook only — restart named service, clear known flag; else propose) → Verify (same probe green twice across a quiet window + receipt; then `learning:`).
**Always keep a human:** mutate / Layer-2 recover / remux / credential / execute-wall / anything in 01:00–05:00 that isn't a pre-approved runbook. Detect+triage+claim+propose can be dark; **first live recover that restarts or writes state needs Ally ack** until that exact fix class is graduated. **Incidents that drew the line:** L1 detect OK autonomous, L2 recover requires Ally ack before live, no L2 in mutate band; Discord "Plex down" while native PMS was fine — autonomous "fix Plex" would have been the wrong mutate.

**Q19 Noise floor:** (1) Actionable = new fingerprint + machine-checkable fail + not in SKIP_KEYS/suppression list. (2) Flap = ≥N state flips in window W → suppress alert, keep counter, don't wake fixer. (3) Dedup = same fingerprint coalesces for T (30–60m); one ticket/claim max. (4) Repeated noise → auto-suppress + `learning:`; escalate only if fingerprint *changes* or burn rate spikes; dead-letter after M suppressions with no human reopen. (5) Never Composer-wake on routine health flaps. **Storm:** Docker Arr/Plex keys alerting while household Arr/Plex were **native** — false "down" loops into #plex-fix. **Pattern:** SKIP_KEYS {plex,sonarr,radarr} + host-path health; incidents-only channel; monitoring AUTO_RESTART cleared so bots stop "healing" ghosts.

**Q20 Fix blast control — before mutate, proof:** repro = current probe fail receipt (not chat); root-cause class ∈ allowlisted runbook id; rollback = one command named in runbook; claim + path/service scope exact; outside mutate window unless runbook `mw-ok`. **Auto-executable:** idempotent restarts of named known-good units, clear stale alert suppress, re-run probe, post learning. **Propose-only:** remux, deletes, compose recreate, credential/env flips, AutoFix that kills processes, anything novel / encode-class / disk cleanup. **Sideways:** SYSTEM Plex Monitor AutoFix false-positive → Restart-Plex as SYSTEM (Session-0 flake). Gate: AutoFix default false; block SYSTEM Restart-Plex — never autonomous "restart Plex" from a soft health lie.

**Q21 Knowledge injection (minimum wake):** fixer gets ONLY: alert fingerprint + probe receipt JSON; matched runbook id text (steps/rollback/verify); live claim/path-lock/SKIP_KEYS excerpt; latest 1–2 learnings for that fingerprint; thrift class + stand-down flag. Leave out: Discord scrollback, old sitreps, RAZ dumps, guessed root cause. Stay current: fix/refuse → learning; promote repeats → runbook/script; never a second wiki. **Wrong fix from missing context:** treating Docker Arr health as household SoT while native :8990/:7878 was real — context owed: **port/process map + which profile is live**.

**Q22 Prompt injection via logs:** (1) Fence: wrap log/ticket body as untrusted data; law never reads instructions from inside. (2) Sanitize: strip/escape `^(claim:|handoff:|ignore previous|system:|TOOL_|CHIP_|sudo )`; truncate; structured JSON probes preferred over raw paste. (3) Model sees the script-built summary, not raw Discord paste. (4) authorize() still required — injection can't mint `--execute`. (5) Cited "instruction" in a log → dead-letter + alert Ally once, never obey. Seen/expected: "restart all containers" / "disable stand-down" / fake `claim: ally …`. Likely miss: unicode lookalikes inside a legitimate field copied verbatim into system prompt — fix: never concatenate raw strings into law/tool instructions.

**Final (1 line):** Implement 19 (noise floor + SKIP_KEYS/dedup) first — without it self-heal amplifies false storms; still missed: graduated runbook registry (id → auto vs propose) + authorize() wired to monitoring fingerprints.
