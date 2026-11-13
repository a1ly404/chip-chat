"""PM-driven forge steps: the PM (outside any job) runs the dev-bot PR forge steps through a runner.

``chip.forge_driver`` does the forge calls (``open`` / ``ci`` / ``review`` / ``merge``) and is the only
code that ever holds forge credentials. A deployment usually runs it as a separate one-shot process
or container that alone can read the GitHub App key files. This module is the orchestrator that sits
on the PM side (relay / scheduler): it never sees a key, token or app config. It passes only a step
name and the item id plus plain arguments to a pluggable :class:`StepRunner`, and parses the JSON the
driver prints.

Order, enforced here before the runner is called (the driver and ``chip.dev_pr`` enforce it again):

1. ``open``   - item ``assigned`` / ``changes_requested``; runs as the item's author (the dev identity).
2. ``ci``     - item has a PR; read-only CI poll of the PR's current head.
3. ``review`` - item ``submitted``, and a fresh ``ci`` poll says ``success`` on the submitted head;
   runs as the item's reviewer, which the driver signs with the PM/approver identity. The author can
   never review.
4. ``merge``  - item ``approved`` / ``merge_ok`` by a reviewer that is not the author, and a fresh ``ci``
   poll says ``success`` on the approved head; runs as the PM. The driver re-checks with
   ``dev_pr.merge_gate`` and merges pinned to the gated sha.

Anything else is refused with a reason and nothing runs.

Fail closed and default off:

* ``CHIP_FORGE_STEPS`` - empty / ``0`` refuses every step. ``1`` / ``all`` allows all four, or a
  comma list (e.g. ``ci,review``) allows just those.
* ``CHIP_FORGE_STEP_CMD`` - the runner command as a JSON array (argv prefix, no shell). The step and
  its arguments are appended. Empty = refused (no runner configured).
* ``CHIP_FORGE_STEPS_TICK`` - ``1`` lets :func:`advance` (called from the PM tick) poll CI and merge
  approved items on its own. Default off; ``open`` and ``review`` are never automatic, because they
  need a job's output or a reviewer's verdict.
* Steps are PM-only and refused inside a job or bot process (``CHIP_BGJOB_ID`` / ``CHIP_JOB_ACTOR``),
  so a dev bot cannot drive its own PR through the forge.
* The runner gets a scrubbed environment: every ``CHIP_FORGE_*`` var, every secret-looking var and
  every policy control are removed. The key-holding process takes its config from its own
  deployment, never from the caller.

Every call (allowed or refused) appends a row with the PM's reason to
``<data dir>/policy/forge_steps.jsonl``. Rows hold the step, item, rc and a short whitelisted summary
of the driver's JSON (pr, head sha, url, state, ci, verdict, gate/merged sha). No bodies, no tokens.

CLI: ``python -m chip.forge_steps open|ci|review|merge|advance|status`` (JSON out; exit 0 ok,
2 refused/deny, 5 runner or forge error, 64 usage).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

from chip import dev_pr as dp
from chip import job_policy as jp

FLAG = "CHIP_FORGE_STEPS"
CMD_ENV = "CHIP_FORGE_STEP_CMD"
TICK_ENV = "CHIP_FORGE_STEPS_TICK"
TIMEOUT_ENV = "CHIP_FORGE_STEP_TIMEOUT_S"
FILE_NAME = "forge_steps.jsonl"
STEPS = ("open", "ci", "review", "merge")
DEFAULT_TIMEOUT_S = 300
EXIT_OK, EXIT_REFUSED, EXIT_RUNNER, EXIT_USAGE = 0, 2, 5, 64
SUMMARY_KEYS = ("pr", "head_sha", "url", "state", "ci", "verdict", "reasons", "gate_id", "merged",
                "merged_sha", "review_id", "comment_id", "kind", "refused", "forge_error")
_ITEM = re.compile(r"^devpr-[0-9a-f]{12}$")
_SECRETISH = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE|API_KEY|_KEY$|_KEY_|WEBHOOK|CREDENTIAL|COOKIE)",
                        re.IGNORECASE)


class StepRefused(RuntimeError):
    """A forge step was refused before the runner ran (the message is the reason)."""


@dataclass
class StepResult:
    """What the runner reports: the driver's exit code and its parsed JSON (``{}`` if none)."""

    rc: int
    out: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class StepRunner(Protocol):
    def __call__(self, step: str, args: Sequence[str]) -> StepResult: ...


# ------------------------------------------------------------------ config


def _env(env: Mapping[str, str] | None) -> dict[str, str]:
    return dict(os.environ if env is None else env)


