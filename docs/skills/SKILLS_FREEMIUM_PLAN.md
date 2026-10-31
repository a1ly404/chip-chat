# Chip Chat skills: free runner, paid set-up/ops (option 5, hybrid) — rev 3

Status: **approved plan, implementation starting.** No Agensi account, no listing, no contact with Agensi.

Supersedes the product shape in `AGENSI_SKILLS_PLAN.md` §5. That file remains the marketplace/ToS research;
the publish gates it describes are restated inline in §5 so this plan is self-contained.

## 0. Scope note

The lumocheck brief is written for the Razzorstack media stack. Its media-specific constraints (CIFS/SMB,
`*arr` COPY, remux, dual MKV+MP4) do not apply here. What does apply, and is enforced below: branch + PR
delivery, no printed secrets, and **no unsupervised host mutation** — in v1 the CLI cannot merge, cannot
execute model output, and cannot touch a default branch.

## 1. Shape

| Tier | What the buyer gets | Licence | Repo | Price |
| --- | --- | --- | --- | --- |
| **Free** | `SKILL.md` + a small self-contained CLI: cheapest model per step, plan twice, small PR, CI verdict reported. Local cost ledger, hard spend cap, thrift mode. Merging is a **human-only** command. | MIT | `a1ly404/chip-chat-skills` (public) | $0 |
| **Paid** | `chip-chat-setup-ops`: install and run the full engine (Docker, pack authoring, Discord relay, approval ladder, scheduler, activity feed) + troubleshooting tree, cost-tuning and doctor tooling. | Proprietary | `a1ly404/chip-chat-ops` (private, no public remote) | **$12 one-time** |

Why this shape:

- The free tier is useful **alone**, inside whatever agent the user already runs (Claude Code, Cursor,
  Copilot, Codex). No Docker, no Discord, no account. That is what earns distribution.
- The paid tier sells work **absent from the MIT repo** — install, packs, relay, approvals, scheduler,
  troubleshooting — so it is defensible and not "repackaged free".
- The upgrade is a technical step, not a nag: the free tier cannot schedule, hold approvals across sessions,
  stay online, or sandbox-exec.
- Option 4 (bridge to a relay the user already runs) stays out of v1: its audience is existing Chip Chat
  users only.

**Two repositories.** Free (MIT, public) and paid (proprietary, private) never share history or a remote, so
a `push --mirror` mistake cannot publish paid text. Structural fix, not a CI patch.

## 2. Free tier

### 2.1 Skill body

`SKILL.md`, frontmatter `name: cheap-change-runner`,
`description: Use when the user asks for a code change and wants the cheapest model that can do each step,
small PRs, and a clear CI verdict.` Body, in order:

1. Restate the request as a one-line acceptance test; refuse to start if it cannot be restated.
2. Draft a plan with the **cheap** tier; check it once with the **cheap** tier against a fixed checklist
   (missing files, unsafe step, simpler path). No third round.
3. One approval message from the user, not a conversation.
4. Change on a branch, smallest reviewable slice, **mid** tier for edits.
5. Open the PR. Never force-push, never push to the default branch, **never merge**. The run ends by
   printing the PR URL, the final CI verdict, and the ledger line.
6. **Never run `chip-run merge`.** If the user wants a merge, print the exact command for them to run.
   Merge is a human keystroke by design.

### 2.2 CLI

Self-contained (stdlib + `urllib`), no engine install, ships inside the skill zip:

```
chip-run plan    "add request-id middleware"   # cheap tier -> plan file
chip-run check   <plan>                         # cheap tier second pass: exit code + gap list
chip-run apply   <plan>                         # mid tier edits, branch, commit, push, PR; prints PR URL
chip-run status  --pr 42                        # read-only CI verdict (anonymous API, rate-limit fallback)
chip-run merge   --pr 42                        # HUMAN ONLY: interactive, verifies green, merges
chip-run spend   [--month YYYY-MM]              # ledger summary per step and per tier
chip-run doctor                                 # key presence/mode, git state, remote, CI reachability
chip-run mode    thrift|normal|stopped
```

Config `.chip-run.toml` (user-owned):

```toml
[models]
cheap = "openrouter:<cheap-model-id>"   # classification, plan, plan-check, summaries
mid   = "openrouter:<mid-model-id>"     # code edits

[budget]
monthly_cap_usd = 10.0
thrift_at       = 0.80
hard_stop       = 1.00

[repo]
base_branch = "main"
```

No `merge_policy` key: policy lives in the human, not in config.

Design rules:

- **Tier names, never hardcoded model ids** in the skill body. `models.toml.example` carries current
  examples; refreshes happen only through skill releases (bundled manifest, no background network call).
- **Credentials: environment variable first** (`CHIP_RUN_API_KEY`). A key file at
  `~/.config/chip-run/key` is fallback only, mode `0600`, and `doctor` warns when looser. The key is never
  written to the ledger and never echoed.
