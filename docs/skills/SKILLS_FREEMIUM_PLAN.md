# Chip Chat skills: free runner, paid set-up/ops (option 5, hybrid) — rev 2

Status: **plan for operator review.** Draft only. No repo, no account, no listing, no contact with Agensi.

Supersedes the product shape in `AGENSI_SKILLS_PLAN.md` §5. That file remains the marketplace/ToS research.
The publish gates it describes are **restated inline in §5** so this plan is self-contained.

## 0. Reviewer scope note

The lumocheck brief is written for the Razzorstack media stack. Its media-specific constraints (CIFS/SMB,
`*arr` COPY, remux, dual MKV+MP4) **do not apply** to this artifact. What does apply, and is enforced below:

- work lands on a **branch + PR**, never as live-tree-only "done";
- no secret is ever printed, and the CLI design must not normalise plaintext credentials;
- no unsupervised host mutation: the free CLI never executes model output and never merges on its own.

Operator to confirm this re-scope (decision 10).

## 1. Shape

| Tier | What the buyer gets | Licence | Repo | Price |
| --- | --- | --- | --- | --- |
| **Free** | `SKILL.md` + a small self-contained CLI: cheapest model per step, plan twice, small PR, merge only on the user's CI. Local cost ledger, hard spend cap, thrift mode. | MIT | `a1ly404/chip-chat-skills` (public) | $0 |
| **Paid** | `chip-chat-setup-ops`: install and run the full engine (Docker, pack authoring, Discord relay, approval ladder, scheduler, activity feed) + troubleshooting tree, cost-tuning and doctor tooling. | Proprietary | `a1ly404/chip-chat-ops` (private, no public remote) | $9–15 one-time |

Why this shape:

- The free tier is useful **alone**, inside whatever agent the user already runs (Claude Code, Cursor,
  Copilot, Codex). No Docker, no Discord, no account. That is what earns distribution.
- The paid tier sells work that is **absent from the MIT repo** — install, packs, relay, approvals,
  scheduler, troubleshooting — so it is defensible and not "repackaged free".
- The upgrade is a real technical step, not a nag: the free tier cannot schedule, hold approvals across
  sessions, stay online, or run code in a sandbox.
- Option 4 (send work to a relay the user already runs) stays out of v1: its audience is existing Chip Chat
  users only. Revisit after the paid tier has buyers.

**Two repositories, deliberately.** Free (MIT, public) and paid (proprietary, private) never share history
or a remote, so a `push --mirror` mistake cannot publish paid text. This is the structural fix, not a CI
patch.

## 2. Free tier

### 2.1 Skill body

`SKILL.md`, frontmatter `name: cheap-change-runner`,
`description: Use when the user asks for a code change and wants the cheapest model that can do each step,
small PRs, and merge only on green CI.` Body, in order:

1. Restate the request as a one-line acceptance test; refuse to start if it cannot be restated.
2. Draft a plan with the **cheap** tier; check it once with the **cheap** tier against a fixed checklist
   (missing files, unsafe step, simpler path). No third round.
3. One approval message from the user, not a conversation.
4. Change on a branch, smallest reviewable slice, **mid** tier for edits.
5. PR; never force-push; never merge unless the repo's checks are green **and** `--merge-on-green` was passed
   on this invocation.
6. Print the ledger line at the end of every run.

### 2.2 CLI

Self-contained (stdlib + `urllib`), no engine install, ships inside the skill zip:

```
chip-run plan    "add request-id middleware"   # cheap tier -> .chip/plan.md
chip-run check   .chip/plan.md                 # cheap tier second pass, PASS/FAIL + gaps
chip-run apply   .chip/plan.md                 # mid tier edits, branch, commit, push, PR
chip-run watch   --pr 42                       # polls CI, reports; merges only with --merge-on-green
chip-run spend   [--month 2026-10]             # ledger summary per step and per tier
chip-run doctor                                # key presence, git state, remote, CI reachable, key-file mode
chip-run mode    thrift|normal|stopped
```

