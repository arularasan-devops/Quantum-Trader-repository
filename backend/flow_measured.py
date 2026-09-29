#!/usr/bin/env python
"""What the recorded Flow book cost at the spread the market actually quoted.

    .venv/bin/python flow_measured.py
    .venv/bin/python flow_measured.py --out ~/flow_measured.json

Read-only. Reads the Flow ledger and the captured chain; writes nothing except
the optional JSON report. RESEARCH ONLY — changes no engine behaviour.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import settings
from app.research import flow_measured as fm


def _money(v: float | None) -> str:
    return "n/a" if v is None else f"Rs {v:,.0f}"


def render(rep: dict) -> str:
    j, c, g = rep["join"], rep["cost"], rep["gate"]
    m = c["measured"]
    out: list[str] = []
    out.append("§1 join — Flow legs against the captured chain")
    out.append(f"  legs={j['legs']}  matched={j['matched']}  "
               f"unmatched={j['unmatched']}  median gap={j['median_match_gap_sec']}s")
    out.append(f"  priced on a quoted book={j['measured_book']}  "
               f"matched but no usable book={j['matched_but_no_book']}")
    if j["measured_book"] == 0:
        out.append("  nothing can be called measured yet — no leg's entry candle is "
                   "in the captured chain, so every cost below would be a model")
        return "\n".join(out)

    out.append("")
    out.append("§2 the spread we assumed vs the spread that was quoted")
    out.append(f"  quoted spread median={c['quoted_spread_median_points']} pts")
    out.append(f"  round trip median={m['median_cost_pct_of_premium']}% of premium")
    err = c["median_error_points"]
    if err is not None:
        worse = "understated" if err > 0 else "overstated"
        out.append(f"  the family median {worse} the real round trip by "
                   f"{abs(err):.2f} pts per leg (median)")
    out.append(f"  measured cost total={_money(m['cost_rupees'])}   "
               f"assumed cost total={_money(c['assumed_cost_rupees'])}")

    out.append("")
    out.append("§3 the book, net of the cost the market charged")
    out.append(f"  legs={m['legs']}  gross={_money(m['gross_rupees'])}  "
               f"cost={_money(m['cost_rupees'])}  net={_money(m['net_rupees'])}")
    out.append(f"  legs that were net winners after cost: {m['net_win_pct']}%")

    out.append("")
    out.append(f"§4 the entry gate, replayed on measured terms "
               f"(needs {g['required_multiple']}x its own cost)")
    for name, key in (("would have refused", "refused"),
                      ("would have allowed", "allowed"),
                      ("could not price", "unmeasured")):
        a = g[key]
        out.append(f"  {name:<20} legs={a['legs']:<6} "
                   f"gross={_money(a['gross_rupees']):<14} "
                   f"net={_money(a['net_rupees']):<14} net win="
                   + ("n/a" if a["net_win_pct"] is None
                      else f"{a['net_win_pct']}%"))
    out.append(f"  book net={_money(g['book_net_rupees'])}   "
               f"net with the refused legs removed="
               f"{_money(g['net_without_refused_rupees'])}")
    out.append(f"  {rep['caveat']}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", default="")
    ap.add_argument("--tolerance", type=int, default=180,
                    help="seconds a leg's entry may be from a captured row")
    ap.add_argument("--bars", type=int, default=0,
                    help="candles in the median range (default: engine setting)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    path = Path(args.ledger) if args.ledger else (
        Path(settings.data_dir) / "flow_signals.jsonl")
    legs = fm.load_legs(path)
    if not legs:
        print(f"no completed Flow legs found in {path.name} — nothing to price")
        return 1
    rep = fm.reprice(legs, tolerance_sec=args.tolerance, bars=args.bars)
    print(render(rep))
    if args.out:
        dest = Path(args.out).expanduser()
        payload = {k: v for k, v in rep.items() if k != "legs"}
        payload["legs"] = rep["legs"]
        dest.write_text(json.dumps(payload, indent=2, default=str))
        print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
