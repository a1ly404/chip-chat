"""Dev-bot PR pipeline: PM assigns, a dev bot opens the PR, a DIFFERENT bot approves, PM gates the merge.

This is the bookkeeping and the rules for "a dev bot ships a change end to end" (assign → work in an
isolated job → PR → independent review → CI green → merge). It never talks to a forge and never
merges anything itself: a plugin driver does the git/forge calls and asks this module, at every
step, whether the step is allowed. Every step is an append-only row in
``<data dir>/policy/dev_pr.jsonl``; an item's state is the fold of its rows.

Rules (all fail closed):

* the job policy must be on (``CHIP_JOB_POLICY``);
* only the PM (``job_policy.PM``) assigns and gates merges, and neither is allowed from inside a
  job or bot process (``CHIP_BGJOB_ID`` / ``CHIP_JOB_ACTOR``), so a dev bot cannot assign itself
  work or approve its own merge;
* author and reviewer are two different ``dev:*`` bots fixed at assignment time; only the author
  submits, only the reviewer reviews;
* the repo must be on the ``CHIP_DEVPR_REPOS`` allowlist (comma-separated ``owner/name``; empty
  means no repo is allowed);
* a submit must cite an executor receipt (``policy_exec``) with status ``ok`` that the author
  requested, i.e. the work ran as a PM-authorized policy job, not ad hoc;
* the review binds to the submitted head sha; the merge gate needs an approval of the CURRENT head
  sha (a push after approval re-opens review) and CI state exactly ``success``;
* a merge is recorded only against the LATEST gate id that allowed it; any later gate (allow or deny)
  supersedes earlier ones. Drivers should merge pinned to the gated head sha (forges accept an
  expected head sha on merge) so a push between gate and merge cannot slip in.

CLI: ``python -m chip.dev_pr assign|submit|review|gate|merged|show|list`` (JSON out; exit 0 ok,
2 refused/deny, 64 usage).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from chip import job_policy as jp

REPOS_ENV = "CHIP_DEVPR_REPOS"
FILE_NAME = "dev_pr.jsonl"
STATES = ("assigned", "submitted", "changes_requested", "approved", "merge_ok", "merged", "closed")
OPEN_STATES = frozenset({"assigned", "submitted", "changes_requested", "approved", "merge_ok"})
CI_OK = "success"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_BOT = re.compile(r"^dev:[a-z0-9][a-z0-9._-]{0,63}$", re.IGNORECASE)
_TICKET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,119}$")
_ITEM = re.compile(r"^devpr-[0-9a-f]{12}$")
MAX_NOTE = 500


class DevPrRefused(RuntimeError):
    """A pipeline step was refused (the message is the reason)."""


def ledger_path() -> Path:
    return jp.policy_dir() / FILE_NAME


def allowed_repos(env: Mapping[str, str] | None = None) -> frozenset[str]:
    raw = str(jp._env(env).get(REPOS_ENV, "") or "")
    return frozenset(r.strip().lower() for r in raw.split(",") if _REPO.match(r.strip()))


def _rows() -> list[dict[str, Any]]:
    return jp._jsonl(ledger_path())


def _fold(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        iid = str(row.get("id", ""))
        ev = row.get("event")
        if ev == "assign":
            items[iid] = {
                "id": iid, "state": "assigned", "ticket": row.get("ticket"), "repo": row.get("repo"),
                "author": row.get("author"), "reviewer": row.get("reviewer"), "reason": row.get("reason", ""),
                "assigned_at": row.get("ts"), "pr": None, "head_sha": "", "decision_id": "",
                "approved_sha": "", "gate_id": "", "merged_sha": "", "history": [],
            }
        it = items.get(iid)
        if it is None:
            continue
        it["history"].append({k: row.get(k) for k in ("event", "actor", "ts") if k in row})
        if ev == "submit":
            it.update(state="submitted", pr=row.get("pr"), head_sha=row.get("head_sha"),
                      decision_id=row.get("decision_id"), approved_sha="", gate_id="")
        elif ev == "review":
            if row.get("verdict") == "approve":
                it.update(state="approved", approved_sha=row.get("head_sha"), approved_by=row.get("actor"))
            else:
                it.update(state="changes_requested", approved_sha="")
        elif ev == "gate":
            if row.get("verdict") == "allow":
                it.update(state="merge_ok", gate_id=row.get("gate_id"))
            elif it["state"] == "merge_ok":  # a later deny revokes an earlier allow
                it.update(state="approved", gate_id="")
        elif ev == "merged":
            it.update(state="merged", merged_sha=row.get("merged_sha"))
        elif ev == "close":
            it.update(state="closed")
    return items


def items(*, state: str | None = None) -> list[dict[str, Any]]:
    out = list(_fold(_rows()).values())
    return [i for i in out if not state or i["state"] == state]


def show(item_id: str) -> dict[str, Any]:
    it = _fold(_rows()).get(item_id)
    if it is None:
        raise DevPrRefused(f"unknown item {item_id!r}")
    return it


def _write(row: dict[str, Any], now: datetime | None) -> dict[str, Any]:
    row = {**row, "ts": jp._iso(jp._now(now))}
    jp._append(ledger_path(), row)
    return row


def _common(env: Mapping[str, str] | None) -> None:
    if not jp.enabled(env):
        raise DevPrRefused("job policy disabled (CHIP_JOB_POLICY)")


def _pm_only(actor: str, what: str, env: Mapping[str, str] | None) -> None:
    marker = jp.in_job_marker(env)
    if marker:
        raise DevPrRefused(f"{what} is refused inside a job or bot process ({marker} set)")
    if actor != jp.PM:
        raise DevPrRefused(f"only the PM may {what} (actor {actor!r})")


def _item(item_id: str) -> dict[str, Any]:
    if not _ITEM.match(str(item_id or "")):
        raise DevPrRefused("malformed item id")
    return show(item_id)


def _sha(raw: str, what: str) -> str:
    s = str(raw or "").strip().lower()
    if not _SHA.match(s):
        raise DevPrRefused(f"{what} must be a full 40-hex commit sha")
    return s


def assign(*, ticket: str, repo: str, author: str, reviewer: str, actor: str, reason: str = "",
           env: Mapping[str, str] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """PM assigns ``ticket`` in ``repo`` to dev bot ``author``; ``reviewer`` must be a different bot."""
    _common(env)
    _pm_only(actor, "assign work", env)
    ticket = str(ticket or "").strip()
    if not _TICKET.match(ticket):
        raise DevPrRefused("malformed ticket reference")
    r = str(repo or "").strip().lower()
    if r not in allowed_repos(env):
        raise DevPrRefused(f"repo {repo!r} not in {REPOS_ENV} allowlist")
    for who, val in (("author", author), ("reviewer", reviewer)):
        if not _BOT.match(str(val or "")):
            raise DevPrRefused(f"{who} must be a dev bot (dev:<name>)")
    if author.lower() == reviewer.lower():
        raise DevPrRefused("reviewer must be a different bot than the author")
    for it in _fold(_rows()).values():
        if it["ticket"] == ticket and it["repo"] == r and it["state"] in OPEN_STATES:
            raise DevPrRefused(f"ticket already has an open item {it['id']}")
    iid = "devpr-" + uuid.uuid4().hex[:12]
    _write({"id": iid, "event": "assign", "actor": actor, "ticket": ticket, "repo": r,
            "author": author.lower(), "reviewer": reviewer.lower(), "reason": str(reason)[:MAX_NOTE]}, now)
    return show(iid)


def _receipt_ok(decision_id: str, author: str) -> None:
    from chip import policy_exec as px

    rows = px.receipt(decision_id) if decision_id else []
    if not rows:
        raise DevPrRefused("no executor receipt for that decision (work must run as a PM-authorized job)")
    last = rows[-1]
    if last.get("status") != "ok":
        raise DevPrRefused(f"cited job did not succeed (status {last.get('status')!r})")
    if str(last.get("requested_by", "")).lower() != author:
        raise DevPrRefused("cited job was not requested by the assigned author")


def submit(item_id: str, *, actor: str, pr: int, head_sha: str, decision_id: str,
           env: Mapping[str, str] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """The assigned author reports its PR (number + head sha) and the job receipt behind it."""
    _common(env)
    it = _item(item_id)
    if it["state"] not in ("assigned", "changes_requested"):
        raise DevPrRefused(f"cannot submit in state {it['state']!r}")
    if str(actor).lower() != it["author"]:
        raise DevPrRefused("only the assigned author may submit")
    try:
        n = int(pr)
    except (TypeError, ValueError) as exc:
        raise DevPrRefused("pr must be a positive integer") from exc
    if n <= 0:
        raise DevPrRefused("pr must be a positive integer")
    sha = _sha(head_sha, "head_sha")
    _receipt_ok(str(decision_id or "").strip(), it["author"])
    _write({"id": item_id, "event": "submit", "actor": it["author"], "pr": n, "head_sha": sha,
            "decision_id": str(decision_id).strip()}, now)
    return show(item_id)


def review(item_id: str, *, actor: str, verdict: str, head_sha: str, note: str = "",
           env: Mapping[str, str] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """The assigned reviewer (never the author) approves or requests changes on the submitted head."""
    _common(env)
    it = _item(item_id)
    who = str(actor or "").lower()
    if who == it["author"]:
        raise DevPrRefused("the author cannot review its own change")
    if who != it["reviewer"]:
        raise DevPrRefused("only the assigned reviewer may review")
    if it["state"] != "submitted":
        raise DevPrRefused(f"cannot review in state {it['state']!r}")
    if verdict not in ("approve", "changes"):
        raise DevPrRefused("verdict must be approve or changes")
    if _sha(head_sha, "head_sha") != it["head_sha"]:
        raise DevPrRefused("review sha does not match the submitted head")
    _write({"id": item_id, "event": "review", "actor": who, "verdict": verdict, "head_sha": it["head_sha"],
            "note": str(note)[:MAX_NOTE]}, now)
    return show(item_id)


def merge_gate(item_id: str, *, actor: str, head_sha: str, ci: str, env: Mapping[str, str] | None = None,
               now: datetime | None = None) -> dict[str, Any]:
    """PM merge gate. ``head_sha`` / ``ci`` are the forge's CURRENT values for the PR head.

    Returns ``{"verdict": "allow"|"deny", "reasons": [...], "gate_id": ...}``; every call is logged."""
    _common(env)
    _pm_only(actor, "gate a merge", env)
    it = _item(item_id)
    cur = _sha(head_sha, "head_sha")
    reasons: list[str] = []
    if it["state"] not in ("approved", "merge_ok"):
        reasons.append(f"state is {it['state']!r}, not approved")
    if not it.get("approved_sha"):
        reasons.append("no approval")
    elif cur != it["approved_sha"]:
        reasons.append("PR head moved since approval (re-review needed)")
    if it.get("approved_by") and it["approved_by"] == it["author"]:
        reasons.append("approval came from the author")
    if str(ci or "").strip().lower() != CI_OK:
        reasons.append(f"CI is {ci!r}, not success")
    verdict = "deny" if reasons else "allow"
    gate_id = "gate-" + uuid.uuid4().hex[:12] if verdict == "allow" else ""
    _write({"id": item_id, "event": "gate", "actor": actor, "verdict": verdict, "head_sha": cur,
            "ci": str(ci)[:32], "reasons": reasons, "gate_id": gate_id}, now)
    return {"id": item_id, "verdict": verdict, "reasons": reasons, "gate_id": gate_id, "head_sha": cur}


def record_merged(item_id: str, *, actor: str, gate_id: str, merged_sha: str,
                  env: Mapping[str, str] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Record the merge the driver performed after an ``allow`` gate."""
    _common(env)
    _pm_only(actor, "record a merge", env)
    it = _item(item_id)
    if it["state"] != "merge_ok" or not gate_id or gate_id != it.get("gate_id"):
        raise DevPrRefused("no matching allow gate for this merge")
    sha = _sha(merged_sha, "merged_sha")
    _write({"id": item_id, "event": "merged", "actor": actor, "gate_id": gate_id, "merged_sha": sha}, now)
    return show(item_id)


