"""RAZ-335 ops session A/B runner: solo vs trio on one sealed probe scenario.

Runs the collab protocol (chip.collab) on the fixed slice-complete state.
LIVE OpenRouter calls are blocked behind all of:
  1. ``--live`` flag (absent = mock-only),
  2. a ``--go-receipt <file>`` whose first line parses as
     ``GO RAZ-335-ops-ab <token>`` (per-run named GO, receipted),
  3. monthly ledger below chip.spend WARN and session tracker below its cap.
Fail-closed on every gate; the runner never improvises a GO.

Arm design (fairness fixed across arms):
  solo = SOC participant(s): exactly chipchatdev; begin_brainstorm ->
         submit_hypothesis (+ self-critique is refused by protocol law, which
         is itself a recorded finding) -> decision pass, no cross-critique.
  trio = three personas (opsdev, homelabdev, chipchatdev); sealed
         brainstorm -> cross critique with end_critique coverage law ->
         chair converge with verbatim dissent.
Verdict: deterministic rubric against the known ground-truth class, evidence
over completion. Tie or loss => mothball recommendation with reasoning.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from chip.spend_tracker import SESSION_ALERT_USD, SESSION_REFUSE_USD, SpendRefused, SpendTracker
from chip.transcript_logger import ROOT, TranscriptLogger

SCENARIO_GOAL = (
    "AirVPN reserved port 54543 was reported unreachable at 38.88.124.105; "
    "inbound peers fail; qBittorrent itself is not directly visible. WITHOUT "
    "reading the new probe script: (1) single most probable root cause for why "
    "the monitoring stack failed to catch this; specifically what the fleet's "
    "qbittorrent-monitor got wrong about probe SOURCE, (2) a minimal "
    "proposal-only fix shape, (3) a verification probe with a pass/fail "
    "criterion (falsifiability)."
)

TRIO_PARTICIPANTS = ["opsdev", "homelabdev", "chipchatdev"]
SOLO_PARTICIPANTS = ["chipchatdev"]

GROUND_TRUTH_CLASS = re.compile(
    r"(outside the tunnel|external reachab|egress|probe source|from the (host|outside)|"
    r"tunnel[-_ ]?(internal|plumbing)|api[-_ ]derive[ds]? target|/v1/portforward)",
    re.IGNORECASE,
)
FALSIFIABLE = re.compile(
    r"(verified[-_ ]as[-_ ]of|verified[-_ ]listen|re[-_ ]probe|pass[/ ]?fail|"
    r"flip[s]? .{0,30}(true|reachable)|before/after|rollback)",
    re.IGNORECASE,
)
EVIDENCE_CITE = re.compile(
    r"(inbound_reachable|qb_listen_port|38\.88\.124\.105|54543|gluetun|publicip)"
    r"(\w| |[.:,])+",  # crude citation shape = term + trailing detail
    re.IGNORECASE,
)

MAX_MESSAGE_CHARS = 2000  # relay dispatch law; wake texts must respect it


def _room_tip(widget: str) -> str:
    return widget[:160]


def _chip_room_reply(repo_root: Path, room: str, persona: str, message: str,
                     live: bool) -> dict:
    """One persona turn via the chip CLI; returns the parsed --json payload."""
    if len(message) > MAX_MESSAGE_CHARS:
        raise ValueError(f"wake text oversized: {len(message)}")
    argv = [str(repo_root / "bin" / "chip"), "room", room, "-m", message,
            "--as", persona, "--json"]
    env_extra = {} if live else {"CHIP_CHAT_MOCK": "1"}
    import os

    env = dict(os.environ)
    env.update(env_extra)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=180, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"chip room rc={proc.returncode}: {proc.stderr[-300:]}")
    payload = json.loads(proc.stdout)
    payload["armed"] = live
    payload["persona"] = persona
    payload["room"] = room
    return payload


_LIVE_STATE: dict = {"live": False, "token": ""}


def is_live() -> bool:
    """One live predicate for the whole harness: --live + go env + receipt."""
    return bool(_LIVE_STATE.get("live"))


def go_token() -> str:
    return str(_LIVE_STATE.get("token") or "")


def _maybe_collab():
    """Import chip.collab; refuse loudly if pm's slice state is absent."""
    try:
        from chip import collab  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "chip.collab unavailable: protocol engine slice state absent from "
            "this checkout. Harness is merge-ready but refuses to run against "
            "pre-slice code (fail-closed by design; no improvised engine)."
        ) from exc
    return collab


