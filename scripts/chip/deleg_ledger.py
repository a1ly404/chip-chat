"""Delegation ledger: follow-through for hand-offs from the team lead (PM) to specialists.

Every ``delegate:<persona> <task>`` hop the relay runs can be tracked as one *item*:
who asked (the request thread and user), which specialist got it, the ask, when, a
deadline, and a state. The relay calls the hooks below and posts whatever message an
:class:`Action` carries; this module never talks to a chat service itself and makes no
model calls (every follow-up line is a fixed, plain-words template).

The loop it enforces ("nothing stays open without the user hearing"):

* **hand-off** (:func:`open_or_attach`): a new item, and the PM acks in the thread
  ("handed to X, I'll report back by ..."). A near-identical delegate for the same
  specialist while the first one is still in flight is a *duplicate*: the caller skips
  the hop instead of waking the specialist twice.
* **specialist reply** (:func:`on_dev_reply`): classified as done / blocked / progress /
  contradictory / unclear. Done -> the PM relays the outcome and asks once "did that fix
  it?" (state ``checkin``). Blocked -> the PM tells the user what is stuck and what is
  next (state ``waiting_user``). Progress -> the PM relays it and the deadline restarts.
  Contradictory (e.g. "blocked" and "running it now" in one breath, or "blocked" then
  "running it now" with no new user turn in between) -> the PM asks the specialist to
  clarify *before* telling the user anything; a second contradiction goes to the user.
* **deadline** (:func:`tick`): no reply by the deadline -> one nudge to the specialist
  (a ``delegate:`` line tagged ``(deleg <id>)`` so the relay attaches the answer to the
  same item); still nothing at 2x -> the PM tells the user plainly what is stuck.
* **check-in** (:func:`on_user_reply`): yes -> closed; no / a new problem -> reopened so
  the PM re-delegates or escalates; no answer after ``CHIP_DELEG_CHECKIN_HOURS`` (24h) ->
  closed quietly and listed in the daily summary (:func:`summary` / :func:`summary_lines`).

Knobs (env): ``CHIP_DELEG_LEDGER`` (``0``/unset off, ``shadow`` = record + log only,
``1`` = live), ``CHIP_DELEG_DEADLINE_MIN`` (30), ``CHIP_DELEG_CHECKIN_HOURS`` (24),
``CHIP_DELEG_TZ`` (IANA zone for the times shown to users, default ``UTC``).
State: ``$CHIP_CHAT_DATA_DIR/chip_state/deleg_ledger.json`` (bounded, atomic writes).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chip.config import data_dir

FLAG = "CHIP_DELEG_LEDGER"
DEADLINE_ENV = "CHIP_DELEG_DEADLINE_MIN"
CHECKIN_ENV = "CHIP_DELEG_CHECKIN_HOURS"
TZ_ENV = "CHIP_DELEG_TZ"
STATE_NAME = "deleg_ledger.json"
MAX_ITEMS = 400
CLOSED_KEEP_S = 14 * 86400
DECISION_KEEP_S = 7 * 86400

OPEN, CLARIFY, WAITING_USER, CHECKIN, CLOSED = "open", "clarify", "waiting_user", "checkin", "closed"
LIVE_STATES = (OPEN, CLARIFY, WAITING_USER, CHECKIN)
AWAITING_DEV = (OPEN, CLARIFY)

TAG_RE = re.compile(r"\(deleg (d-[0-9a-f]{8})\)")
DECIDED_RE = re.compile(r"^\s*decided\s+(k-[0-9a-f]{6})\b", re.IGNORECASE)

_ON = ("1", "true", "yes", "on", "live")


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def mode(env: Mapping[str, str] | None = None) -> str:
    """``off`` | ``shadow`` | ``live``."""
    v = str(_env(env).get(FLAG, "0") or "0").strip().lower()
    if v == "shadow":
        return "shadow"
    return "live" if v in _ON else "off"


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return mode(env) != "off"


def _num(env: Mapping[str, str] | None, key: str, default: float) -> float:
    try:
        v = float(str(_env(env).get(key, "") or default))
    except ValueError:
        return default
    return v if v > 0 else default


def deadline_s(env: Mapping[str, str] | None = None) -> float:
    return _num(env, DEADLINE_ENV, 30.0) * 60.0


def checkin_timeout_s(env: Mapping[str, str] | None = None) -> float:
    return _num(env, CHECKIN_ENV, 24.0) * 3600.0


def zone(env: Mapping[str, str] | None = None) -> ZoneInfo:
    try:
        return ZoneInfo(str(_env(env).get(TZ_ENV, "") or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def clock(ts: float, env: Mapping[str, str] | None = None) -> str:
    """``3:45 PM PST``-style local time for user-facing lines."""
    dt = datetime.fromtimestamp(ts, zone(env))
    return dt.strftime("%I:%M %p").lstrip("0") + " " + (dt.tzname() or "")


# ----------------------------------------------------------------------------- state


def state_path() -> Path:
    return data_dir() / "chip_state" / STATE_NAME


def _load() -> dict[str, Any]:
    try:
        doc = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc.setdefault("items", {})
    doc.setdefault("decisions", {})
    return doc


def _save(doc: dict[str, Any], now: float) -> None:
    items = doc.get("items") or {}
    for k in [k for k, v in items.items()
              if v.get("state") == CLOSED and now - float(v.get("closed_at") or now) > CLOSED_KEEP_S]:
        items.pop(k, None)
    if len(items) > MAX_ITEMS:
        for k in sorted(items, key=lambda x: float(items[x].get("created_at") or 0))[: len(items) - MAX_ITEMS]:
            items.pop(k, None)
    dec = doc.get("decisions") or {}
    for k in [k for k, v in dec.items() if now - float(v.get("first_seen") or now) > DECISION_KEEP_S]:
        dec.pop(k, None)
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    p = state_path().with_suffix(".lock")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a+", encoding="utf-8") as fh:
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):  # non-POSIX dev boxes: best effort
            pass
        yield


def items(*, live_only: bool = False) -> list[dict[str, Any]]:
    rows = list((_load().get("items") or {}).values())
    if live_only:
        rows = [r for r in rows if r.get("state") in LIVE_STATES]
    return sorted(rows, key=lambda r: float(r.get("created_at") or 0))


def get(item_id: str) -> dict[str, Any] | None:
    row = (_load().get("items") or {}).get(item_id)
    return dict(row) if isinstance(row, dict) else None


def _event(item: dict[str, Any], kind: str, now: float, **extra: Any) -> None:
    ev = {"at": now, "kind": kind}
    ev.update({k: v for k, v in extra.items() if v not in (None, "")})
    item.setdefault("events", []).append(ev)
    item["events"] = item["events"][-30:]


# ----------------------------------------------------------------------------- text


def _norm_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(w) > 2}


def similar(a: str, b: str, threshold: float = 0.6) -> bool:
    wa, wb = _norm_words(a), _norm_words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / max(1, len(wa | wb)) >= threshold or wa <= wb or wb <= wa


_FIELD = re.compile(r"^(claim|status|evidence|next|tool)\s*:", re.IGNORECASE)
_STATUS = re.compile(r"^\s*status\s*:\s*(\w+)", re.IGNORECASE | re.MULTILINE)
_BLOCKED = re.compile(
    r"\b(blocked|can(?:no|['’])?t|cannot|unable to|not able to|won['’]?t be able|refus(?:e|ed|ing)"
    r"|need(?:s)? (?:an? )?(?:explicit )?(?:override|approval|go|sign-?off|decision|permission)"
    r"|waiting (?:on|for) (?:you|your|an? |the )?(?:approval|go|override|operator|decision|sign-?off)"
    r"|outside (?:the |its |our )?(?:\w+ )?window|not allowed|no access)\b",
    re.IGNORECASE,
)
_NOW = re.compile(
    r"\b(running (?:it |that |this |the \w+ )?(?:update |job |script )?now|doing (?:it|that|this) now"
    r"|applying (?:it |that |this )?now|starting (?:it |that |this )?now|kicking (?:it |that )?off now"
    r"|on it now|executing (?:it |that |this )?now|running the [\w -]{1,40} now)\b",
    re.IGNORECASE,
)
_DONE = re.compile(
    r"(?<!once )(?<!when )(?<!after )(?<!until )(?<!be )(?<!get it )\b(done|completed|finished|applied|deployed"
    r"|merged|fixed|resolved|verified|shipped|restarted|succeeded|passed)\b",
    re.IGNORECASE,
)
_FUTURE_DONE = re.compile(r"\b(will|i['’]?ll|going to|gonna|once|when|after)\b[^.!?\n]{0,40}\b(done|applied|deployed|merged|fixed|verified)\b", re.IGNORECASE)
_PROGRESS = re.compile(
    r"\b(on it|starting|started|in progress|working on|queued|i['’]?ll|i will|will (?:drop|post|run|apply|report|deploy|queue|check)"
    r"|kicking off|underway|running)\b",
    re.IGNORECASE,
)


def gist(text: str, limit: int = 240) -> str:
    """First meaningful sentence of a reply (receipt field lines skipped)."""
    lines = [l.strip() for l in str(text or "").splitlines() if l.strip()]
    claim = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("claim:")), "")
    prose = " ".join(l for l in lines if not _FIELD.match(l)) or claim or " ".join(lines)
    prose = re.sub(r"^\[[^\]]{1,32}\]\s*", "", prose)
    m = re.match(r"(.+?[.!?])(\s|$)", prose)
    out = (m.group(1) if m and len(m.group(1)) >= 25 else prose).strip()
    return out if len(out) <= limit else out[: limit - 1].rstrip() + "…"


def classify(text: str) -> str:
    """``done`` | ``blocked`` | ``progress`` | ``contradictory`` | ``unclear`` for one reply."""
    t = str(text or "")
    st = _STATUS.search(t)
    if st:
        s = st.group(1).lower()
        if s in ("done", "ok", "complete", "completed"):
            return "done"
        if s in ("blocked", "refused", "failed"):
            return "contradictory" if _NOW.search(t) else "blocked"
        if s in ("running", "in_progress", "progress", "wip"):
            return "progress"
    blocked = bool(_BLOCKED.search(t))
    now_ = bool(_NOW.search(t))
    done = bool(_DONE.search(t)) and not _FUTURE_DONE.search(t)
    if blocked and now_:
        return "contradictory"
    if blocked:
        return "blocked"
    if done:
        return "done"
    if now_ or _PROGRESS.search(t):
        return "progress"
    return "unclear"


_YES = re.compile(r"^\s*(y|ya|yes|yep|yeah|yup|sure|fixed|it did|it works|works( now)?|all good|resolved|solved|perfect|great|thanks?|thank you|ty)\b", re.IGNORECASE)
_NO = re.compile(r"^\s*(n|no|nope|nah|not (yet|really|fixed)|still|it didn['’]?t|didn['’]?t|doesn['’]?t|isn['’]?t|broken|same (issue|problem)|nothing changed)\b", re.IGNORECASE)


_NEG = re.compile(r"\b(not|still|didn['’]?t|doesn['’]?t|isn['’]?t|broken|fail(?:ed|ing|s)?|worse)\b", re.IGNORECASE)


def parse_yes_no(text: str) -> str | None:
    t = str(text or "").strip()
    if not t or len(t) > 400:
        return None
    if _NO.search(t) or _NEG.search(t):
        return "no"
    if _YES.search(t):
        return "yes"
    return None


# ----------------------------------------------------------------------------- actions


@dataclass
class Action:
    """One thing for the relay to do. ``post`` is the message body (PM voice) or ``""``."""

    kind: str
    item_id: str
    room: str = ""
    thread_id: str = ""
    to: str = "user"  # "user" (PM line in the thread) | "dev" (a delegate: nudge) | "none"
    post: str = ""
    dev: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _action(kind: str, item: Mapping[str, Any], post: str = "", to: str = "user", **extra: Any) -> Action:
    return Action(kind=kind, item_id=str(item.get("id")), room=str(item.get("room") or ""),
                  thread_id=str(item.get("thread_id") or ""), to=to, post=post,
                  dev=str(item.get("dev") or ""), extra=dict(extra))


def _short_ask(item: Mapping[str, Any], n: int = 90) -> str:
    a = " ".join(str(item.get("ask") or "").split())
    return a if len(a) <= n else a[: n - 1].rstrip() + "…"


def ack_text(item: Mapping[str, Any], env: Mapping[str, str] | None = None) -> str:
    due = float(item.get("due_from") or item.get("created_at") or time.time()) + deadline_s(env)
    return f"Handed this to {item.get('dev')}. I'll report back here by {clock(due, env)} at the latest."


def duplicate_text(item: Mapping[str, Any], env: Mapping[str, str] | None = None) -> str:
    return (f"That's already with {item.get('dev')} (since {clock(float(item.get('created_at') or 0), env)}), "
            "so I didn't hand it over twice. I'll report back here.")


def nudge_text(item: Mapping[str, Any]) -> str:
    return (f"delegate:{item.get('dev')} Follow-up on \"{_short_ask(item, 160)}\": please reply with one of "
            "done (what you did + evidence), blocked (why + what you need), or running (with an ETA). "
            f"(deleg {item.get('id')})")


def clarify_text(item: Mapping[str, Any], before: str, after: str) -> str:
    return (f"delegate:{item.get('dev')} Your replies on \"{_short_ask(item, 120)}\" disagree: "
            f"\"{gist(before, 110)}\" vs \"{gist(after, 110)}\". Which is true right now? Reply with exactly one of "
            f"done (with evidence), blocked (why), or running (with an ETA). (deleg {item.get('id')})")


def new_id(room: str, dev: str, ask: str, now: float) -> str:
    return "d-" + hashlib.sha256(f"{room}|{dev}|{ask}|{now}".encode()).hexdigest()[:8]


def open_or_attach(
    room: str,
    dev: str,
    ask: str,
    *,
    user: str = "",
    user_id: str = "",
    thread_id: str = "",
    thread_key: str = "",
    source: str = "",
    from_persona: str = "pm",
    env: Mapping[str, str] | None = None,
    now: float | None = None,
) -> tuple[dict[str, Any], str]:
    """Record a hand-off. Returns ``(item, outcome)`` with outcome:

    * ``created`` — new item; post :func:`ack_text` and run the hop.
    * ``duplicate`` — the same ask is already in flight with this specialist; skip the hop.
    * ``attached`` — a tagged follow-up (nudge/clarify) or a re-delegation of a live item
      after the user spoke again; run the hop, no new ack.
    """
    ts = time.time() if now is None else float(now)
    dev = str(dev or "").strip().lower()
    tag = TAG_RE.search(str(ask or ""))
    with _locked():
        doc = _load()
        its = doc["items"]
        if tag and tag.group(1) in its:
            item = its[tag.group(1)]
            _event(item, "follow_up_hop", ts)
            if thread_id and not item.get("thread_id"):
                item["thread_id"] = thread_id
            _save(doc, ts)
            return dict(item), "attached"
        dl = deadline_s(env)
        for item in sorted(its.values(), key=lambda r: -float(r.get("created_at") or 0)):
            if item.get("state") not in LIVE_STATES or item.get("room") != room or item.get("dev") != dev:
                continue
            same_thread = bool(thread_id) and item.get("thread_id") == thread_id
            if not same_thread and not similar(item.get("ask", ""), ask):
                continue
            if not same_thread and ts - float(item.get("created_at") or 0) > dl:
                continue
            if item.get("state") in AWAITING_DEV and not item.get("user_turn_since"):
                _event(item, "duplicate_suppressed", ts, ask=_short_ask({"ask": ask}, 120))
                item["duplicates"] = int(item.get("duplicates") or 0) + 1
                _save(doc, ts)
                return dict(item), "duplicate"
            # The user spoke again (new info / GO / "it didn't work"): same item, fresh hop.
            item.update(state=OPEN, due_from=ts, nudged_at=None, escalated_at=None,
                        user_turn_since=False, last_class=None, ask_latest=ask)
            if thread_id and not item.get("thread_id"):
                item["thread_id"] = thread_id
            _event(item, "redelegated", ts)
            _save(doc, ts)
            return dict(item), "attached"
        iid = new_id(room, dev, ask, ts)
        item = {
            "id": iid, "room": room, "dev": dev, "ask": str(ask or "")[:600], "from": from_persona,
            "user": user, "user_id": user_id, "thread_id": thread_id, "thread_key": thread_key,
            "source": source, "created_at": ts, "due_from": ts, "deadline_s": dl, "state": OPEN,
            "nudged_at": None, "escalated_at": None, "clarified": False, "last_class": None,
            "last_reply": "", "user_turn_since": False, "checkin_at": None, "closed_at": None,
            "close_reason": "", "outcome": "", "duplicates": 0, "events": [],
        }
        _event(item, "created", ts, source=source)
        its[iid] = item
        _save(doc, ts)
        return dict(item), "created"


def set_thread(item_id: str, thread_id: str, *, now: float | None = None) -> None:
    if not item_id or not thread_id:
        return
    ts = time.time() if now is None else float(now)
    with _locked():
        doc = _load()
        item = doc["items"].get(item_id)
        if item and not item.get("thread_id"):
            item["thread_id"] = str(thread_id)
            _save(doc, ts)


def on_dev_reply(item_id: str, text: str, *, env: Mapping[str, str] | None = None,
                 now: float | None = None) -> Action | None:
    """Specialist answered on a tracked item -> the PM's next line (see module doc)."""
    ts = time.time() if now is None else float(now)
    with _locked():
        doc = _load()
        item = doc["items"].get(item_id)
        if not item or item.get("state") == CLOSED:
            return None
        cls = classify(text)
        prev_cls, prev_reply = item.get("last_class"), item.get("last_reply") or ""
        flip = (prev_cls == "blocked" and cls in ("progress", "done") and bool(_NOW.search(text) or cls == "done")
                and not item.get("user_turn_since"))
        _event(item, "dev_reply", ts, cls=cls, gist=gist(text, 160))
        dev = item.get("dev")
        action: Action
        if cls == "contradictory" or flip:
            if not item.get("clarified"):
                item.update(state=CLARIFY, clarified=True, due_from=ts, nudged_at=None, escalated_at=None)
                before = prev_reply if flip else text
                after = text
                action = _action("clarify", item, clarify_text(item, before, after), to="dev")
            else:
                item.update(state=WAITING_USER)
                action = _action("mixed", item, (
                    f"{dev} gave me mixed answers on this (latest: \"{gist(text)}\"), so I'm treating it as "
                    "**not done**. Next: tell me here whether to retry, wait, or hand it to someone else."))
        elif cls == "done":
            item.update(state=CHECKIN, checkin_at=ts, outcome=gist(text, 300))
            action = _action("outcome", item, (
                f"Update from {dev}: {gist(text)}\n\nDid that fix it? Reply **yes** or **no** here."))
        elif cls == "blocked":
            item.update(state=WAITING_USER, outcome=gist(text, 300))
            action = _action("blocked", item, (
                f"{dev} is blocked: {gist(text)}\n\nNext: reply here with how you want to go ahead "
                "(for example an explicit GO/override, or \"wait\"), and I'll pass it on."))
        elif cls == "progress":
            item.update(state=OPEN, due_from=ts, nudged_at=None, escalated_at=None)
            action = _action("progress", item, (
                f"{dev} is on it: {gist(text)}\nI'll check back by {clock(ts + deadline_s(env), env)}."))
        else:
            item.update(state=OPEN)
            due = float(item.get("due_from") or ts) + deadline_s(env)
            action = _action("unclear", item, (
                f"{dev} replied but didn't say whether it's done or blocked: \"{gist(text)}\"\n"
                f"I'll follow up with them by {clock(due, env)}."))
        item["last_class"] = cls if cls != "contradictory" else "blocked"
        item["last_reply"] = str(text or "")[:800]
        item["user_turn_since"] = False
        _save(doc, ts)
        return action


