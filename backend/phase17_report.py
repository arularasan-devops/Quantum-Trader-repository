#!/usr/bin/env python
"""Phase 17 — build the evidence artefacts from what has actually been captured.

RESEARCH ONLY. No Angel call, no order, safe to run while the paper app is up.

    .venv/bin/python phase17_report.py --outdir ~/phase17
    .venv/bin/python phase17_report.py --outdir ~/phase17 \
        --pool ~/market_scan_pool.json          # adds the against-HTF probe

Reads the JSONL evidence files the live tick writes (observations, legs, paper
episodes, session coverage) and writes ten JSON/Markdown pairs plus the 25 daily
questions. With an empty capture directory it still runs and still answers, and
every answer is REQUIRES_MORE_DATA — which is the correct first output of this
phase, not a failure of it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research.phase17 import artefacts, quality, store


def _pct(value: object) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{float(value):.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 17 evidence artefacts")
    ap.add_argument("--outdir", required=True, help="directory for the artefacts")
    ap.add_argument("--pool", default=None,
                    help="candidate pool JSON for the against-HTF probe (§19)")
    ap.add_argument("--limit", type=int, default=None,
                    help="read at most N rows of each evidence file")
    args = ap.parse_args()

    pool: list[dict] | None = None
    if args.pool:
        raw = json.loads(Path(args.pool).expanduser().read_text(encoding="utf-8"))
        pool = raw.get("trades", raw) if isinstance(raw, dict) else raw

    obs = store.observations(limit=args.limit)
    legs = store.legs(limit=args.limit)
    paper_rows = store.paper(limit=args.limit)
    cov = store.coverage(limit=args.limit)

    res = artefacts.build_all(
        outdir=str(Path(args.outdir).expanduser()),
        observations=obs, legs=legs, paper_rows=paper_rows,
        coverage_rows=cov, pool=pool,
    )
    cap = res["payloads"]["capture"]
    summary = artefacts.summary(res["payloads"])

    print("PHASE 17 — VEHICLE EVIDENCE")
    print(f"  candidates captured : {cap['total_candidates']}")
    print(f"  EXACT match rate    : {_pct(cap.get('exact_match_rate_pct'))} "
          f"(target {quality.EXACT_RATE_TARGET_PCT:.0f}%, "
          f"{'MET' if cap.get('meets_target') else 'NOT MET'})")
    print(f"  both sides captured : {_pct(cap.get('both_side_pct'))}")
    print(f"  resolved legs       : {len(legs)}")
    print(f"  paper episodes      : {len(paper_rows)}")
    print(f"  journal rows        : {res['journal_rows']}")
    print(f"  sessions covered    : "
          f"{len({str(c.get('session')) for c in cov if c.get('session')})}")
    print(f"  promotion status    : {summary['promotion']['status']}")
    print(f"  unanswered questions: {summary['questions']['requires_more_data']}"
          f"/{summary['questions']['total']}")
    print()
    for name in artefacts.PAIRS:
        print(f"  wrote {res['artefacts'][name]['md']}")
    print(f"  wrote {res['artefacts']['journal']['csv']}")
    print("\nAll figures are PAPER/RESEARCH only. No order was placed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
