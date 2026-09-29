"""Phase 34 CLI — research only. Reads the broker's historical endpoint, writes
a research store. Places no order and changes no live behaviour.

    python -m app.research.phase34.cli master --roots NIFTY,BANKNIFTY
    python -m app.research.phase34.cli harvest --roots NIFTY --calls 200
    python -m app.research.phase34.cli status
    python -m app.research.phase34.cli premium     # exits on real premium candles
    python -m app.research.phase34.cli adaptive    # exits on the 5-year underlying
    python -m app.research.phase34.cli morning     # first 30 minutes -> rest of day
"""
from __future__ import annotations

import argparse
import json

from app.backtest.angel_history import login_smart
from app.research.phase34 import harvest as p34harvest
from app.research.phase34 import morning as p34morning
from app.research.phase34 import premium as p34premium
from app.research.phase34 import store as p34store
from app.research.phase34 import study as p34study

DEFAULT_ROOTS = "NIFTY,BANKNIFTY"


def _roots(raw: str) -> tuple[str, ...]:
    return tuple(r.strip().upper() for r in raw.split(",") if r.strip())


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 34 option premium history")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("master", help="register today's listed contracts (run daily)")
    m.add_argument("--roots", default=DEFAULT_ROOTS)

    h = sub.add_parser("harvest", help="download 1-minute premium candles")
    h.add_argument("--roots", default=DEFAULT_ROOTS)
    h.add_argument("--calls", type=int, default=200, help="per-run call budget")
    h.add_argument("--lookback-days", type=int, default=365)

    sub.add_parser("status", help="what the premium store now covers")
    sub.add_parser("premium", help="adaptive exits measured on real premium candles")
    sub.add_parser("adaptive", help="adaptive exits on the five-year underlying")
    sub.add_parser("morning", help="morning direction persistence and volatility rank")

    args = ap.parse_args()

    if args.cmd == "master":
        n = p34harvest.snapshot_master(_roots(args.roots))
        print(f"contracts registered/refreshed: {n}")
        return

    if args.cmd == "harvest":
        smart = login_smart()
        result = p34harvest.harvest(
            smart,
            _roots(args.roots),
            lookback_days=args.lookback_days,
            max_calls=args.calls,
        )
        print(json.dumps(result, indent=2, default=str))
        return

    if args.cmd == "premium":
        out = p34premium.study()
        print(f"status {out['status']} contracts {out['contracts_used']} "
              f"sessions {out['sessions']}")
        return

    if args.cmd == "adaptive":
        out = p34study.run()
        print(f"VERDICT {out['verdict']} hypotheses {out['hypotheses_counted']} "
              f"validated {len(out['validated'])} leads {len(out['research_leads'])}")
        return

    if args.cmd == "morning":
        out = p34morning.run()
        print(f"VERDICT {out['verdict']} hypotheses {out['hypotheses_counted']} "
              f"leads {len(out['leads'])}")
        return

    con = p34store.connect()
    try:
        print(json.dumps(p34store.coverage(con), indent=2))
    finally:
        con.close()


if __name__ == "__main__":
    main()