Config `.chip-run.toml` (user-owned):

```toml
[models]
cheap  = "openrouter:<cheap-model-id>"    # classification, plan, plan-check, summaries
mid    = "openrouter:<mid-model-id>"      # code edits
strong = "openrouter:<strong-model-id>"   # only after a recorded escalation

[budget]
monthly_cap_usd = 10.0
thrift_at       = 0.80
hard_stop       = 1.00
max_escalations = 2

[repo]
merge_policy = "ci-green"                 # eligibility only; never triggers a merge
```

Design rules:

- **Tier names, never hardcoded model ids** in the skill body. `models.toml.example` carries current
  examples; refreshing it happens only through skill releases (bundled manifest, no background network call).
  Owner and cadence in decision 11.
- **Credentials: environment variable first** (`CHIP_RUN_API_KEY`). A key file at
  `~/.config/chip-run/key` is a fallback only, must be mode `0600`, and `doctor` warns when the mode is
  looser. The key is never written to the ledger and never echoed.
- **Redaction is best-effort and says so.** Scan every request body for a conservative high-entropy
  heuristic plus a denylist of known token prefixes (e.g. `sk-`, `ghp_`, JWT shape). Record the redaction
  count in the ledger. `README`/`SECURITY.md` state plainly that this is not a secrets boundary.
- **No telemetry, no account, no phone-home.** The only outbound calls are to the configured model provider.
- **Escalation is explicit and logged.** cheap→mid→strong writes a reason; past `max_escalations` the run
  stops and asks the user.
- **Budget:** normal → thrift at the threshold (refuse strong tier, shrink context) → hard stop (refuse live
  calls, print the reset path). Never silently overspend.
- **Repo safety:** refuse a dirty tree, refuse to push to the default branch, never force-push, never run
  arbitrary host commands from model output.
- **Artifacts outside the user's diff:** plans, ledgers and state go to the OS cache directory by default
  (`%LOCALAPPDATA%` / `$XDG_CACHE_HOME`); if the user opts into in-repo `.chip/`, the tool writes a
  `.gitignore` fragment so plans and ledgers cannot ride along in a PR.
- **Merge precedence:** the flag must be passed per invocation. `merge_policy` in config makes the repo
  *eligible*; it never merges. `watch` also refuses while the host platform reports unresolved review
  threads.
- **Windows + Linux paths** handled explicitly (no shelling out; `pathlib`; documented in `examples/`).

### 2.3 Freemium boundary (goes in the README)

Free tier cannot: schedule or stay online; hold multi-step approvals or a second-opinion ladder; talk to
Discord or run personas/rooms/packs; execute code in a sandbox; remember across runs beyond the local ledger.
Those are the paid tier's job, stated up front so nobody feels cheated.

## 3. Paid tier — `chip-chat-setup-ops`

Proprietary, one-time, $9–15 (recommend $12). New material, written fresh in the private repo:

| Piece | Why it is worth money |
| --- | --- |
| Install path | Docker/compose bring-up for relay + scheduler, env-file handling, health checks, symptom → cause → fix |
| Pack authoring | Personas, rooms, channel law, tools manifest, `CHIP_PACK_DIR`, pack vs engine |
| Relay wiring | One webhook per room, `username=` per persona, wake prefixes, delegate rules, hop cap |
| Approval ladder | cheap reviewer → stronger model → second opinion → human, batching, fail-closed rule |
| Spend control | Monthly cap, thrift mode, ledger on the container volume, `chip spend` / `chip ping` |
| Troubleshooting tree | Healthy-but-silent relay, `Created` container, webhook 404, missing pack, spend cutoff, provider auth expiry |
| Companion scripts | `doctor.py`, `pack_lint.py`, `cost_report.py`, `selftest.py` (proprietary) |
| Support runbook | Refund steps and 5-business-day triage templates (private) |
| Worked example | One end-to-end pack with placeholder identity, a failing case and its receipt |

