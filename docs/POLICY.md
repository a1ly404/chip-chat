# Job policy (`chip policy`)

Parity item **D slice 1**: the engine decides which jobs may run, and which may run with sudo/root,
by itself, without a separate job runner. Module: `scripts/chip/job_policy.py` (+ `scripts/chip/ed25519_ref.py`).
Tests: `scripts/test_chip_job_policy.py` (attack matrix included).

**Off by default.** `CHIP_JOB_POLICY=1` enables evaluation; with it off every request is denied.
`job_policy` only *decides*; `scripts/chip/policy_exec.py` (slice 2, below) is the only place an
approved argv runs, and needs `CHIP_POLICY_EXEC=1` as well. Nothing here enables
`CHIP_JOBS_ALLOW_MUTATE`, `CHIP_HOST_EXEC` or `CHIP_RECIPE_EXEC`.

## Roles

| who | can | cannot |
| --- | --- | --- |
| **PM** (`actor="pm"`) | evaluate requests; approve ordinary jobs inside the policy; in YOLO mode grant sudo | request jobs; write rules; change YOLO or policy |
| **Dev bot** (`dev:<name>`) | request a job (what, why, scope) | evaluate, approve, confirm, or grant itself sudo |
| **Operator** | confirm/decline requests, revoke rules, pin the policy file, generate the key | — (not a runtime principal: needs the operator key file) |

## Decision order (`job_policy.evaluate`)

1. flag off → **deny**
2. malformed request, unknown fields (`approved_by`, `yolo`, `rule_id`, …), non-PM actor, PM/operator
   requester → **deny**
3. authority store signature chain broken → **deny** (the whole store fails closed)
4. policy file changed since the operator pinned it → **deny**
5. **hard denies** (below) → **deny**, whatever YOLO, rules or confirms say
6. job-type policy (commands, targets, scripts, caps) → **deny** on mismatch
7. not sudo → **allow** (PM approves within policy)
8. sudo: job type `sudo: "never"` → deny; matching operator rule → **allow**; YOLO → **allow** with a
   logged grant; otherwise → **ask** (a pending operator confirm request)

Every evaluation writes a receipt to `policy/audit.jsonl` (command sha256, never the raw argv).

## Sudo: confirm → durable rule (YOLO off, the default)

* The PM's **ask** creates a request in `policy/requests.jsonl` with what (command, action, target,
  context), why (PM reason) and an expiry (`CHIP_POLICY_REQUEST_TTL_S`, default 24 h). Identical
  requests reuse the pending one.
* `chip policy confirm <id>` (operator): the job may run once (`job_policy.was_confirmed(id)`), and a
  rule binding the **exact** scope is saved: job type, action, target, context, command sha256,
  script path + sha256. Matching requests are then auto-approved. Rules never contain wildcards, so a
  confirm never widens anything. `--rule-ttl` gives the rule an expiry.
* `chip policy decline <id>` is final for that request; the same exact request is refused for 24 h.
  Expired requests are closed and cannot be confirmed.
* `chip policy rules` lists rules, `chip policy revoke <rule>` removes one.
* Confirm re-checks hard denies and refuses a request record that was edited after it was created.

## YOLO

`CHIP_PM_YOLO` (env) beats the system pack value `values.pm_yolo` (strict JSON `true`); both default
false. In YOLO the PM grants sudo itself and never asks, but each grant is written to
`policy/grants.jsonl` (reason, exact scope, expiry `CHIP_PM_YOLO_GRANT_TTL_S`, default 1 h) and shows
in the daily PM summary under **Sudo grants (YOLO)**. Hard denies, `sudo: never` and job-type policy
still apply. A request cannot carry its own `yolo` field, and job environments drop `CHIP_PM_YOLO`.

## Authority store (tamper evidence)

`policy/authority.jsonl` holds rule adds/revokes, confirms, declines and policy pins. Each row links to
the sha256 of the previous row and carries an **Ed25519 signature** by the operator key. The runtime
holds only the public key (`CHIP_POLICY_OPERATOR_PUBKEY`, the trust root), so the PM, dev bots and jobs
can verify the store but cannot write a row that verifies. A forged, edited, reordered or deleted row,
or a non-empty store without the trust root configured, makes every evaluation deny.

Operator key rules (`OperatorKey.load`):

* `CHIP_POLICY_OPERATOR_KEY_FILE` (64 hex chars) must live **outside** the engine data dir;
* its public half must equal `CHIP_POLICY_OPERATOR_PUBKEY`;
* it refuses to load inside a job/bot process (`CHIP_BGJOB_ID` / `CHIP_JOB_ACTOR` set).

