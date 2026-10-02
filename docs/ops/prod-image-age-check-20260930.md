# Prod image staleness check — chip-relay (audit receipt 2026-09-30)

- Issued: 2026-09-30 ~21:15Z · Operator: OCgo (cc audit block)
- Scope: container/image inspection + git correlation. Read-only. No deploys.

## Verbatim inspection outputs

```
$ docker inspect chip-chat-chip-relay-1 --format '{{.Image}}'
sha256:4685d1c89a7f9b51ceec536d7af5fa9a43e970528bfe5c3eb891f3405359af02
$ docker inspect --format '{{.Id}} {{.Created}}' sha256:4685d1c89a7f…af02
sha256:4685d1c89a7f…af02 2026-09-29T05:50:23.73418794Z
$ docker inspect chip-chat-chip-relay-1 --format '{{.State.StartedAt}}'
2026-09-29T05:50:30.059673565Z
$ docker exec chip-chat-chip-relay-1 sh -c 'cat /app/BUILD_SHA.txt 2>/dev/null || echo NO_BUILD_SHA_IN_CONTAINER'
NO_BUILD_SHA_IN_CONTAINER
$ cat /mnt/c/Users/17783/chip-chat/BUILD_SHA.txt   # Hyper-V deploy tree stamp
86a1ad5-merged95
$ git log --format='%h %cI %s' -1 86a1ad5
86a1ad5 2026-09-28T22:49:48-07:00 Merge pull request #95 from a1ly404/chipchatdev/lane-amendment
$ git rev-list --count --merges 86a1ad5..origin/main
21
$ git rev-list --count --no-merges 86a1ad5..origin/main
46
```

## Finding
- Prod relay container binds image built **2026-09-29T05:50:23Z**; deploy-tree
  stamp says it contains **`86a1ad5` (PR #95, merged 2026-09-29T05:49:48Z** ==
  2026-09-28 22:49 PT — consistent with the build timestamp 74s later).
- `origin/main` = `dc2d851` (PR #135, merged 2026-09-30T18:11:18Z).
- **Prod is 21 merges / 46 non-merge commits behind main.**

## Merges on main not in prod (newest first)

```
dc2d851 #135 voice-law            a9a397e drop Done= / PM lane orders from channel law  ← LAW CONTENT
9560658 #134 ocgo/usd-cents       43681d4 monthly-USD display law to cents (WARN emit, standup fact, cutoff ".2f")  ← CODE
f480549 #133 ocgo/escalation-docs 65882ed WG-pivot landscape addendum (docs)
e443bc4 #131 ocgo/corrlog         fb04aec RAZ-335 port-death correlation log (docs)
b193a35 #130 ocgo/escalation-docs 6c3be86 RAZ-335 escalation packet + port-health receipts (docs)
0781746 #129 scrub/go-token-redact 6f93b2f burned-GO-token scrub (docs)
68cbb25 #128 fix-124-tests        af027ac harden #124 critique test contracts (tests)
4152830 #125 pm/churn-post-merge  (docs + ci retrigger)
f07d634 #127 ocgo/pin-repair      19b406d ci pin-list repair (209f6a6 merged pre-repair)
209f6a6 #124 ocgo/wip-adversarial f38bdd0… test(collab) WIP adversarial coverage (tests)
b3df7d3 #126 ocgo/ops-ab-run-fix  2cfc137 A/B run findings — arm-scope + DeltaSpend delta accounting fix  ← CODE
b2cdf64 #94  lane-followon · d20eadc #82 gymlog-standup-digest · c51be72 #91 jev-build
c7b2825 #113 ops-ab-harness · b9d6827 #122 fix-protocol-seal · cfc5d35 #120 admin-handoff
d0bb946 #111 fix-protocol-s4 · be87bfa #110 s3 · ac91d3e #109 s2 · 63d54b8 #108 s1
```

(Counts above are the authoritative `86a1ad5..origin/main` set — the Windows
deploy stamp pins the deployed commit, so every merge on main not reachable
from `86a1ad5` is by definition out of prod; PR numbers do not sort by merge
date, and the oldest listed merges here — #82/#91/#94 — did merge after the
build.)

## Highest-impact deltas sitting out of prod
1. **`a9a397e` (#135) — channel law edit shipped to repo but NOT the live relay.**
   In-room behavior (Done= lines, PM lane orders) still runs Sep-29-BUILD law.
2. **`43681d4`** monthly-USD cent display (+3 guard tests) — spend visuals
   stale in prod.
3. **`2cfc137`** DeltaSpend session-ledger delta accounting (correctness fix).
4. 8 fix-protocol/collab commits (#108–#111, #122) — room behavior hardening
   not live.

## Convention gap (minor, flag for operator)
- `BUILD_SHA.txt` stamp exists on Windows deploy tree but is **not present
  inside `/app`** (`NO_BUILD_SHA_IN_CONTAINER`), so operators can't correlate
  from the container side (AGENTS.md §6 asks image/deploy stamping).
- Health OK throughout: `{"status":"healthy"}` 12:06Z + listening port
  confirmed; staleness is NOT an outage — just law/code drift.

## Note on racing
`origin` had fresh activity within the audit window (worktrees
`raz-receipt-gate` 12:29 PT, `voice-law` 02:51 PT — plus merged #135 at 11:11
PT). At ask-return, any merge AFTER audit start would shift the counts upward:
`git rev-list --merges <image-sha>..<main>` is the reproducible check.