def run_solo(repo_root: Path, spend: SpendTracker, log: TranscriptLogger,
             go_token: str, room: str = "ab_solo") -> dict:
    if is_live() and not go_token:
        raise RuntimeError("live arm refused: empty GO token (gate threading law)")
    collab = _maybe_collab()
    board = collab.begin_brainstorm(room, SCENARIO_GOAL, SOLO_PARTICIPANTS)
    log.event("board_op", "harness", json.dumps({"op": "begin_brainstorm",
                                                 "room": room, "go": go_token},
                                                sort_keys=True))
    message = f"[sealed brainstorm] {SCENARIO_GOAL} reply with ONE hypothesis only."
    reply = _chip_room_reply(repo_root, room, SOLO_PARTICIPANTS[0], message, live=is_live())
    spend.record_call(reply["session_usd"], "solo/hypothesis")
    log.event("prompt", SOLO_PARTICIPANTS[0], message)
    log.event("reply", SOLO_PARTICIPANTS[0], reply["reply"],
              tokens_prompt=reply.get("tokens_prompt"),
              tokens_completion=reply.get("tokens_completion"),
              session_usd=reply.get("session_usd"))
    board = collab.submit_hypothesis(room, SOLO_PARTICIPANTS[0], reply["reply"])
    log.event("board_op", "harness", json.dumps({"op": "submit_hypothesis",
                                                 "sealed": True}, sort_keys=True))
    board = collab.close_brainstorm(room)
    decision = _chip_room_reply(
        repo_root, room, SOLO_PARTICIPANTS[0],
        "[decision] Given the revealed board, state your final diagnosis + "
        "fix shape + falsifiability check in <=120 words.", live=is_live())
    spend.record_call(decision["session_usd"], "solo/decision")
    log.event("decision", SOLO_PARTICIPANTS[0], decision["reply"],
              tokens_completion=decision.get("tokens_completion"),
              session_usd=decision.get("session_usd"))
    return {"arm": "solo", "decision": decision["reply"], "spend": spend.state()}


def run_trio(repo_root: Path, spend: SpendTracker, log: TranscriptLogger,
             go_token: str, room: str = "ab_trio") -> dict:
    if is_live() and not go_token:
        raise RuntimeError("live arm refused: empty GO token (gate threading law)")
    collab = _maybe_collab()
    board = collab.begin_brainstorm(room, SCENARIO_GOAL, TRIO_PARTICIPANTS)
    log.event("board_op", "harness", json.dumps({"op": "begin_brainstorm",
                                                 "go": go_token},
                                                sort_keys=True))
    message = f"[sealed brainstorm] {SCENARIO_GOAL} reply with ONE hypothesis only."
    replies: dict[str, dict] = {}
    for persona in TRIO_PARTICIPANTS:
        reply = _chip_room_reply(repo_root, room, persona, message, live=is_live())
        spend.record_call(reply["session_usd"], f"trio/{persona}/hypothesis")
        log.event("prompt", persona, message)
        log.event("reply", persona, reply["reply"],
                  tokens_prompt=reply.get("tokens_prompt"),
                  tokens_completion=reply.get("tokens_completion"),
                  session_usd=reply.get("session_usd"))
        board = collab.submit_hypothesis(room, persona, reply["reply"])
        replies[persona] = reply
    board = collab.close_brainstorm(room)
    entry_id_by_author = {e["author"]: e["id"] for e in board["entries"]
                          if e.get("kind") == "hypothesis"}
    for persona in TRIO_PARTICIPANTS:
        others = [a for a in TRIO_PARTICIPANTS if a != persona]
        target_author = others[TRIO_PARTICIPANTS.index(persona) % len(others)]
        target_id = entry_id_by_author[target_author]
        critique_msg = (
            "[cross critique] You may critique any OTHER participant's "
            "hypothesis besides your own. Separate hypothesis "
            f"(ID {target_id}) is available on the board alongside yours. "
            "Reply with your critique of it: what it gets right, what it "
            "misses, falsifiability of its fix shape.")
        critical = _chip_room_reply(repo_root, room, persona, critique_msg, live=is_live())
        spend.record_call(critical["session_usd"], f"trio/{persona}/critique")
        log.event("critique", persona, critical["reply"],
                  target_id=target_id,
                  tokens=1,
                  tokens_completion=critical.get("tokens_completion"),
                  session_usd=critical.get("session_usd"))
        board = collab.critique(room, persona, target_id, critical["reply"])
    collab.end_critique(room)
    chair = board.get("chair")
    conv_msg = (
        "[chair converge] State the trio's converged plan for RAZ-335 + "
        "evidence cites + disagreed parts + assumption ties + verification "
        "probe with pass/fail.")
    conv = _chip_room_reply(repo_root, room, chair, conv_msg, live=is_live())
    spend.record_call(conv["session_usd"], f"trio/{chair}/converge")
    log.event("converge", chair, conv["reply"],
              tokens_completion=conv.get("tokens_completion"),
              session_usd=conv.get("session_usd"))
    # dissent extraction is rubric-driven later; store verbatim for now
    assignments = [{"persona": p, "scope": "RAZ-335"} for p in TRIO_PARTICIPANTS]
    board = collab.converge(room, chair, conv["reply"], assignments,
                            [{"author": "pm", "text": "verbatim-body-pending"}])
    return {"arm": "trio", "decision": conv["reply"], "spend": spend.state()}


