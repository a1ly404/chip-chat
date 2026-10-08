#!/usr/bin/env python3
"""Run frozen v2 accuracy fixtures (canned LLM only) and write scoreboard receipts.

Labels: fixtures/ladder_accuracy/v2/labels.jsonl (frozen; separate commit to change).
Inputs + canned LLM: fixtures/ladder_accuracy/v2/fixtures.jsonl

Do not edit ladder_accuracy_score.py in iteration commits.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chip import config  # noqa: E402
from chip_relay.ladder_accuracy_harness import run_fixture  # noqa: E402
import ladder_accuracy_score as score  # noqa: E402

V2_DIR = config.REPO_ROOT / "fixtures" / "ladder_accuracy" / "v2"


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(json.loads(line))
    return rows


def merge_fixtures_labels(fixtures: list[dict], labels: list[dict]) -> list[dict]:
    by_id = {str(r["id"]): r for r in labels}
    out = []
    for fx in fixtures:
        lid = str(fx["id"])
        lab = by_id.get(lid)
        if not lab:
            raise KeyError(f"missing label for fixture {lid}")
        out.append({**fx, "corpus_class": lab.get("corpus_class"), "expected": lab["expected"], "label_first_try_ok": lab.get("first_try_ok", True)})
    return out


def run_all(merged: list[dict], *, data_root: Path, repo_root: Path) -> list[dict]:
    rows = []
    for fx in merged:
        observed = run_fixture(fx, data_root=data_root, repo_root=repo_root)
        rows.append(
            {
                "id": fx["id"],
                "corpus_class": fx.get("corpus_class"),
                "observed": observed,
                "expected": fx["expected"],
                "label_first_try_ok": fx.get("label_first_try_ok", True),
            }
        )
    return rows


def render_markdown(report: dict, *, tag: str) -> str:
    lines = [
        f"# Ladder accuracy run ({tag})",
        "",
        f"- cases: **{report['cases']}**",
        f"- first-try accuracy: **{report['first_try_accuracy']:.1%}**" if report.get("first_try_accuracy") is not None else "",
        f"- weakest class: `{report.get('weakest_class')}`",
        "",
        "## Per-class",
        "",
        "| class | n | first-try % | field match % |",
        "| --- | --- | --- | --- |",
    ]
    for klass, m in sorted(report.get("per_class", {}).items()):
        lines.append(f"| {klass} | {m['n']} | {m['first_try_accuracy']:.0%} | {m['match_accuracy']:.0%} |")
    lines.extend(["", "## Misses", ""])
    for m in report.get("misses", [])[:40]:
        lines.append(f"- `{m['id']}` ({m['corpus_class']}): {m['reason']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixtures", type=Path, default=V2_DIR / "fixtures.jsonl")
    ap.add_argument("--labels", type=Path, default=V2_DIR / "labels.jsonl")
    ap.add_argument("--out-dir", type=Path, default=config.REPO_ROOT / "logs" / "accuracy")
    ap.add_argument("--tag", default=time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    ap.add_argument("--data-root", type=Path, default=None)
    args = ap.parse_args(argv)

    merged = merge_fixtures_labels(load_jsonl(args.fixtures), load_jsonl(args.labels))
    data_root = args.data_root or (args.out_dir / f"scratch-{args.tag}")
    data_root.mkdir(parents=True, exist_ok=True)
    scored = score.score_rows(run_all(merged, data_root=data_root, repo_root=config.REPO_ROOT))
    scored["tag"] = args.tag
    scored["fixtures"] = str(args.fixtures.relative_to(config.REPO_ROOT))
    scored["labels"] = str(args.labels.relative_to(config.REPO_ROOT))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / f"accuracy_{args.tag}.json"
    md_path = args.out_dir / f"accuracy_{args.tag}.md"
    payload = {k: v for k, v in scored.items() if k != "results"}
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(scored, tag=args.tag), encoding="utf-8")
    print(
        json.dumps(
            {"first_try_accuracy": scored["first_try_accuracy"], "weakest_class": scored["weakest_class"], "json": str(json_path), "md": str(md_path)},
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