- **GitHub credentials are not handled by the CLI for planning or editing.** `apply` uses the user's ambient
  git credentials (SSH agent or `gh auth`). Only `merge` reads `CHIP_RUN_GITHUB_TOKEN`, and only when the
  human invokes it.
- **`merge` is not automatable in v1.** It requires an interactive TTY, prints the PR title and diffstat,
  requires the human to type the PR number, verifies CI green and no unresolved review threads, and refuses
  `--yes`/non-interactive invocation. There is no agent-invocable merge anywhere.
- **`status` is read-only** and unauthenticated where possible; on rate limit it prints "open the PR page".
- **v1 escalation is cheap→mid only.** A condition that would need a "strong" model stops the run and asks
  the user. Strong tier is a v1.1 feature with its own risk review.
- **Budget checks precede every live call, including escalations.** A refused escalation consumes no quota
  and is logged with a reason. Ledger writes take an **OS-level file lock**; concurrent runs serialize and
  re-read the cap before each request.
- **Redaction is best-effort and says so.** Scan every request body for a conservative high-entropy
  heuristic plus known token prefixes; record the redaction count. `SECURITY.md` states this is not a
  secrets boundary.
- **Privacy leads with transmission, not redaction.** `PRIVACY.md` opens with: prompts, code excerpts and
  filenames are sent to the configured model provider; no local processing boundary exists.
- **No telemetry, no account, no phone-home.** Only outbound calls go to the configured model provider.
- **Repo safety:** refuse a dirty tree, refuse to push to the default branch, never force-push, never run
  arbitrary host commands from model output.
- **Artifacts outside the user's diff:** plans, ledgers and state go to the OS cache directory
  (`%LOCALAPPDATA%` / `$XDG_CACHE_HOME`) by default; if the user opts into in-repo `.chip/`, the tool writes
  a `.gitignore` fragment so plans and ledgers cannot ride along in a PR.
- **Windows + Linux paths** handled explicitly (`pathlib`, no shelling out; documented in `examples/`).

### 2.3 Freemium boundary (goes in the README)

Free tier cannot: schedule or stay online; hold multi-step approvals or a second-opinion ladder; talk to
Discord or run personas/rooms/packs; execute code in a sandbox; remember across runs beyond the local ledger;
**merge without a human at the keyboard**. Those are the paid tier's job, stated up front.

## 3. Paid tier — `chip-chat-setup-ops`

Proprietary, one-time, **$12**. New material, written fresh in the private repo:

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
rights reserved, never dual-labelled. Engine pin: a tagged release if one exists, otherwise a documented
green strict-export SHA.

**Support window (bounded):** setup support and updates for **12 months or two engine major versions**,
whichever ends first; after that, best-effort. Stated in the listing text.

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
  scripts/check_skill.py  scripts/testdata/  scripts/build_zip.py
  scripts/denylist.example.txt    # placeholders only, never the real list
  .gitleaks.toml  .github/workflows/ci.yml
  .github/PULL_REQUEST_TEMPLATE.md  .github/CODEOWNERS

chip-chat-ops/                    # private, proprietary, paid tier only
  SKILL.md  scripts/{doctor,pack_lint,cost_report,selftest}.py
  runbook/{refunds,triage}.md  examples/  CHANGELOG.md
  LICENSE-PROPRIETARY
