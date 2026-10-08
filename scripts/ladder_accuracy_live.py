#!/usr/bin/env python3
"""Live OpenRouter ladder accuracy spot-check (synthetic cases only, capped spend).

Scores against frozen v2 labels via ``ladder_accuracy_score``. Requires
``--confirm-live`` and refuses when ``monthly_usd`` is at or above the $5 warn
gate. Does not print ``OPENROUTER_CC_API_KEY``.

  PYTHONPATH=scripts python3 scripts/ladder_accuracy_live.py --confirm-live --dry-run
  PYTHONPATH=scripts python3 scripts/ladder_accuracy_live.py --confirm-live --max 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chip import config, openrouter, spend  # noqa: E402
from chip_relay import case_intake_ladder as ladder  # noqa: E402
from chip_relay.case_intake import load_limits, parse_freeform  # noqa: E402
from chip_relay.case_intake_pipeline import live_parser  # noqa: E402
from chip_relay.ladder_accuracy_harness import run_fixture  # noqa: E402
from chip_relay.synthetic_dry_run import guard_subprocess  # noqa: E402
import ladder_accuracy_run as canned_run  # noqa: E402
import ladder_accuracy_score as score  # noqa: E402

V2_DIR = config.REPO_ROOT / "fixtures" / "ladder_accuracy" / "v2"
ABSOLUTE_MAX = 10
COMMAND = "ladder-accuracy-live"


def _abort_if_monthly_at_warn() -> float:
    key = openrouter.require_api_key()
    ledger, api_monthly = spend.refresh_from_auth(key)
    monthly = spend.effective_monthly_usd(ledger, api_monthly)
    if monthly >= spend.WARN_MONTHLY_USD:
        raise SystemExit(
            f"refuse: monthly_usd={monthly:.2f} >= warn ${spend.WARN_MONTHLY_USD:.2f} "
            f"(OPENROUTER_CC_API_KEY)"
        )
    return monthly


def _guarded_live_parser(msg: str, model: str) -> dict[str, Any]:
    key = openrouter.require_api_key()
    spend.guard_before_live_completion(f"{COMMAND}-parse", key)
    parsed = live_parser(msg, model)
    # live_parser already performed the completion; refresh ledger for reporting.
    ledger, api_monthly = spend.refresh_from_auth(key)
    spend.effective_monthly_usd(ledger, api_monthly)
    return parsed


def _ensure_synthetic(fx: dict[str, Any]) -> dict[str, Any]:
    out = dict(fx)
    out["synthetic"] = True
    route = str(out.get("route") or "ladder")
    if route in {"adapter", "informational", "parse", "monitoring_skill"}:
        raw = str(out.get("raw_input") or "")
        if "synthetic:true" not in raw.lower():
            out["raw_input"] = f"synthetic:true {raw}".strip()
    if route == "ladder":
        case = dict(out.get("case") or {})
        case["synthetic"] = True
        out["case"] = case
    return out


def pick_stratified_sample(merged: list[dict[str, Any]], *, max_n: int) -> list[dict[str, Any]]:
    by_class: dict[str, list[dict[str, Any]]] = {}
    for fx in merged:
        by_class.setdefault(str(fx.get("corpus_class") or "unknown"), []).append(fx)

    ranked: list[tuple[tuple[int, str], str, list[dict[str, Any]]]] = []
    for klass, items in by_class.items():
        misses = sum(1 for i in items if not i.get("label_first_try_ok", True))
        ranked.append(((-misses, klass), klass, items))
    ranked.sort(key=lambda row: row[0])

    picks: list[dict[str, Any]] = []
    for _, _klass, items in ranked[:max_n]:
        items_sorted = sorted(items, key=lambda i: (i.get("label_first_try_ok", True), str(i["id"])))
        picks.append(items_sorted[0])
    return picks


def run_fixture_live(
    fx: dict[str, Any],
    *,
    data_root: Path,
    repo_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (observed, meta) with spend_by_tier and monthly snapshots."""
    fx = _ensure_synthetic(fx)
    route = str(fx.get("route") or "ladder")
    meta: dict[str, Any] = {"route": route, "spend_by_tier": {}, "spend_usd": 0.0}

    with guard_subprocess(allow_non_docker=False) as guard:
        if route == "parse":
            _abort_if_monthly_at_warn()
            lim = load_limits()
            result = parse_freeform(
                str(fx.get("raw_input") or ""),
                parser=_guarded_live_parser,
                limits=lim,
                monthly_usd=0.0,
                skip_spend_gate=False,
            )
            obs: dict[str, Any] = {"route": route, "kind": result.kind}
            if result.pending:
                obs["missing"] = list(result.pending.missing)
            if result.case is not None:
                obs["case_id"] = result.case.id
            meta["subprocess_attempts"] = guard["attempts"]
            return obs, meta

        if route == "ladder":
            _abort_if_monthly_at_warn()
            case = dict(fx.get("case") or {})
            case.setdefault("id", fx["id"])
            case["synthetic"] = True
            stage = ladder.make_openrouter_stage_fn(command=COMMAND)
            out = ladder.run_ladder(
                case,
                stage_fn=stage,
                data_root=data_root,
                repo_root=repo_root,
                case_path=None,
                evidence=str(case.get("repro_context") or ""),
                synthetic=True,
            )
            meta["spend_by_tier"] = dict(out.get("spend_by_tier") or {})
            meta["spend_usd"] = float(sum(meta["spend_by_tier"].values()))
            meta["subprocess_attempts"] = guard["attempts"]
            return {
                "route": route,
                "status": out.get("status"),
                "verdict": out.get("verdict"),
                "bounces": int(out.get("bounces") or 0),
                "first_try": out.get("bounces") == 0 and out.get("status") == "verified",
            }, meta

        obs = run_fixture(fx, data_root=data_root, repo_root=repo_root)
        meta["subprocess_attempts"] = guard["attempts"]
        return obs, meta


