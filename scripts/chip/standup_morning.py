"""RAZ-330 — morning digest (~11:25 PT) + Chip Chat signal writer.

Dry-run default: prints digest + always writes signal file when configured.
--post forwards SITREP to CHIP_WEBHOOK_MONITORING (optional).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any

from chip import standup
from chip import standup_collectors as sc
from chip import standup_format as sf
from chip import standup_signal as sg


def _pt_day() -> str:
    return sg._pt_now().strftime("%Y-%m-%d")


def _body_sha(fields: dict[str, Any]) -> str:
    blob = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _live_receipt(lane: str, fields: dict[str, Any], issued_at: str) -> dict[str, Any]:
    rec = {
        "standup": lane,
        "issued_at": issued_at,
        "payload": {
            "source": "live",
            "age_h": 0.0,
            "sha": _body_sha(fields),
            "sections_expected": list(fields.keys()),
        },
        "skills_commanded": False,
    }
    return standup.finalize_receipt(rec)


def _silent_receipt(lane: str, issued_at: str, *, zero_progress: bool = False) -> dict[str, Any]:
    extra: dict[str, Any] = {"zero_progress": True} if zero_progress else {}
    return standup.make_silent_receipt(lane, None, issued_at=issued_at, **extra)


def build_receipts(
    bundle: sc.GymlogCollectorBundle,
    homelab_fields: dict[str, str],
    *,
    issued_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    gym_fields = sc.gymlog_fields_from_bundle(bundle)
    if bundle.collectors_live:
        gymlog_rec = _live_receipt("gymlog", gym_fields, issued_at)
    else:
        gymlog_rec = _silent_receipt("gymlog", issued_at)
    # Homelab stays optional: live only when JSON file supplied all non-unknown blockers
    homelab_live = homelab_fields.get("blockers", "").startswith("Homelab collectors optional") is False
    if homelab_live and "unknown" not in homelab_fields.get("heartbeat", "unknown"):
        homelab_rec = _live_receipt("homelab", homelab_fields, issued_at)
    else:
        homelab_rec = _silent_receipt("homelab", issued_at)
    return gymlog_rec, homelab_rec


def write_receipt(path: str, receipt: dict[str, Any]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=1, sort_keys=True)
        fh.write("\n")


def run_morning(
    *,
    receipts_dir: str | None,
    signal_file: str | None,
    post: bool,
    issued_at: str,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = environ if environ is not None else os.environ
    day = _pt_day()
    bundle = sc.run_gymlog_collectors(env)
    homelab_fields = sc.load_homelab_fields(env)
    gym_fields = sc.gymlog_fields_from_bundle(bundle, env)

    banner = ""
    if not bundle.collectors_live:
        banner = (
            "⚠️ **Missed standup payload** — one or more GymLog collectors failed "
            "(explicit banner; not a silent miss).\n\n"
        )

    digest_parts = [
        banner + sf.format_gymlog_standup(day, gym_fields),
        "",
        sf.format_homelab_standup(day, homelab_fields),
        "",
        f"verified-as-of: {sg._iso_utc()}",
        f"collectors_live: {str(bundle.collectors_live).lower()}",
        f"unknowns: {json.dumps({'gymlog': not bundle.collectors_live})}",
    ]
    digest = "\n".join(digest_parts).strip()
    print(digest)

    gymlog_rec, homelab_rec = build_receipts(bundle, homelab_fields, issued_at=issued_at)
    if receipts_dir:
        write_receipt(os.path.join(receipts_dir, "gymlog.json"), gymlog_rec)
        write_receipt(os.path.join(receipts_dir, "homelab.json"), homelab_rec)

    fin = [standup.finalize_receipt(dict(gymlog_rec)), standup.finalize_receipt(dict(homelab_rec))]
    verified = sg._iso_utc()
    signal = sg.build_signal(fin, verified_as_of=verified, pt_date=day)
    sig = sg.write_signal(signal, signal_file, environ=env)
    print(f"signal: {sig['note']}" + (f" -> {sig['wrote']}" if sig.get("wrote") else ""))

    if post:
        from chip.monitor_watchdog import post_to_monitoring

        post_sig = sg.post_signal_url(signal, environ=env)
        if post_sig.get("url"):
            print(f"signal-url: {post_sig['note']}")
        ok, note = post_to_monitoring(digest[:3900])
        print(note)
        if not ok:
            return {
                "ok": False,
                "bundle": bundle,
                "digest": digest,
                "signal": sig,
                "post_note": note,
            }

    return {
        "ok": bundle.collectors_live,
        "bundle": bundle,
        "digest": digest,
        "signal": sig,
        "collectors": {
            "gha": bundle.gha.ok,
            "site": bundle.site.ok,
            "linear": bundle.linear.ok,
            "live_e2e": bundle.live_e2e.ok,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chip-standup-morning", description=__doc__)
    parser.add_argument(
        "--receipts-dir",
        default=None,
        help="Write homelab.json + gymlog.json receipts for standup_emit",
    )
    parser.add_argument("--signal-file", default=None, help="Override CHIP_CHAT_STANDUP_SIGNAL_FILE")
    parser.add_argument(
        "--post",
        action="store_true",
        help="Post digest to CHIP_WEBHOOK_MONITORING (dry-run default)",
    )
    parser.add_argument(
        "--issued-at",
        default="",
        help="Receipt issued_at stamp (default: now PT-ish via env CHIP_STANDUP_ISSUED_AT)",
    )
    args = parser.parse_args(argv)
    from chip.standup_secrets import apply_standup_secrets

    loaded = apply_standup_secrets()
    missing = [
        name
        for name in ("CHIP_STANDUP_GITHUB_TOKEN", "LINEAR_API_KEY", "CHIP_ALERT_INGEST_TOKEN")
        if not os.environ.get(name, "").strip()
    ]
    print(
        "standup-secrets: loaded "
        + (",".join(loaded) if loaded else "none")
        + "; absent "
        + (",".join(missing) if missing else "none")
    )
    issued = args.issued_at.strip() or os.environ.get("CHIP_STANDUP_ISSUED_AT") or f"{_pt_day()} 11:25 PT"
    result = run_morning(
        receipts_dir=args.receipts_dir,
        signal_file=args.signal_file,
        post=args.post,
        issued_at=issued,
    )
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
