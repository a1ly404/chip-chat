"""FROZEN accuracy scorer — compare harness observations to hand labels only.

Iteration PRs must not edit this module. Label changes belong in
``fixtures/ladder_accuracy/v2/labels.jsonl`` with a justification commit.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def _match(observed: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, str]:
    route = expected.get("route") or observed.get("route")
    if route and observed.get("route") != route:
        return False, f"route {observed.get('route')}!={route}"
    for key, want in expected.items():
        if key in ("route", "corpus_class", "first_try_ok"):
            continue
        got = observed.get(key)
        if key == "note_substr":
            if str(want) not in str(observed.get("note") or ""):
                return False, f"note missing {want!r}"
        elif key == "missing":
            if list(got or []) != list(want):
                return False, f"missing {got}!={want}"
        elif got != want:
            return False, f"{key} {got}!={want}"
    return True, "ok"


def score_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Each row: {id, corpus_class, observed, expected, first_try_ok (label)}."""
    results = []
    by_class: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        ok, reason = _match(row["observed"], row["expected"])
        if "first_try" in row["observed"]:
            want_first = row["expected"].get("first_try", row.get("label_first_try_ok", True))
            hit = ok and row["observed"].get("first_try") == want_first
        else:
            hit = ok
        rec = {
            "id": row["id"],
            "corpus_class": row.get("corpus_class"),
            "hit": hit,
            "match": ok,
            "reason": reason,
            "observed": row["observed"],
            "expected": row["expected"],
        }
        results.append(rec)
        by_class[str(row.get("corpus_class") or "unknown")].append(rec)

    n = len(results)
    first_hits = sum(1 for r in results if r["hit"])
    per_class = {}
    for klass, items in sorted(by_class.items()):
        per_class[klass] = {
            "n": len(items),
            "first_try_accuracy": round(sum(1 for i in items if i["hit"]) / len(items), 3),
            "match_accuracy": round(sum(1 for i in items if i["match"]) / len(items), 3),
        }
    weakest = min(per_class.items(), key=lambda kv: kv[1]["first_try_accuracy"])[0] if per_class else None
    confusion: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        if not r["hit"]:
            confusion[r["corpus_class"]][r["reason"][:60]] += 1
    return {
        "cases": n,
        "first_try_accuracy": round(first_hits / n, 3) if n else None,
        "per_class": per_class,
        "weakest_class": weakest,
        "misses": [r for r in results if not r["hit"]],
        "confusion_by_class": {k: dict(v) for k, v in confusion.items()},
        "results": results,
    }