def allowed_steps(env: Mapping[str, str] | None = None) -> frozenset[str]:
    raw = str(_env(env).get(FLAG, "") or "").strip().lower()
    if raw in ("", "0", "off", "false", "no"):
        return frozenset()
    if raw in ("1", "all", "on", "true", "yes"):
        return frozenset(STEPS)
    return frozenset(s.strip() for s in raw.split(",") if s.strip() in STEPS)


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return bool(allowed_steps(env))


def tick_enabled(env: Mapping[str, str] | None = None) -> bool:
    return jp._truthy(_env(env).get(TICK_ENV, "0"))


def command(env: Mapping[str, str] | None = None) -> list[str]:
    """The runner argv prefix from ``CHIP_FORGE_STEP_CMD`` (JSON array of strings); ``[]`` if unset/bad."""
    raw = str(_env(env).get(CMD_ENV, "") or "").strip()
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(val, list) or not val or not all(isinstance(x, str) and x for x in val):
        return []
    return list(val)


def runner_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for the runner process: no forge config, no secrets, no policy controls."""
    out: dict[str, str] = {}
    for k, v in _env(env).items():
        if k.startswith(("CHIP_FORGE_", "CHIP_POLICY_", "CHIP_JOB_POLICY")) or k == jp.YOLO_ENV:
            continue
        if k in ("GITHUB_TOKEN", "GH_TOKEN") or _SECRETISH.search(k):
            continue
        out[k] = v
    return out


def parse_output(text: str) -> dict[str, Any]:
    """The last stdout line that is a JSON object (the driver prints exactly one)."""
    for line in reversed(str(text or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                val = json.loads(line)
            except ValueError:
                continue
            if isinstance(val, dict):
                return val
    return {}


class CommandRunner:
    """Runs ``<prefix> <step> <args...>`` with no shell and a scrubbed env; parses the JSON result.

    The prefix is deployment config, e.g. a one-shot container run of the forge driver. Only the
    exit code and the parsed JSON come back; raw output is never returned or logged.
    """

    def __init__(self, prefix: Sequence[str], *, env: Mapping[str, str] | None = None,
                 timeout_s: int = DEFAULT_TIMEOUT_S, run: Any = None) -> None:
        if not prefix:
            raise StepRefused(f"no forge step runner configured ({CMD_ENV})")
        self.prefix = list(prefix)
        self.env = runner_env(env)
        self.timeout_s = int(timeout_s)
        self._run = run or subprocess.run

    def __call__(self, step: str, args: Sequence[str]) -> StepResult:
        argv = [*self.prefix, step, *args]
        try:
            p = self._run(argv, env=self.env, capture_output=True, text=True, timeout=self.timeout_s,
                          stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return StepResult(EXIT_RUNNER, {}, f"runner timed out after {self.timeout_s}s")
        except OSError as exc:
            return StepResult(EXIT_RUNNER, {}, f"runner failed to start: {type(exc).__name__}")
        out = parse_output(p.stdout or "")
        err = "" if out else f"runner exit {p.returncode} with no JSON result"
        return StepResult(int(p.returncode), out, err)


def default_runner(env: Mapping[str, str] | None = None) -> CommandRunner:
    e = _env(env)
    try:
        timeout = int(str(e.get(TIMEOUT_ENV, "") or DEFAULT_TIMEOUT_S))
    except ValueError:
        timeout = DEFAULT_TIMEOUT_S
    return CommandRunner(command(e), env=e, timeout_s=max(10, min(timeout, 1800)))


# ------------------------------------------------------------------ log


def log_path():
    return jp.policy_dir() / FILE_NAME


def _summary(out: Mapping[str, Any]) -> dict[str, Any]:
    s: dict[str, Any] = {}
    for k in SUMMARY_KEYS:
        if k in out:
            v = out[k]
            s[k] = v[:300] if isinstance(v, str) else (v[:6] if isinstance(v, list) else v)
    return s


def _log(row: Mapping[str, Any], now: datetime | None) -> None:
    jp._append(log_path(), {**row, "ts": jp._iso(jp._now(now))})


def history(item_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    rows = jp._jsonl(log_path())
    if item_id:
        rows = [r for r in rows if r.get("id") == item_id]
    return rows[-max(1, int(limit)):]


# ------------------------------------------------------------------ orchestrator


class Steps:
    """PM-side orchestrator. ``runner`` is any :class:`StepRunner`; default is :func:`default_runner`."""

    def __init__(self, *, env: Mapping[str, str] | None = None, runner: StepRunner | None = None,
                 now: datetime | None = None) -> None:
        self.env = env
        self._runner = runner
        self.now = now

    @property
    def runner(self) -> StepRunner:
        if self._runner is None:
            self._runner = default_runner(self.env)
        return self._runner

    def _pre(self, step: str, item_id: str, reason: str) -> dict[str, Any]:
        if step not in allowed_steps(self.env):
            raise StepRefused(f"forge step {step!r} not enabled ({FLAG} is "
                              f"{str(_env(self.env).get(FLAG, '') or '').strip() or 'empty'})")
        marker = jp.in_job_marker(self.env)
        if marker:
            raise StepRefused(f"forge steps are PM-only and refused inside a job or bot process ({marker} set)")
        if not str(reason or "").strip():
            raise StepRefused("a PM reason is required for every forge step")
        if not _ITEM.match(str(item_id or "")):
            raise StepRefused("malformed item id")
        try:
            return dp.show(item_id)
        except dp.DevPrRefused as exc:
            raise StepRefused(str(exc)) from None

    def _call(self, step: str, item_id: str, args: Sequence[str], reason: str, actor: str) -> dict[str, Any]:
        res = self.runner(step, [item_id, *args])
        ok = res.rc == EXIT_OK and bool(res.out) and "refused" not in res.out and "forge_error" not in res.out
        if res.out.get("verdict") == "deny":
            ok = False
        status = "ok" if ok else ("refused" if res.rc == EXIT_REFUSED else "error")
        row = {"id": item_id, "step": step, "actor": actor, "reason": str(reason)[:dp.MAX_NOTE], "status": status,
               "rc": res.rc, "result": _summary(res.out)}
        if res.error:
            row["error"] = res.error[:300]
        _log(row, self.now)
        return {"id": item_id, "step": step, "status": status, "rc": res.rc, **_summary(res.out),
                **({"error": res.error[:300]} if res.error else {})}

    def _refuse(self, step: str, item_id: str, reason: str, why: str) -> StepRefused:
        _log({"id": str(item_id)[:40], "step": step, "actor": jp.PM, "reason": str(reason)[:dp.MAX_NOTE],
              "status": "refused", "refused": why[:300]}, self.now)
        return StepRefused(why)

    def _guard(self, step: str, item_id: str, reason: str) -> dict[str, Any]:
        try:
            return self._pre(step, item_id, reason)
        except StepRefused as exc:
            raise self._refuse(step, item_id, reason, str(exc)) from None

    # ---------------------------------------------------------------- steps
    def open(self, item_id: str, *, worktree: str, branch: str, title: str, decision_id: str,
             reason: str, body: str = "", base: str = "main") -> dict[str, Any]:
        it = self._guard("open", item_id, reason)
        if it["state"] not in ("assigned", "changes_requested"):
            raise self._refuse("open", item_id, reason, f"cannot open in state {it['state']!r}")
        for what, val in (("worktree", worktree), ("branch", branch), ("title", title), ("decision id", decision_id)):
            if not str(val or "").strip():
                raise self._refuse("open", item_id, reason, f"{what} required")
        args = [f"--actor={it['author']}", f"--worktree={worktree}", f"--branch={branch}", f"--title={title}",
                f"--body={body}", f"--decision-id={decision_id}", f"--base={base}"]
        return self._call("open", item_id, args, reason, it["author"])

    def ci(self, item_id: str, *, reason: str) -> dict[str, Any]:
        it = self._guard("ci", item_id, reason)
        if not it.get("pr"):
            raise self._refuse("ci", item_id, reason, "item has no PR (open first)")
        return self._call("ci", item_id, [], reason, jp.PM)

    def _ci_green(self, step: str, item_id: str, reason: str, want_sha: str) -> dict[str, Any]:
        if "ci" not in allowed_steps(self.env):
            raise self._refuse(step, item_id, reason, f"{step} needs a CI check first, and step 'ci' is not enabled")
        c = self._call("ci", item_id, [], f"pre-{step} CI check: {reason}", jp.PM)
        if c["status"] != "ok":
            raise self._refuse(step, item_id, reason, f"CI check failed ({c.get('refused') or c.get('forge_error') or c.get('error') or c['status']})")
        if c.get("ci") != dp.CI_OK:
            raise self._refuse(step, item_id, reason, f"CI is {c.get('ci')!r}, not success")
        if str(c.get("head_sha", "")).lower() != str(want_sha or "").lower():
            raise self._refuse(step, item_id, reason, "PR head moved (CI result is for a different sha)")
        return c

    def review(self, item_id: str, *, verdict: str, reason: str, note: str = "") -> dict[str, Any]:
        it = self._guard("review", item_id, reason)
        if verdict not in ("approve", "changes"):
            raise self._refuse("review", item_id, reason, "verdict must be approve or changes")
        if it["state"] != "submitted" or not it.get("pr"):
            raise self._refuse("review", item_id, reason, f"cannot review in state {it['state']!r} (open first)")
        if not it.get("reviewer") or it["reviewer"] == it["author"]:
            raise self._refuse("review", item_id, reason, "reviewer must differ from the author")
        if verdict == "approve":
            self._ci_green("review", item_id, reason, it["head_sha"])
        args = [f"--actor={it['reviewer']}", f"--verdict={verdict}", f"--note={note or reason}"]
        return self._call("review", item_id, args, reason, it["reviewer"])

    def merge(self, item_id: str, *, reason: str, method: str = "squash") -> dict[str, Any]:
        it = self._guard("merge", item_id, reason)
        if method not in ("squash", "merge", "rebase"):
            raise self._refuse("merge", item_id, reason, "merge method must be squash, merge or rebase")
        if it["state"] not in ("approved", "merge_ok") or not it.get("approved_sha"):
            raise self._refuse("merge", item_id, reason, f"no APPROVE review (state {it['state']!r})")
        if it.get("approved_by") in (None, "", it["author"]):
            raise self._refuse("merge", item_id, reason, "approval did not come from a reviewer other than the author")
        self._ci_green("merge", item_id, reason, it["approved_sha"])
        return self._call("merge", item_id, [f"--actor={jp.PM}", f"--method={method}"], reason, jp.PM)


def advance(*, env: Mapping[str, str] | None = None, runner: StepRunner | None = None,
            now: datetime | None = None) -> dict[str, Any]:
    """PM tick hook: poll CI on submitted items and merge approved ones whose CI is green.

    No-op unless ``CHIP_FORGE_STEPS`` allows the step and ``CHIP_FORGE_STEPS_TICK=1``. Never opens or
    reviews. Each refusal is logged and reported, never raised."""
    if not (enabled(env) and tick_enabled(env)):
        return {"status": "off"}
    s = Steps(env=env, runner=runner, now=now)
    done: list[dict[str, Any]] = []
    for it in dp.items():
        try:
            if it["state"] in ("approved", "merge_ok") and "merge" in allowed_steps(env):
                done.append(s.merge(it["id"], reason="tick: approved item, merge if CI is green"))
            elif it["state"] == "submitted" and it.get("pr") and "ci" in allowed_steps(env):
                done.append(s.ci(it["id"], reason="tick: CI poll for a submitted item awaiting review"))
        except StepRefused as exc:
            done.append({"id": it["id"], "status": "refused", "refused": str(exc)[:300]})
    return {"status": "ok", "steps": done}


def status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    e = _env(env)
    return {"enabled": enabled(e), "steps": sorted(allowed_steps(e)), "tick": tick_enabled(e),
            "runner_configured": bool(command(e)), "in_job": bool(jp.in_job_marker(e))}


# ------------------------------------------------------------------ CLI


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m chip.forge_steps", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("open")
    o.add_argument("item")
    o.add_argument("--worktree", required=True)
    o.add_argument("--branch", required=True)
    o.add_argument("--title", required=True)
    o.add_argument("--body", default="")
    o.add_argument("--decision-id", required=True)
    o.add_argument("--base", default="main")
    o.add_argument("--reason", required=True)
    c = sub.add_parser("ci")
    c.add_argument("item")
    c.add_argument("--reason", required=True)
    r = sub.add_parser("review")
    r.add_argument("item")
    r.add_argument("--verdict", required=True, choices=("approve", "changes"))
    r.add_argument("--note", default="")
    r.add_argument("--reason", required=True)
    m = sub.add_parser("merge")
    m.add_argument("item")
    m.add_argument("--method", default="squash", choices=("squash", "merge", "rebase"))
    m.add_argument("--reason", required=True)
    sub.add_parser("advance")
    sub.add_parser("status")
    return p


def main(argv: Sequence[str] | None = None, *, runner: StepRunner | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else EXIT_USAGE
    s = Steps(runner=runner)
    try:
        if args.cmd == "status":
            out: Any = status()
        elif args.cmd == "advance":
            out = advance(runner=runner)
        elif args.cmd == "open":
            out = s.open(args.item, worktree=args.worktree, branch=args.branch, title=args.title, body=args.body,
                         decision_id=args.decision_id, base=args.base, reason=args.reason)
        elif args.cmd == "ci":
            out = s.ci(args.item, reason=args.reason)
        elif args.cmd == "review":
            out = s.review(args.item, verdict=args.verdict, note=args.note, reason=args.reason)
        else:
            out = s.merge(args.item, method=args.method, reason=args.reason)
    except StepRefused as exc:
        print(json.dumps({"refused": str(exc)}))
        return EXIT_REFUSED
    print(json.dumps(out, ensure_ascii=False))
    if isinstance(out, dict) and out.get("status") in ("refused",):
        return EXIT_REFUSED
    if isinstance(out, dict) and out.get("status") == "error":
        return EXIT_RUNNER
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