Set the key file path only in the operator's own shell, never in the relay or job environment.

```console
$ chip policy keygen --out ~/.config/chip-operator/op.key   # prints CHIP_POLICY_OPERATOR_PUBKEY
$ export CHIP_POLICY_OPERATOR_PUBKEY=<printed hex>           # relay + operator shell
$ export CHIP_POLICY_FILE=/etc/chip/job_policy.json           # outside the data dir
$ chip policy pin                                             # operator shell (has the key file)
```

## Declarative policy file

`CHIP_POLICY_FILE` (JSON). No file → no job types → everything denied. Editing it requires a fresh
`chip policy pin` by the operator.

```json
{
  "job_types": {
    "diagnostics": {
      "allow_commands": ["systemctl", "journalctl", "ls"],
      "deny_commands": ["dd"],
      "allow_targets": ["/srv/app", "/srv/app/*"],
      "deny_targets": ["/srv/app/private*"],
      "sudo": "ask",
      "max_spend_usd": 0.10,
      "max_seconds": 300
    },
    "fix-script": {
      "allow_commands": [],
      "scripts": {"/opt/chip/scripts/fix.sh": "<sha256>"}
    }
  }
}
```

* commands match on the executable's base name (globs allowed); targets are globs;
* a target is required to match `allow_targets` when given (no list → no targets allowed);
* scripts are **hash-bound**: the request must carry `script_path` + `script_sha256`, the file on disk
  must hash to it, and (when the job type lists `scripts`) to the pinned value; a rule bound to a
  script stops matching when the script changes;
* `max_spend_usd` is clamped to `bg_jobs` per-job caps; `max_seconds` to 3600.

## Hard denies

Built in, generic, never overridden:

* inline privilege escalation (`sudo`, `su`, `doas`, `pkexec`, … in the argv): sudo is only ever a
  request flag decided by policy;
* YOLO / rules / policy / enforcement changes from a job: any mention of `CHIP_PM_YOLO`, `pm_yolo`,
  `CHIP_POLICY_*`, `CHIP_JOB_POLICY`, `chip policy`, the authority store, or a path inside the policy
  store, the operator key file, the policy file or the enforcement modules;
* credential/secret access and exfiltration: secret paths (`.ssh`, `.aws`, `.env`, `*.pem`, `*.key`,
  `shadow`, …), secret-named env var references, environment dumps, raw network tools, uploads
  (`curl -T/-F/-d @`), remote copies, inline interpreter code (`bash -c`, `python -c`);
* deletes outside the declared absolute target (also wildcards, `..`, root, too-broad targets, and
  anything that would remove the policy store or key).

Plugins add more with `job_policy.register_hard_deny(name, fn)` or `CHIP_POLICY_DENY_HOOKS=module:func`
(`fn(request) -> reason | None`). Hooks can only refuse; a hook that errors or fails to load denies.

## Job environment

`job_policy.job_env(env, job_id=, actor=)` builds a subprocess environment without policy controls,
the operator key path, or secret-named variables (`*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*API_KEY*`,
`*WEBHOOK*`, …), and marks it with `CHIP_BGJOB_ID` / `CHIP_JOB_ACTOR` so operator actions refuse there.

### In-job refusal (defense in depth)

Code running inside a job could, in principle, re-add every policy flag plus YOLO to an env mapping
and call the policy API directly. So, besides the executor refusal, the scrubbed job env and the hard
denies, every decision and row writer refuses when an in-system marker is set in the passed env **or**
in the real process env (`job_policy.in_job_marker`):

* `job_policy.evaluate` returns `deny` with an `in-job refusal: …` reason as step 0 and writes nothing
  (no audit, confirm request, YOLO grant or decision row);
* `policy_exec.authorize` / `run_dev_job` / `gate_job` return `deny` with no ticket;
* operator confirm request creation, YOLO grant writes and authority-chain appends (`confirm`,
  `decline`, `revoke`, `pin`) raise `PolicyError`, even for a caller holding an `OperatorKey`.

The PM tick, relay and operator shell never carry these markers (only `job_env` sets them), so the
legitimate path is unchanged. Tests: `scripts/test_chip_policy_in_job.py`.

## CLI

| command | who | exit |
| --- | --- | --- |
| `chip policy status` / `verify` / `rules` / `pending` / `grants` / `audit` | anyone (read-only) | 5 if the store is tampered |
| `chip policy check --request JSON [--reason]` | PM (refused inside a job) | 0 allow, 4 ask, 2 deny |
| `chip policy confirm <id> [--rule-ttl S]` / `decline <id>` / `revoke <rule>` / `pin` | operator key | 2 refused |
| `chip policy keygen --out PATH` | operator | 2 if the file exists or is inside the data dir |