def render_markdown(report: dict[str, Any], *, tag: str) -> str:
    lines = [
        f"# Ladder accuracy live run ({tag})",
        "",
        f"- cases: **{report['cases']}**",
        f"- first-try accuracy: **{report['first_try_accuracy']:.1%}**",
        f"- total spend (session delta approx): **${report.get('total_spend_usd', 0):.4f}**",
        f"- monthly_usd at start: **{report.get('monthly_usd_start')}**",
        f"- monthly_usd at end: **{report.get('monthly_usd_end')}**",
        "",
        "## Cases",
        "",
        "| id | class | hit | spend_usd | bounces |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report.get("case_rows", []):
        lines.append(
            f"| {row['id']} | {row.get('corpus_class')} | {'y' if row.get('hit') else 'n'} "
            f"| {row.get('spend_usd', 0):.4f} | {row.get('bounces', '')} |"
        )
    lines.extend(["", "## Misses", ""])
    for m in report.get("misses", [])[:20]:
        lines.append(f"- `{m['id']}` ({m['corpus_class']}): {m['reason']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixtures", type=Path, default=V2_DIR / "fixtures.jsonl")
    ap.add_argument("--labels", type=Path, default=V2_DIR / "labels.jsonl")
    ap.add_argument("--out-dir", type=Path, default=config.REPO_ROOT / "logs" / "accuracy")
    ap.add_argument("--max", type=int, default=ABSOLUTE_MAX, help=f"at most {ABSOLUTE_MAX} cases")
    ap.add_argument("--confirm-live", action="store_true", help="required for real OpenRouter calls")
    ap.add_argument("--dry-run", action="store_true", help="print sample ids only; no network")
    args = ap.parse_args(argv)

    if args.max > ABSOLUTE_MAX:
        print(f"refuse: --max {args.max} > {ABSOLUTE_MAX}", file=sys.stderr)
        return 2
    if not args.dry_run and not args.confirm_live:
        print("refuse: pass --confirm-live for live OpenRouter (or --dry-run)", file=sys.stderr)
        return 2

    merged = canned_run.merge_fixtures_labels(canned_run.load_jsonl(args.fixtures), canned_run.load_jsonl(args.labels))
    sample = pick_stratified_sample(merged, max_n=max(1, args.max))
    if args.dry_run:
        print(json.dumps({"sample": [s["id"] for s in sample], "classes": [s.get("corpus_class") for s in sample]}, indent=2))
        return 0

    monthly_start = _abort_if_monthly_at_warn()
    tag = time.strftime("live_%Y%m%dT%H%M%SZ", time.gmtime())
    data_root = args.out_dir / f"scratch-{tag}"
    data_root.mkdir(parents=True, exist_ok=True)

    scored_rows = []
    case_rows = []
    total_spend = 0.0
    for fx in sample:
        _abort_if_monthly_at_warn()
        observed, meta = run_fixture_live(fx, data_root=data_root, repo_root=config.REPO_ROOT)
        total_spend += float(meta.get("spend_usd") or 0.0)
        scored_rows.append(
            {
                "id": fx["id"],
                "corpus_class": fx.get("corpus_class"),
                "observed": observed,
                "expected": fx["expected"],
                "label_first_try_ok": fx.get("label_first_try_ok", True),
            }
        )
        case_rows.append(
            {
                "id": fx["id"],
                "corpus_class": fx.get("corpus_class"),
                "observed": observed,
                "expected": fx["expected"],
                "meta": meta,
            }
        )

    report = score.score_rows(scored_rows)
    key = openrouter.require_api_key()
    ledger, api_monthly = spend.refresh_from_auth(key)
    monthly_end = spend.effective_monthly_usd(ledger, api_monthly)

    for cr, res in zip(case_rows, report.get("results", []), strict=True):
        cr["hit"] = res["hit"]
        cr["reason"] = res["reason"]
        cr["spend_usd"] = cr["meta"].get("spend_usd", 0.0)
        cr["bounces"] = cr["observed"].get("bounces")

    payload = {
        **{k: v for k, v in report.items() if k != "results"},
        "tag": tag,
        "mode": "live",
        "sample_ids": [s["id"] for s in sample],
        "total_spend_usd": round(total_spend, 6),
        "monthly_usd_start": monthly_start,
        "monthly_usd_end": monthly_end,
        "case_rows": case_rows,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / f"{tag}.json"
    md_path = args.out_dir / f"{tag}.md"
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown({**payload, "misses": report.get("misses", [])}, tag=tag), encoding="utf-8")

    print(
        json.dumps(
            {
                "first_try_accuracy": report["first_try_accuracy"],
                "total_spend_usd": total_spend,
                "monthly_usd_end": monthly_end,
                "json": str(json_path),
                "md": str(md_path),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