```

The real denylist exists **only** as a CI secret; the public tree ships `denylist.example.txt` with category
placeholders (`<internal-hostname-pattern>`, `<rfc1918>`, `<household-term>`). Publishing real entries to
protect against leaks would be the leak.

Semver for the skill zip (`<skill>-vX.Y.Z`), linked from `CHANGELOG.md`.

## 5. Publish gates (inlined, every skill, every update)

1. `check_skill.py` self-test against bad fixtures passes (snowflake, RFC1918, `curl|sh`, env harvest, bad
   frontmatter).
2. `gitleaks detect` on full history.
3. Denylist gate: the CI-secret list is loaded and verified **against the categories** in
   `denylist.example.txt`; a missing secret or an uncovered category fails CI. Matches are masked in logs.
4. Manual line-by-line read; examples use `acme-example`-style placeholders only.
5. Sandboxed dry run: disposable container, no network beyond the configured provider, no real keys, toy
   repo; environment and result recorded in `examples/`.
6. Operator approval `approve <skill>@<version>` in the tracking issue. Unconditional, per version —
   including `models.toml.example` refreshes.
7. CI builds the tagged zip + SHA256; the operator uploads that exact artifact by hand. Agents never hold
   the Agensi account.

Publish-leak runbook: delist immediately → assess buyer exposure → notify Agensi → postmortem → halt
publishes until root cause fixed.

## 6. Phases

Every phase's "done when" includes **branch + PR + review + merge** in the owning repo.

| Phase | Work | Timebox | Done when |
| --- | --- | --- | --- |
| 0 | Operator decisions | 0.5 day | decisions recorded (§9) |
| 1 | Scaffold public repo + CI + checker self-test | 1–2 days | CI green on a dummy skill; bad fixtures rejected |
| 2 | Free skill + `chip_run.py` (plan/check/apply/status/merge/spend/doctor/mode) | 7–10 days | unit tests green; ≥20 fixture tasks pass |
| 3 | Eval vs single-model baseline | 2 days | ≥20 tasks, ≥30% **median** cost cut, two blind raters equal/better on a written rubric |
| 4 | Scrub + sandbox dry run + free listing | 2 days | approval recorded; GitHub canonical copy public; listing live |
| 5 | **30-day measurement window** | 30 days | §7 gate met or missed |
| 6 | *Conditional*: paid skill + scripts + runbook | 5 days | paid dry run passes; price set; draft listing |
| 7 | *Conditional*: paid launch + 30-day review | 30 days | continue / delist decision |

Framing: phases 0–5 are the shippable plan. Phases 6–7 are opportunistic and start only if §7 is met.

## 7. Gates

**Free launch (all three):**

1. ≥20 fixture tasks pass on the repo's own fixture CI **and** on a fresh machine (Windows native + one
   Linux container) from the zip alone, with the operator merging manually.
2. Routed median cost ≥30% below the same tasks on a single mid-tier model, with **two independent blind
   raters** scoring routed diffs equal or better on a written rubric (correctness, completeness, style
   parity); a disagreement gets a third read and per-task costs are reported, not just the aggregate.
3. Clean scrub per §5.

**Paid tier (two of three, measured after 30 days):**

- ≥ 40 first-use notifications/installs on the Agensi dashboard;
- ≥ 5 inbound signals (issues, stars, messages) asking about scheduling, chat or setup;
- ≥ 3 explicit "how do I run this as a service / with Discord".

**If the dashboard exposes no analytics, the gate reduces entirely to inbound signals at a bar of 8** and
the other two legs are void.

Paid success after 30 days: ≥ 3 sales, support ≤ 1 h/month, no refund wave. Otherwise delist, keep both repos.

## 8. Risk

Expected money is small (comparables $1–15, thin reviews, young platform). Revenue is upside; the return is
distribution for the free engine and a clean engine release.

| Risk | Mitigation |
| --- | --- |
| Listed as "repackaged free" | Original prose + a CLI absent from the engine; paid adds absent material |
| Licence leak via one repo | Two repos, no shared history or remote |
| Denylist itself leaks | Real list is a CI secret; public tree has placeholders only |
| Agent-invoked merge of its own diff | v1 has **no** agent-invocable merge; `merge` is interactive and human-only |
| CLI damages a user repo | Dirty-tree refusal, no default-branch push, no force-push |
| Two runs race the spend cap | OS-level ledger lock; budget re-checked before every live call |
| Prompt injection in a model-authored diff | No automated post-PR action; worst case is a bad diff the human reviews |
| Buyer expects lifetime support | Support/update window stated: 12 months or two engine majors |
| Model-id churn | Tier indirection, bundled refresh manifest, `doctor` |
| Key handling repels buyers | Env first, 0600 fallback + warning, no telemetry, key never logged |
| Scrub leak | §5 gates, per-version approval, leak runbook |
| Agensi ToS changes | Non-exclusive, canonical copy on GitHub, no off-platform payment links |
| No engine tag yet | Pin tagged release, else documented green strict-export SHA |

## 9. Decisions

**Resolved 2026-10-30:** shape (free runner + paid set-up/ops); two repos; MIT / all-rights-reserved split;
**$12 one-time**; OpenRouter-only in v1 (local/Ollama in 1.1); merge is human-only in v1; lumocheck re-scope.

**Open (not blocking the free tier):**

1. Analytics gate numbers in §7, once the creator dashboard is visible.
2. Support owner/inbox.
3. May the free listing mention the paid companion (ToS wording check first)?
4. `models.toml.example` refresh owner + cadence.
5. Confirm the paid support window wording (12 months or two engine majors).

## 10. Not in v1

Hosted service, accounts, telemetry, agent-invocable merge, option 4 relay bridge, subscriptions,
multi-agent orchestration, strong tier, any private-repo content.

## 11. Review findings applied

**Round 1 (first pass):** scope mismatch recorded; branch+PR phase rule; launch gate no longer depends on
our CI; env-first key handling with 0600 fallback; redaction specified and bounded; network-free model
manifest; two-repo split; `.chip/` artifacts out of the diff; unit tests + SECURITY/PRIVACY/CONTRIBUTING +
semver + runbooks + Windows notes; merge precedence defined; no-analytics gate leg made total; publish
gates inlined; blind-rated quality; paid phases marked conditional.

**Round 1 (second pass, on the priced text):** denylist moved out of the public tree (CI secret + example
placeholders); GitHub credential surface defined and narrowed; automated merge removed — `merge` is now
interactive and human-only; eval scaled to ≥20 tasks with a rubric and two blind raters; ledger file lock and
budget-before-escalation precedence; paid support window bounded; privacy leads with third-party
transmission; decisions renumbered; phase-2 estimate corrected to 7–10 days; strong tier deferred to v1.1.

---

Implementation starts at phase 1. No account, no listing, no contact with Agensi.