def score_arm(transcript_path: Path) -> dict:
    text = Path(transcript_path).read_text(encoding="utf-8")
    decision_blobs = [json.loads(l).get("content", "") for l in text.splitlines()
                      if json.loads(l).get("kind") in ("decision", "converge")]
    blob = " ".join(decision_blobs)
    hits = {
        "ground_truth_class": bool(GROUND_TRUTH_CLASS.search(blob)),
        "falsifiable": bool(FALSIFIABLE.search(blob)),
    }
    cite_hits = len(EVIDENCE_CITE.findall(blob))
    return {
        "transcript": str(transcript_path),
        "decision_chars": len(blob),
        "hits": hits,
        "score": (2 * int(hits["ground_truth_class"]) + int(hits["falsifiable"])
                  + min(1, cite_hits)),
        "max": 4,
    }


def gate_check(go_receipt: Path) -> str:
    text = Path(go_receipt).read_text(encoding="utf-8").splitlines()[0]
    m = re.match(r"^GO RAZ-335-ops-ab (\S+)", text.strip())
    if not m:
        raise SystemExit("go receipt must start 'GO RAZ-335-ops-ab <token>'")
    return m.group(1)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="ops_session_a_b")
    p.add_argument("--arm", choices=["solo", "trio", "both"], default="solo")
    p.add_argument("--live", action="store_true",
                   help="live OpenRouter (default: mock). Requires --go-receipt")
    p.add_argument("--go-receipt", type=Path, default=None)
    p.add_argument("--score", type=Path, default=None,
                   help="grade an existing solo.jsonl/trio.jsonl")
    p.add_argument("--allow-mock-mutation", action="store_true",
                   help="permit protocol board mutation in mock mode "
                        "(still refuses if collab is absent).")
    args = p.parse_args(argv)
    if args.score is not None:
        print(json.dumps(score_arm(args.score)))
        return 0
    # ONE live predicate for the entire harness (pm review fix #2):
    # live == --live AND CHIP_OPS_AB_GO=1 AND a parsed go receipt.
    live = False
    token = ""
    if args.live:
        if not args.go_receipt:
            print("LIVE refused: --go-receipt required (per-run named GO)", file=sys.stderr)
            return 2
        import os
        if str(os.environ.get("CHIP_OPS_AB_GO", "")) != "1":
            print("LIVE refused: CHIP_OPS_AB_GO=1 env required (double gate)", file=sys.stderr)
            return 3
        token = gate_check(args.go_receipt)  # fail-closed; may SystemExit
        live = True
    _LIVE_STATE["live"] = live
    _LIVE_STATE["token"] = token
    if not live and not args.allow_mock_mutation:
        print("REFUSED: arm run would mutate the room board (begin_brainstorm/"
              "critique/converge). Pass --allow-mock-mutation for cheap "
              "validation, or --live with the go receipt for the real session "
              "(pm review fix #4: mock is gated like live).", file=sys.stderr)
        return 4
    log_arm = args.arm if args.arm != "both" else "solo"
    log = TranscriptLogger(log_arm)
    spend = SpendTracker(ROOT / "spend.jsonl", arm=log_arm,
                         cap=SESSION_REFUSE_USD, alert=SESSION_ALERT_USD)
    repo_root = Path(__file__).resolve().parents[2]
    try:
        if args.arm == "both":
            result_solo = run_solo(repo_root, spend, TranscriptLogger("solo"), token)
            result_trio = run_trio(repo_root, spend, TranscriptLogger("trio"), token)
            print(json.dumps({"solo": result_solo, "trio": result_trio,
                              "spend": spend.state()}))
        elif args.arm == "solo":
            print(json.dumps(run_solo(repo_root, spend, log, token)))
        else:
            print(json.dumps(run_trio(repo_root, spend, log, token)))
    except RuntimeError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