## Executor (slice 2)

Module `scripts/chip/policy_exec.py`, tests `scripts/test_chip_policy_exec.py` (executor attack matrix).
Off by default: needs `CHIP_JOB_POLICY=1` **and** `CHIP_POLICY_EXEC=1`.

1. `policy_exec.authorize(request, actor="pm")` runs `evaluate`. An **allow** becomes a *decision
   ticket*: id, job id, requester, request digest, command sha256, sudo flag, basis (`rule_id` or YOLO
   `grant_id`), issue and expiry time (`CHIP_POLICY_DECISION_TTL_S`, default 900 s, max 3600), plus a
   `binding` sha256 over all of it. Tickets are appended to `policy/decisions.jsonl` (inside the
   protected policy store, so a job can't touch it). An **ask** posts the confirm request to the
   operator channel (below). A **deny** returns the reason.
2. `policy_exec.execute(ticket, request)` refuses (nothing runs, a receipt is still written) unless:
   * both flags are on and the process is not a job (`CHIP_BGJOB_ID` / `CHIP_JOB_ACTOR` unset);
   * the ticket equals the row the PM stored and its binding hash verifies (hand-made or edited
     tickets refuse), it hasn't expired, and it hasn't been used (single use, `policy/consumed.jsonl`,
     claimed before the run so a crash can't replay it);
   * the request has the ticket's digest, job id, requester and sudo flag (an argv, target, context,
     script or requester swap after the decision refuses);
   * `job_policy.recheck` passes again: authority chain, policy pin, hard denies (including hooks
     registered after the decision), job-type policy and caps, and the script hash;
   * for sudo, the basis still holds (`job_policy.sudo_basis_ok`): the operator rule is still active
     and matches, or the YOLO grant is unexpired with exactly this scope and job. A non-sudo ticket
     never carries a basis.

   The argv then runs with `shell=False`, no stdin, under `job_env()` (no policy controls, no secrets),
   with the time cap (request `max_seconds`, else the job type's, max 3600) and spend cap (request
   `max_spend_usd`; a runner reporting more fails the job). Only granted sudo prepends
   `sudo -n --`; inline sudo stays a hard deny.
3. Every outcome writes a receipt to `policy/exec_receipts.jsonl` that is bound to `decision_id` + `binding`.
   It holds the command sha256, rule/grant id, status/reason, exit, seconds, spend, and the stdout sha256 plus a 400-char tail.
   It never holds the raw argv.

Entry points:

* **Dev bots**: `policy_exec.run_dev_job(request, pm_reason=...)` (PM authorizes, executor runs).
* **`bg_jobs` mutate jobs**: with `CHIP_JOB_POLICY=1`, `run_pending` routes every selected mutate
  job through `policy_exec.gate_job`. The job carries its request in `params.policy_request` (its
  `job_id` is forced to the bg job id).
  * allow: a fresh ticket goes on the record. Any ticket already on the record is dropped first.
  * ask: the job stays `queued` (deferred "waiting on operator confirm <id>") until the operator
    confirms. The next tick then matches the saved rule.
  * deny: the job fails with `policy deny: ...`.
  * A mutate job without a request is denied.

  Jobs with `params.exec: true` run their argv through the executor (`policy_exec.exec_step`).
  Other allowed mutate jobs run their normal step. With the policy flag off nothing changes, and
  `params.exec` jobs never reach the executor.

### Operator confirm notifications

`CHIP_POLICY_NOTIFY=1` (default off) posts each new confirm request once (`policy/notified.jsonl`):
what, command, PM reason, expiry and the `chip policy confirm|decline <id>` commands. The channel is a
hook: `policy_exec.register_notifier(fn)` or `CHIP_POLICY_NOTIFY_HOOK=module:func`
(`fn(message, request_row)`). Hook errors never raise and the request is retried on the next ask;
no notifier → `no-notifier`.

Built-in generic channel: set `CHIP_POLICY_NOTIFY_WEBHOOK` to an incoming-webhook URL (any service that
accepts `POST {"content": "..."}`). The message is clipped to 1900 chars and mentions are disabled
(`allowed_mentions.parse = []`), because it carries PM / requester text. A non-2xx reply is an `error`
(retried later). The URL is never printed: `chip policy status` shows only
`notify: {enabled, registered, hooks, webhook: true|false}`. The webhook name matches the job-env
secret filter, so jobs never see it.

CLI (refused inside a job, like `check`):

* `chip policy check --request JSON --notify`: on `ask`, also post the confirm request (adds `notify`).
* `chip policy notify <request_id>`: post one pending request now. Exit 0 sent/duplicate, 1
  off/no-notifier/error, 3 not pending.
* `chip policy notify --pending`: retry sweep, posts every pending request not yet posted.

## Executor hardening (slice 3)

Tests: `scripts/test_chip_policy_exec_d3.py`. Same flags as slice 2 (nothing new is on by default).

* **Process-group kill.** The default runner starts the child in its own session
  (`start_new_session`) and on the time cap kills the whole group (SIGTERM, 1 s grace, then
  SIGKILL), so grandchildren can't outlive the job or hold its output pipes open. When a job exits
  normally, background stragglers left in its group are swept too. If the group can't be signalled
  (e.g. a root-owned group under `sudo`), the runner falls back to signalling the direct child; `sudo`
  relays SIGTERM to its command.
* **Private script copy.** A hash-bound script never runs from its original path. The executor reads
  it once into a fresh `0700` temp dir (`chip-exec-*`), makes the copy `0500`, re-hashes **the copy**
  against the bound `script_sha256`, swaps the copy path into the argv and runs that. The dir is
  removed afterwards. An edit that lands after the recheck is refused (`script hash mismatch on the
  private copy`) and nothing runs; the decision is already claimed, so it can't be retried. Receipts
  carry `private_copy: true`. Residual: a process running as the same user could still write into the
  private dir between copy and exec; jobs needing stronger isolation should run as a separate user.
* **Receipts CLI (read-only).** `chip policy receipts [--limit N] [--status ok|failed|refused] [--job ID]`
  lists recent executor receipts; `chip policy receipt <decision_id>` shows every receipt for one
  decision (exit 3 if none). Receipts hold hashes and tails, never the raw argv.
* **PM daily summary.** `pm_summary.build` adds `executor`: counts by status, granted sudo runs, and
  one line per failed/refused run. Refused runs also count under refusals
  (`executor: run refused`). The rendered message gets an **Executor runs** section on days with runs.

## Alert remediations (slice 4)

Module `scripts/chip/remediations.py`, tests `scripts/test_chip_remediations.py`. Off by default:
needs `CHIP_PICKER_REMEDIATE=1` on top of `CHIP_JOB_POLICY=1` and `CHIP_POLICY_EXEC=1`.

The operator lists **remediations** in the pinned policy file. Each one maps an alert **id** (full
regex match, never title/detail text) to an exact fix request: dev-bot requester, job type,
action, argv with a single `{target}` placeholder, target, optional hash-bound script, sudo flag
and time cap. The captured `target` must match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$`.

```json
"remediations": [{
  "id": "container-heal",
  "match": "^container-down:(?P<target>[a-z0-9][a-z0-9_.-]{0,62})$",
  "requested_by": "dev:remediator", "job_type": "container-heal", "action": "restart-container",
  "command": ["/opt/chip/heal.sh", "{target}"], "target": "{target}",
  "script_path": "/opt/chip/heal.sh", "sudo": true, "max_seconds": 180
}]
```

When the PM picker sees a matching alert it queues a single-hop `mutate` job with `params.exec`,
`params.policy_request` (job id forced to the bg job id) and a PM reason, spending no model money.
`bg_jobs.run_pending` then gates it like any mutate job: hard denies, job-type allowlists, the sudo
decision (operator rule, YOLO grant, or an operator confirm request), a decision-bound ticket, the
executor and a receipt. A remediation therefore never widens anything: a target outside the job
type's `allow_targets`, or one a plugin hard-deny hook refuses, fails the job with a deny receipt in
the audit log. An edited, unpinned policy file disables every remediation. The same alert id is
picked at most once per UTC day (picker dedupe).

Fix scripts should verify their own result (e.g. wait for the service to report healthy) and exit
non-zero otherwise, so the receipt says whether the fix held.

`CHIP_PM_ALERTS_EXTRA_PATHS` (comma or path-separator list) adds JSONL alert files, such as a
deployment's own health-probe output, to the PM tick next to the default alert log; each file keeps
its own cursor. `chip policy remediations [--alert-id ID]` shows the loaded templates and the PM's
plan for an alert id (read-only).

## Next (slice 5)

* `chip policy exec --request JSON` (operator-side authorize → execute from the CLI).
* Relay/dev-bot wiring that calls `run_dev_job`; the plugin's operator-channel notifier and deployment
  hard denies (via `register_hard_deny`).