Licence rule: paid **references** the public engine and never contains MIT text. Free = MIT, paid = all
rights reserved, never dual-labelled. Engine release pin: a tagged release if one exists, otherwise a
documented green strict-export SHA.

## 4. Repos and CI

```
chip-chat-skills/                 # public, MIT, free tier only
  README.md  LICENSE  SECURITY.md  PRIVACY.md  CONTRIBUTING.md  UPGRADE.md
  free/cheap-change-runner/
    SKILL.md
    scripts/chip_run.py           # single file, stdlib + urllib
    scripts/models.toml.example
    tests/                        # unit tests, mocked HTTP
    examples/                     # fixture tasks, recorded cost comparisons, Windows notes
    CHANGELOG.md
  scripts/check_skill.py  scripts/testdata/  scripts/build_zip.py  scripts/denylist.txt
  .gitleaks.toml  .github/workflows/ci.yml
  .github/PULL_REQUEST_TEMPLATE.md  .github/CODEOWNERS

chip-chat-ops/                    # private, proprietary, paid tier only
  SKILL.md  scripts/{doctor,pack_lint,cost_report,selftest}.py
  runbook/{refunds,triage}.md  examples/  CHANGELOG.md
  LICENSE-PROPRIETARY
```

Semver for the skill zip (`<skill>-vX.Y.Z`), linked from `CHANGELOG.md`.

## 5. Publish gates (inlined, every skill, every update)

1. `check_skill.py` self-test against bad fixtures passes (snowflake, RFC1918, `curl|sh`, env harvest, bad
   frontmatter).
2. `gitleaks detect` on full history.
3. Denylist grep: private hostnames, RFC1918, snowflakes, webhook URLs, household/product terms, channel
   names. Single source (CI secret), CI fails if missing, matches masked in logs.
4. Manual line-by-line read; examples use `acme-example`-style placeholders only.
5. Sandboxed dry run: disposable container, no network, no real keys, toy repo; environment and result
   recorded in `examples/`.
6. Operator approval `approve <skill>@<version>` recorded in the tracking issue. Unconditional — a clean
   scan never waives it.
7. CI builds the tagged zip + SHA256; the operator uploads that exact artifact by hand. Agents never hold
   the Agensi account.

Publish-leak runbook: delist immediately → assess buyer exposure → notify Agensi → postmortem → halt
publishes until root cause fixed.

## 6. Phases

Every phase's "done when" includes **branch + PR + review + merge** in the owning repo. No live-tree done.

| Phase | Work | Timebox | Done when |
| --- | --- | --- | --- |
| 0 | Operator answers §9 | 0.5 day | decisions written; repo names approved |
| 1 | Scaffold public repo + CI + checker self-test | 1–2 days | CI green on a dummy skill; bad fixtures rejected |
| 2 | Free skill + `chip_run.py` + `doctor`/`spend`/`mode` | 5–7 days | unit tests green; 6 fixture tasks pass |
| 3 | Eval vs single-model baseline | 1 day | ≥30% cost cut with blind-rated equal/better diffs |
| 4 | Scrub + sandbox dry run + free listing | 2 days | approval recorded; GitHub canonical copy public; listing live |
| 5 | **30-day measurement window** | 30 days | §7 gate met or missed |
| 6 | *Conditional*: paid skill + scripts + runbook | 5 days | paid dry run passes; price set; draft listing |
| 7 | *Conditional*: paid launch + 30-day review | 30 days | continue / delist decision |

Framing rule: **phases 0–5 are the shippable plan**; 6–7 are opportunistic and only start if §7 is met.
Nothing in 6–7 is on the critical path to a useful free skill.

## 7. Gates

**Free launch (all three):**

1. 6 fixture tasks pass on the repo's own fixture CI **and** on a fresh machine (Windows native + one Linux
   container) from the zip alone.