def close(item_id: str, *, actor: str, reason: str, env: Mapping[str, str] | None = None,
          now: datetime | None = None) -> dict[str, Any]:
    """PM abandons an open item (e.g. PR closed unmerged)."""
    _common(env)
    _pm_only(actor, "close an item", env)
    it = _item(item_id)
    if it["state"] not in OPEN_STATES:
        raise DevPrRefused(f"item is already {it['state']!r}")
    _write({"id": item_id, "event": "close", "actor": actor, "reason": str(reason)[:MAX_NOTE]}, now)
    return show(item_id)


# ------------------------------------------------------------------ CLI


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m chip.dev_pr", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("assign")
    a.add_argument("--ticket", required=True)
    a.add_argument("--repo", required=True)
    a.add_argument("--author", required=True)
    a.add_argument("--reviewer", required=True)
    a.add_argument("--reason", default="")
    a.add_argument("--actor", default=jp.PM)
    s = sub.add_parser("submit")
    s.add_argument("item")
    s.add_argument("--actor", required=True)
    s.add_argument("--pr", required=True)
    s.add_argument("--head-sha", required=True)
    s.add_argument("--decision-id", required=True)
    r = sub.add_parser("review")
    r.add_argument("item")
    r.add_argument("--actor", required=True)
    r.add_argument("--verdict", required=True, choices=["approve", "changes"])
    r.add_argument("--head-sha", required=True)
    r.add_argument("--note", default="")
    g = sub.add_parser("gate")
    g.add_argument("item")
    g.add_argument("--head-sha", required=True)
    g.add_argument("--ci", required=True)
    g.add_argument("--actor", default=jp.PM)
    m = sub.add_parser("merged")
    m.add_argument("item")
    m.add_argument("--gate-id", required=True)
    m.add_argument("--merged-sha", required=True)
    m.add_argument("--actor", default=jp.PM)
    c = sub.add_parser("close")
    c.add_argument("item")
    c.add_argument("--reason", required=True)
    c.add_argument("--actor", default=jp.PM)
    sh = sub.add_parser("show")
    sh.add_argument("item")
    ls = sub.add_parser("list")
    ls.add_argument("--state", choices=STATES)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 64
    try:
        if args.cmd == "assign":
            out: Any = assign(ticket=args.ticket, repo=args.repo, author=args.author, reviewer=args.reviewer,
                              actor=args.actor, reason=args.reason)
        elif args.cmd == "submit":
            out = submit(args.item, actor=args.actor, pr=args.pr, head_sha=args.head_sha, decision_id=args.decision_id)
        elif args.cmd == "review":
            out = review(args.item, actor=args.actor, verdict=args.verdict, head_sha=args.head_sha, note=args.note)
        elif args.cmd == "gate":
            out = merge_gate(args.item, actor=args.actor, head_sha=args.head_sha, ci=args.ci)
        elif args.cmd == "merged":
            out = record_merged(args.item, actor=args.actor, gate_id=args.gate_id, merged_sha=args.merged_sha)
        elif args.cmd == "close":
            out = close(args.item, actor=args.actor, reason=args.reason)
        elif args.cmd == "show":
            out = show(args.item)
        else:
            out = items(state=args.state)
    except DevPrRefused as exc:
        print(json.dumps({"refused": str(exc)}))
        return 2
    print(json.dumps(out, ensure_ascii=False))
    if isinstance(out, dict) and out.get("verdict") == "deny":
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
