"""Offline A/B of one persona versus a three-persona protocol round."""

from __future__ import annotations

from typing import Any, Callable

from chip import boards, collab


def run_ab(
    room: str,
    problem: str,
    personas: list[str],
    *,
    clock: Callable[[], int] | None = None,
    go_ticket: str | None = None,
) -> dict[str, Any]:
    from chip import topology
    from chip.collab import require_protocol_go

    require_protocol_go(go_ticket)
    topology.enforce_collab_ab()
    if len(personas) != 3:
        raise ValueError("trio arm needs 3 personas")
    solo_name, *others = personas
    solo = _solo(f"{room}-solo", solo_name, problem)
    trio = _trio(f"{room}-trio", problem, personas)
    comparison = {
        "solo": solo,
        "trio": trio,
        "evidence": solo["evidence"] + trio["evidence"],
        "hypotheses": {"solo": solo["hypotheses"], "trio": trio["hypotheses"]},
        "critiques_that_killed": trio["critiques_that_killed"],
        "time_ms": int(clock()) if clock else 0,
        "tokens_by_persona": _merge_tokens(solo["tokens_by_persona"], trio["tokens_by_persona"]),
        "grade": "pass"
        if trio["hypotheses"] >= solo["hypotheses"] and trio["cross_critiques"] >= 1
        else "fail",
    }
    board = boards.load(room)
    board["comparison"] = comparison
    boards.save(board)
    return board


def _merge_tokens(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    out = dict(left)
    for key, value in right.items():
        out[key] = out.get(key, 0) + value
    return out


def _solo(room: str, actor: str, problem: str) -> dict[str, Any]:
    board = boards.protocol_turn(boards.load(room), actor=actor, body=problem)
    return {
        "hypotheses": 1,
        "evidence": 1,
        "critiques_that_killed": 0,
        "cross_critiques": 0,
        "tokens_by_persona": {actor: len(problem)},
        "version": board["version"],
    }


def _trio(room: str, problem: str, personas: list[str]) -> dict[str, Any]:
    tokens: dict[str, int] = {personas[0]: len(problem)}
    board = collab.begin_brainstorm(room, problem, personas)
    for actor in personas[1:]:
        body = f"{actor} view of {problem}"
        board = collab.submit_hypothesis(room, actor, body)
        tokens[actor] = tokens.get(actor, 0) + len(body)
    collab.close_brainstorm(room)
    board = boards.load(room)
    hypos = [entry for entry in board["entries"] if entry.get("kind") == "hypothesis"]
    # Each persona critiques the next persona's hypothesis.
    killed = 0
    for index, actor in enumerate(personas):
        target = hypos[(index + 1) % len(hypos)]
        body = f"critique from {actor}"
        if actor == personas[-1]:
            body = f"kill: {body}"
        collab.critique(room, actor, target["id"], body)
        tokens[actor] = tokens.get(actor, 0) + len(body)
    collab.end_critique(room)
    board = boards.load(room)
    killed = 0
    for entry in list(board["entries"]):
        if entry.get("kind") == "critique" and str(entry.get("body") or "").startswith("kill:"):
            collab.reject_hypothesis(room, str(entry["target_id"]), str(entry["body"]))
            killed += 1
    board = boards.load(room)
    dissent = [
        {"author": entry["author"], "text": entry["body"]}
        for entry in board["entries"]
        if entry.get("kind") == "hypothesis" and entry.get("author") != personas[0]
    ]
    collab.converge(
        room,
        personas[0],
        hypos[0]["body"],
        [{"persona": personas[1], "scope": f"{room}-scope"}],
        dissent,
    )
    evidence = sum(1 for entry in boards.load(room)["entries"] if str(entry.get("body") or "").strip())
    return {
        "hypotheses": len(hypos),
        "evidence": evidence,
        "critiques_that_killed": killed,
        "cross_critiques": len(personas),
        "tokens_by_persona": tokens,
    }