def item_for_thread(thread_id: str, *, states: tuple[str, ...] = LIVE_STATES) -> dict[str, Any] | None:
    if not thread_id:
        return None
    rows = [r for r in items() if r.get("thread_id") == str(thread_id) and r.get("state") in states]
    return dict(rows[-1]) if rows else None


def on_user_reply(thread_id: str, text: str, *, env: Mapping[str, str] | None = None,
                  now: float | None = None) -> Action | None:
    """A human posted in a thread that has a live item.

    * ``closed_yes`` — the check-in got a yes: post the short close line, skip the model turn.
    * ``reopened`` — the check-in got a no / a new problem: item reopened; the caller lets the
      PM turn run with :func:`reopen_context` prepended so it re-delegates or escalates.
    * ``noted`` — any other turn on a live item (the next delegate is a re-delegation).
    """
    ts = time.time() if now is None else float(now)
    with _locked():
        doc = _load()
        cand = [r for r in doc["items"].values()
                if thread_id and r.get("thread_id") == str(thread_id) and r.get("state") in LIVE_STATES]
        if not cand:
            return None
        item = max(cand, key=lambda r: float(r.get("created_at") or 0))
        if item.get("state") == CHECKIN:
            yn = parse_yes_no(text)
            if yn == "yes":
                item.update(state=CLOSED, closed_at=ts, close_reason="user confirmed fixed")
                _event(item, "closed", ts, why="yes")
                _save(doc, ts)
                return _action("closed_yes", item, "Great, glad that's sorted. Closing this one.")
            if yn == "no" or len(str(text or "").strip()) > 3:
                item.update(state=OPEN, due_from=ts, nudged_at=None, escalated_at=None,
                            user_turn_since=True, reopened=int(item.get("reopened") or 0) + 1,
                            user_feedback=str(text or "")[:400])
                _event(item, "reopened", ts, why=yn or "new message")
                _save(doc, ts)
                return _action("reopened", item, "", to="none")
        item["user_turn_since"] = True
        _event(item, "user_turn", ts)
        _save(doc, ts)
        return _action("noted", item, "", to="none")