2. Routed cost ≥30% below the same tasks on a single mid-tier model, with **blind-rated** diffs equal or
   better.
3. Clean scrub per §5.

**Paid tier (two of three, measured after 30 days):**

- ≥ 40 first-use notifications/installs on the Agensi dashboard;
- ≥ 5 inbound signals (issues, stars, messages) asking about scheduling, chat or setup;
- ≥ 3 explicit "how do I run this as a service / with Discord".

**If the dashboard exposes no analytics, the gate reduces entirely to inbound signals at a bar of 8** and
the other two legs are void — not "two of three with one missing".

Paid success after 30 days: ≥ 3 sales, support ≤ 1 h/month, no refund wave. Otherwise delist, keep both repos.

## 8. Risk

Expected money is small (comparables $1–15, thin reviews, young platform). Revenue is upside; the return is
distribution for the free engine and a clean engine release.

| Risk | Mitigation |
| --- | --- |
| Listed as "repackaged free" | Original prose + a CLI absent from the engine; paid adds absent material |
| **Licence leak via one repo** | **Two repos, no shared history or remote** |
| Buyer copies paid text | Accepted; sell updates/scripts/support, keep price low, licence + DMCA |
| Model-id churn | Tier indirection, bundled refresh manifest, `doctor` |
| Key handling repels buyers | Env first, 0600 fallback + warning, no telemetry, key never logged |
| CLI damages a user repo | Dirty-tree refusal, no default-branch push, no force-push, explicit merge flag |
| Scrub leak | §5 gates, per-version approval, leak runbook |
| Support/refund load | One-time pricing, limitations section, 1 h/month cap, 5-day triage |
| Agensi ToS changes | Non-exclusive, canonical copy on GitHub, no off-platform payment links |
| No engine tag yet | Pin tagged release, else documented green strict-export SHA |

## 9. Operator decisions

1. Approve the shape (free runner + paid set-up/ops) and the MIT/proprietary split.
2. Approve **two repos**: `a1ly404/chip-chat-skills` (public, free) and `a1ly404/chip-chat-ops` (private, paid).
3. Free = MIT, paid = all rights reserved — yes/no.
4. Paid price: $9 / $12 / $15 one-time (recommend **$12**).
5. OpenRouter-only in v1, local/Ollama in 1.1 — yes/no.
6. Analytics gate numbers in §7 once you have seen the dashboard.
7. Support owner/inbox.
8. May the free listing mention the paid companion (ToS wording check first)?
9. Two-repo split confirmed.
10. Confirm the lumocheck re-scope note in §0 (media constraints do not apply; delivery discipline does).
11. `models.toml.example` refresh owner + cadence.

## 10. Not in v1

Hosted service, accounts, telemetry, auto-merge by default, option 4 relay bridge, subscriptions,
multi-agent orchestration, any private-repo content.

## 11. Round-1 findings applied

- Scope mismatch recorded and answered (§0).
- Branch+PR delivery made a phase rule (§6).
- Launch gate reworded so public listing is not gated on our CI (§7).
- Key storage hardened: env first, 0600 file fallback, `doctor` mode check (§2.2).
- Redaction specified and honestly bounded (§2.2, README/SECURITY).
- `models --refresh` given a network-free definition (§2.2, decision 11).
- Same-repo licence mix removed: two repos (§1, §4, §8).
- `.chip/` artifacts moved out of the user's diff by default (§2.2).
- Missing files added: unit tests, SECURITY/PRIVACY/CONTRIBUTING, semver, support runbook, Windows notes (§4).
- Merge precedence and review-thread check defined (§2.2).
- Gate arithmetic edge case fixed: no-analytics path is inbound-only (§7).
- Reused publish gates restated inline (§5).
- Blind-rated quality equality in the eval gate (§7).
- Free tier reframed as the terminal milestone; paid phases marked conditional (§6).

---

Draft only. No account, no listing, no contact with Agensi, no skills repo created.
