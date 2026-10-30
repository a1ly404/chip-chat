# Agensi skills plan (draft — not approved)

Status: **draft for operator review.** Nothing has been listed, no Agensi account exists, and no new repository has been created. Every item under "Operator decides" below needs an explicit yes before work starts.

lumocheck: round-2 PASS (2026-10-30; two-pass lumo-max review; round-1 findings merged: single-source denylist, checker self-test, unconditional per-version approval, sandboxed dry-run, publish-leak runbook, support owner, phase 1 trimmed to 2 skills, GitHub-canonical-first)

## 1. Context

Agensi (agensi.io, operated by Agensi BV, Netherlands) reached out after seeing the agent instructions in this engine repo. They invited us to repackage them as `SKILL.md` skills, or to write new skills to sell. Listing is non-exclusive and free. The seller sets the price and keeps 70%.

This engine is **public and MIT-licensed**. The private plugin/operator repository is **out of scope for publication**. Patterns that exist only there may be *rewritten from scratch* as generic skills. Its text, hostnames, channel names, service names and identifiers are never copied.

## 2. Research findings (fetched 2026-10-30)

| Topic | Finding | Source |
|---|---|---|
| IP / licence | Creators keep full ownership. Agensi gets a non-exclusive, worldwide licence to display and deliver. Buyers get a personal, non-transferable licence and may not copy or export. Each paid load carries a licence notice with the order ID, and a DMCA process exists. | [ToS §8](https://www.agensi.io/terms) |
| Exclusivity | Non-exclusive. However, listings may not link to or upsell paid versions sold elsewhere (Gumroad, Stripe links, etc.). Links to our own docs or source repo are allowed. | ToS §6.3a |
| Visibility / copying | Skills are delivered only through their MCP connector, and agents "may not save skill files". In practice the SKILL.md text enters the buyer's agent context, so a determined buyer can copy it. Protection is contractual (licence notice plus DMCA), not technical. | ToS §5.2, §8.3 |
| "Repackaged free" | The seller page says **"Original work… Repackaged free skills get rejected."** Listing MIT text from this repo verbatim as a paid skill is likely to be rejected, and it is worthless anyway because the MIT copy is public. | [/sell](https://www.agensi.io/sell) |
| Security scan | Automated 8-point scan: file structure, file types, dangerous commands, secrets, env-var harvesting, network access, obfuscation, prompt injection. Each skill gets a 0–100 score, then an AI intent review and a human review (24–48 h). Updates without failed checks may auto-approve. | ToS §6.2, /sell |
| Refunds | Agensi offers a 30-day money-back guarantee if a skill "does not do what its page promises". The creator's share is clawed back. Sellers may not offer their own refund terms. Subscription skills must keep working, which is an ongoing support duty. | ToS §5.5, §6.3 |
| Takedown | Agensi may reject, remove or suspend listings at its sole discretion. It can change the revenue split with 30 days' notice. Paid skills get a ranking boost over free ones. | ToS §3.1, §6.2, §7.1 |
| Pricing / payouts | Minimum price $1. Skills under $5 are credit-only. Split is 70/30 on net (ex-VAT), with Agensi as merchant of record. Stripe Connect is available in US/CA/EEA/UK; USDC via Zoneless elsewhere. The payout threshold is $3.50 for credit sales. KYC is required. | ToS §6.4, §7.2; [payout docs](https://www.agensi.io/docs/earnings-and-payouts) |
| Packaging | A zip containing `SKILL.md` (required frontmatter: name, description) plus optional scripts, references and templates. One-time or subscription pricing; bundles are supported. | ToS §6.2, /sell |
| Comparable prices | Discord Bot (guidance only) $5, no reviews after 7 months. Telegram/Discord bot builder $1. Code Reviewer free. Advanced Code Review $15 (ships scanners, an eval harness and 18 references). Research Dispatcher $12. Dev Community Post Writer $9. | [discord-bot](https://www.agensi.io/skills/discord-bot), [advanced-code-review](https://www.agensi.io/skills/advanced-code-review), [code-reviewer](https://www.agensi.io/skills/code-reviewer) |
| Legitimacy | It is a real Dutch BV with detailed, recently updated ToS, Stripe as processor and a DMCA page. It is very young (ToS effective 2026-03, LinkedIn page lists one employee). The outreach contact joined in mid-2026 as "Partnerships & Growth". The site claims 16k skills and 15k users, while a LinkedIn post says roughly 400 verified creators. The ~65k visitors/month figure is unverified. No independent complaints were found, but there are also few independent reviews (one aggregator page, with an outdated 80% split). | [creator page](https://www.agensi.io/creators/joeri-brons-1), [review aggregator](https://topbusinesssoftware.com/products/Agensi/reviews/) |

| Scale (secondary) | Round-1 review cited an earlier founder Show HN with much smaller self-reported numbers (tens of creators, hundreds of skills, roughly one paying MCP subscriber at the time). I have not verified this. Plan for a small audience. | lumocheck r1 (unverified) |

**Assessment.** The money is small and uncertain. Comparable paid listings show no reviews, and the catalogue is crowded. The real value is **distribution and visibility for the open-source engine**. Listing free skills costs little. Paid skills carry refund and support obligations.

## 3. Recommendation

**Conditional GO, starting small: free skills first, with paid skills gated on traffic data.**

- **Yes:** publish **2** *original, generic* free skills. Mirror them to the public skills repo on GitHub first, so GitHub is the canonical copy. Then list them on Agensi as pure distribution, linking back to the engine.
- **Defer:** decide on a paid skill only after the phase-1 window. The **only** phase-2 candidate is the Chip Chat setup/ops skill, which adds value beyond the MIT docs: worked install, a troubleshooting tree, pack authoring and cost tuning.
- **No:** do not sell verbatim engine text. It is already MIT-licensed, likely to be rejected as "repackaged free", and cheap for anyone to copy.
- **No:** do not move any private repository content. Rewrite patterns from scratch only.

## 4. Proposed structure

A **new, separate repository**, suggested name `a1ly404/chip-chat-skills`. It is created only after the operator approves. It starts **private** while drafting; going public is a separate decision.

```
chip-chat-skills/
  README.md              # index, licence matrix, link to engine
  LICENSE                # MIT (covers free/ only)
  LICENSE-PROPRIETARY    # all-rights-reserved for paid/ (if any)
  .gitignore             # ignores scripts/denylist.txt, dist/*.zip, scratch/
  .gitleaks.toml
  CONTRIBUTING.md        # licence rules for contributions
  .github/PULL_REQUEST_TEMPLATE.md  # embeds the section 6 scrub checklist
  .github/CODEOWNERS
  .github/workflows/ci.yml   # gitleaks + denylist + frontmatter lint + check_skill self-test
  scripts/denylist.txt   # UNTRACKED; single source = CI secret, materialised to this path; CI fails if missing; matches masked in logs
  scripts/check_skill.py # frontmatter, size, kebab-case, no curl|sh / eval / env harvesting, denylist + regex scan
  scripts/testdata/      # known-bad fixtures (snowflake, RFC1918 IP, curl|sh, env harvest, bad frontmatter) the checker MUST catch
  scripts/build_zip.py   # deterministic zip on tag + SHA256 manifest
  free/<skill-name>/SKILL.md (+ references/, examples/, CHANGELOG.md)
  paid/<skill-name>/...  # only if phase 2 is approved; never pushed to a public remote
```

Each `SKILL.md` follows the open-standard frontmatter: `name` (kebab-case) and `description` ("Use when…"), plus a body, a "Known limitations" section and a version line.

### Licence choice

- **Free skills:** MIT, consistent with the engine. They can be listed on Agensi and also published on GitHub and other skill directories. Non-exclusive listing is compatible with this.
- **Paid skills:** proprietary, all rights reserved. They live in a private directory or repo and are **not** published under MIT anywhere. MIT content cannot be meaningfully sold: anyone can legally re-list or share it.
- A paid skill may *reference* the MIT engine. It must not *be* a copy of the MIT docs. Its value has to be new material such as troubleshooting trees, worked examples, scripts and evals.

## 5. Candidate skills

### From the public engine (generic already)

| Skill | One-liner | Tier | Price |
|---|---|---|---|
| `agent-operating-law` | Hard-law checklist for agents: BLOCKED on conflict, evidence-before-Done, dedupe-before-create, explicit GO tokens for irreversible actions | Free | – |
| `task-envelopes` | Structured handoff contracts (scope, acceptance, evidence type, owner, hop budget) for multi-agent work | Free | – |
| `llm-budget-modes` | Monthly spend cap with normal/thrift/stopped modes and a ledger pattern | Free (or bundle) | – |
| `chip-chat-setup-ops` | Install, pack authoring, Discord relay wiring, mock mode, budget tuning and a troubleshooting tree for Chip Chat | **Paid** (phase 2) | $9–15 one-time |

Phase plan (after round-1 review): phase 1 = `two-pass-plan-gate` + `token-thrift-ladder`. Phase 1.5 = `agent-operating-law`, only if phase 1 meets its gate. Phase 2 = `chip-chat-setup-ops` only. Every other row is **inventory, deferred**. In particular, the $5–7 mid-tier paid ideas are off the roadmap unless phase-1 data surprises us upward.

### Patterns to rewrite from scratch (names only; no private text reused)

| Skill | One-liner | Tier | Price |
|---|---|---|---|
| `two-pass-plan-gate` | Model-agnostic two-round plan review with a strict PASS/FAIL first line, no round 3, and a timeout-continue rule | Free (phase 1) | – |
| `token-thrift-ladder` | Prefer script, then CI, then cheap skill, then expensive agent; refuse open-ended digs without a GO | Free (phase 1) | – |
| `pre-pr-triage` | Mandatory triage before a PR: actionable / needs-repro / noise / blocked-env, with evidence plus a disproof attempt | Free or $5 | $5 |
| `approval-escalation-ladder` | Code-exec approval: cheap reviewer, then a stronger model, then a second-opinion model, then a human, batched once a day | Paid candidate | $7 |
| `merge-on-green` | PR merge discipline: required checks green, no force-push, draft until reviewed, trailer conventions | Free | – |
| `ticket-learnings` | Append a short `learning:` note after every ship or incident so other agents stay current | Free | – |
| `agent-activity-feed` | Verify-twice, cooldown-gated status feed into a chat channel; no agent turns triggered by the feed itself | Free | – |
| `scheduler-watchdog` | Cron/routine plus heartbeat watchdog patterns with dedupe and quiet hours | Free | – |
| `sandboxed-exec-ladder` | Tiered sandbox for agent code execution (dry-run, container, allowlisted host) | Paid candidate | $7 |
| `multi-agent-ops-bundle` | Bundle of plan gate + triage + escalation ladder + merge-on-green + learnings | Paid bundle (phase 2+) | $15–19 |

Prices follow comparables: guidance-only skills sit at $1–5 with weak traction, and tool-backed skills with evals at $12–15. Anything paid should ship scripts or evals, not prose alone.

### Excluded

These are explicitly excluded: anything mentioning private hosts, VMs, media stacks, household apps, VPN/torrent tooling, specific chat channel names, re-auth runbooks for personal accounts, or deploy topology.

## 6. Scrub process (every skill, every update)

1. **Write fresh** in the skills repo. Never copy files from the private repo. Engine text may be adapted, but paid skills need new material.
2. **Automated checks:** the `check_skill.py` self-test against `testdata/` must pass, and a failure blocks the pipeline. Then `gitleaks detect` on the full history. A denylist grep for private hostnames, IPs, user names, product and household terms and channel names; the list has a single source, a CI secret, as described in section 4. A regex check for Discord snowflakes (17–20 digits), webhook URLs, `*.ts.net`, RFC1918 IPs, emails and Windows user paths. A frontmatter lint. A scan for `curl|sh`, `eval` and env harvesting, mirroring Agensi's 8 checks.
3. **Manual review:** read the full diff line by line and confirm that examples use `acme-example`-style placeholders only.
4. **Operator approval:** each skill gets an explicit "approve <skill>@<version>", recorded in the tracking issue, before it is published anywhere. This is **unconditional**: a clean scan never waives it, even though Agensi may auto-approve updates.
5. CI builds the tagged zip and records its SHA256. The operator uploads that exact artifact. Agents never hold the Agensi account.

## 7. Extraction workflow

1. Pick a skill from §5, then open a tracking issue.
2. Write the spec: trigger description, steps, limitations and a test prompt.
3. Draft `SKILL.md` in a branch of the skills repo, then run §6 steps 2–3.
4. Dry-run it in two agents inside a **sandbox**: a disposable container or VM with no network, no real credentials in the environment, no access to the engine or private repos, and scrubbed test repos only. Record the environment and results in `examples/`.
5. Open a PR in the skills repo, then get operator review and approval (§6 step 4).
6. Tag `<skill>-vX.Y`. The operator uploads the zip manually.
7. **Support routing:** buyer questions and refund requests go to an operator-designated inbox and are triaged within 5 business days. Time spent is logged against the exit criterion.
8. Updates: same path. Agensi pushes the latest version to all buyers, so **an update is a publish**. Treat it with the same gate.

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Private details leak (hosts, channels, household names) | High: the repo is public and the skill goes to every buyer | §6 scrub, denylist CI, operator approval per version, private-first repo |
| Buyers copy paid SKILL.md text | Medium | Accept it. Sell value that grows (scripts, evals, updates), keep prices low, rely on the licence notice and DMCA |
| Rejected as "repackaged free" | Low to medium | Write original material; paid skills are never MIT text |
| MIT conflict | Medium | Free means MIT; paid means proprietary, kept separate; never dual-label the same text |
| Support and refund burden (30-day guarantee, subscriptions must keep working) | Medium | One-time pricing only, no subscriptions; clear limitations section; few paid skills |
| Agensi is young, can change terms or split, or remove listings at will | Medium | Non-exclusive; keep the canonical source in our repo; no off-platform-upsell links, as their ToS requires |
| A published listing contains a private term | High | **Publish-leak runbook:** delist immediately, assess buyer exposure, notify Agensi, write a postmortem, and halt publishes until the denylist root cause is fixed |
| A clean auto-scan passes a bad update | High | Human per-version approval is unconditional |
| Denylist drifts between local and CI copies | Medium | Single source (CI secret); CI fails if it is missing; self-test fixtures |
| Unverified traffic claims (platform likely smaller than marketed) | Medium | Phase 1 measures first-use notifications before any paid work |
| Ongoing maintenance drains agent budget | Medium | Script-first checks; batch updates monthly; refuse open-ended work without operator GO |
| Tax/KYC and USDC payout handling | Low | Stripe Connect (CA is supported); the operator handles KYC |
| Brand confusion with the public engine | Low | README states the engine stays free/MIT; skills are optional companions |

## 9. Phased rollout

- **Phase 0 (operator decisions, no code):** answer §10 and verify what analytics the creator dashboard exposes. If yes, create the private `chip-chat-skills` repo with full CI scaffolding, including the self-test fixtures. Scaffolding lands through a PR.
- **Phase 1 (2 free skills, at most 1 week of drafting each, then a 30-day window):** `two-pass-plan-gate` and `token-thrift-ladder`, both MIT. Publish them on GitHub first, then list on Agensi after operator approval. The gate threshold is set from the verified dashboard analytics. If there are no analytics, the gate uses inbound signals only (issues, stars, messages). If the gate is missed, stop at free-only.
- **Phase 2 (paid Chip Chat setup/ops skill):** only if phase 1 succeeds. Proprietary licence, one-time price of $9–15. It ships a troubleshooting tree, a worked pack example, a selftest walkthrough and a cost-tuning script. Consider `multi-agent-ops-bundle` afterwards.
- **Exit criteria:** delist and keep the repo if Agensi changes terms adversely, support exceeds about 1 hour a month, or a scrub incident occurs. Any publish-leak means an **immediate delist**.

## 10. Operator decides

1. Go or no-go on Agensi at all, including whether to create the creator account and do KYC/Stripe Connect (the operator does this personally; agents never do).
2. Whether to create `a1ly404/chip-chat-skills` (name OK?), and whether it starts private or public.
3. The licence split: MIT for free skills and proprietary for paid (yes/no).
4. Which 2 phase-1 free skills to write (recommended: `two-pass-plan-gate` and `token-thrift-ladder`).
5. Whether to rewrite private-repo *patterns* from scratch as generic skills at all (each one named and approved).
6. Phase-2 paid skill: yes/no, the price, and one-time pricing (recommended) versus subscription.
7. Whether to mirror free skills to GitHub or other directories as well.
8. Whether and how to reply to the Agensi outreach (draft only; the operator sends it).
9. Per-skill, per-version publish approval (standing rule).
10. Budget: the maximum agent time per month for skill maintenance.
11. Verify the creator-dashboard analytics before phase 1, and set the numeric gate from that data.
12. Name the support inbox or owner for buyer questions and refunds.
13. Add a cross-link from the engine README to the skills repo, stating that the engine stays free/MIT (later, and only if the skills repo goes public).

Draft only — not listed, no account, no contact with Agensi.