def reopen_context(item: Mapping[str, Any]) -> str:
    return (f"(follow-up: earlier {item.get('dev')} reported \"{gist(item.get('outcome') or '', 160)}\" on "
            f"\"{_short_ask(item, 120)}\", and the user says it is NOT fixed. Re-delegate to the right specialist "
            "with what the user said, or tell the user plainly what happens next.)")


def tick(*, env: Mapping[str, str] | None = None, now: float | None = None,
         force_due: str = "") -> list[Action]:
    """Deadline sweep: nudges, escalations to the user, quiet check-in closes.

    ``force_due`` (operator/test tool) treats that item as one deadline older than it is.
    """
    ts = time.time() if now is None else float(now)
    out: list[Action] = []
    with _locked():
        doc = _load()
        changed = False
        for item in doc["items"].values():
            st = item.get("state")
            dl = float(item.get("deadline_s") or deadline_s(env))
            skew = dl if force_due and item.get("id") == force_due else 0.0
            if st in AWAITING_DEV:
                waited = ts + skew - float(item.get("due_from") or item.get("created_at") or ts)
                if waited >= 2 * dl and item.get("nudged_at") and not item.get("escalated_at"):
                    item.update(escalated_at=ts, state=WAITING_USER)
                    _event(item, "escalated", ts)
                    out.append(_action("escalate", item, (
                        f"No answer from {item.get('dev')} on \"{_short_ask(item)}\" after "
                        f"{int(waited // 60)} min, even after a nudge. It's stuck with them, not done. "
                        "Next: reply here if you want me to retry, wait, or hand it to someone else.")))
                    changed = True
                elif waited >= dl and not item.get("nudged_at"):
                    item["nudged_at"] = ts
                    if skew:  # keep the 2x clock honest after a forced nudge
                        item["due_from"] = float(item.get("due_from") or ts) - skew
                    _event(item, "nudged", ts)
                    out.append(_action("nudge", item, nudge_text(item), to="dev"))
                    changed = True
            elif st == CHECKIN:
                if ts + skew - float(item.get("checkin_at") or ts) >= checkin_timeout_s(env):
                    item.update(state=CLOSED, closed_at=ts, close_reason="no answer to check-in")
                    _event(item, "closed", ts, why="checkin timeout")
                    out.append(_action("quiet_close", item, "", to="none"))
                    changed = True
        if changed:
            _save(doc, ts)
    return out


