#!/usr/bin/env python
"""Phase 18 — build the CAS evidence artefacts from what has been captured.

RESEARCH ONLY. No broker call, no order, safe to run while the paper app is up.

    .venv/bin/python phase18_cas_report.py --outdir ~/phase18

Reads the JSONL files the live tick writes (CAS observations, paper legs, session
coverage) and writes ten JSON/Markdown pairs, the twenty questions and the
no-order-path safety scan. With an empty capture directory it still runs and
every verdict is REQUIRES_MORE_DATA — which, four to eight expiries into a
mechanism that began on 2025-08-03, is the correct output rather than a failure.
"""
from __future__ import annotations

import argparse

from app.research.phase18 import artefacts, store


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 18 CAS evidence artefacts")
    ap.add_argument("--outdir", required=True, help="directory for the artefacts")
    ap.add_argument("--limit", type=int, default=None,
                    help="read at most N rows of each evidence file")
    args = ap.parse_args()

    observations = store.observations(limit=args.limit)
    paper_rows = store.paper(limit=args.limit)
    coverage = store.coverage(limit=args.limit)

    result = artefacts.build_all(
        outdir=args.outdir,
        observations=observations,
        paper_rows=paper_rows,
        coverage=coverage,
    )

    cap = result["payloads"].get("phase18_cas_capture", {})
    gate = cap.get("quality", {})
    recon = cap.get("reconciliation", {})
    print("CAS PAPER ONLY — research artefacts, no order path.")
    print(f"observations       : {recon.get('observations')}")
    print(f"exact/near-exact   : {gate.get('match_rate_pct')}% "
          f"(target {gate.get('target_pct')}%) -> {gate.get('status')}")
    print(f"paper legs resolved: {recon.get('resolved')} "
          f"(unresolved {recon.get('unresolved')})")
    print(f"promotion verdict  : {result['verdict']}")
    print(f"safety scan clean  : {result['safety']['clean']}")
    print(f"written            : {len(result['written'])} files -> {args.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