def close(item_id: str, reason: str, *, now: float | None = None) -> bool:
    ts = time.time() if now is None else float(now)
    with _locked():
        doc = _load()
        item = doc["items"].get(item_id)
        if not item or item.get("state") == CLOSED:
            return False
        item.update(state=CLOSED, closed_at=ts, close_reason=reason[:200])
        _event(item, "closed", ts, why=reason[:80])
        _save(doc, ts)
        return True


# ----------------------------------------------------------------------------- decisions


def track_decisions(waiting: list[str], *, now: float | None = None) -> list[dict[str, Any]]:
    """Remember "waiting on you" decisions so they carry over day to day until decided
    (``decided <id>`` from the operator) or 7 days pass. Returns the open ones."""
    ts = time.time() if now is None else float(now)
    with _locked():
        doc = _load()
        dec = doc["decisions"]
        for w in waiting:
            text = " ".join(str(w).split())
            if not text:
                continue
            key = "k-" + hashlib.sha256(re.sub(r"\(job [^)]*\)", "", text).strip().lower().encode()).hexdigest()[:6]
            row = dec.setdefault(key, {"id": key, "text": text[:240], "first_seen": ts, "decided_at": None})
            row["last_seen"] = ts
        _save(doc, ts)
        return [dict(r) for r in sorted(dec.values(), key=lambda r: float(r.get("first_seen") or 0))
                if not r.get("decided_at")]


def decide(key: str, *, now: float | None = None) -> bool:
    ts = time.time() if now is None else float(now)
    with _locked():
        doc = _load()
        row = doc["decisions"].get(key)
        if not row or row.get("decided_at"):
            return False
        row["decided_at"] = ts
        _save(doc, ts)
        return True


# ----------------------------------------------------------------------------- summary


def summary(start_ts: float, end_ts: float, *, env: Mapping[str, str] | None = None,
            now: float | None = None) -> dict[str, Any]:
    """Live items (stale ones flagged) plus what closed inside ``[start_ts, end_ts)``."""
    ts = time.time() if now is None else float(now)
    live, closed_quiet, closed_ok = [], [], 0
    for r in items():
        st = r.get("state")
        if st in LIVE_STATES:
            age = ts - float(r.get("created_at") or ts)
            stale = (st in AWAITING_DEV and bool(r.get("nudged_at"))) or st == WAITING_USER or age > 86400
            live.append({"id": r.get("id"), "dev": r.get("dev"), "state": st, "ask": _short_ask(r, 80),
                         "age_h": round(age / 3600, 1), "stale": stale})
        elif st == CLOSED and start_ts <= float(r.get("closed_at") or 0) < end_ts:
            if r.get("close_reason") == "no answer to check-in":
                closed_quiet.append({"id": r.get("id"), "dev": r.get("dev"), "ask": _short_ask(r, 80)})
            else:
                closed_ok += 1
    return {"live": live, "closed_quiet": closed_quiet, "closed": closed_ok}


_STATE_WORDS = {OPEN: "waiting on the specialist", CLARIFY: "asked the specialist to clarify",
                WAITING_USER: "waiting on you", CHECKIN: "asked you if it's fixed"}


def summary_lines(s: Mapping[str, Any]) -> list[str]:
    out = []
    for r in s.get("live") or []:
        flag = "STALE · " if r.get("stale") else ""
        out.append(f"{flag}`{r.get('id')}` {r.get('dev')}: \"{r.get('ask')}\" ({_STATE_WORDS.get(r.get('state'), r.get('state'))}, {r.get('age_h')}h)")
    for r in s.get("closed_quiet") or []:
        out.append(f"closed quietly (no answer to the check-in): `{r.get('id')}` {r.get('dev')}: \"{r.get('ask')}\"")
    return out
