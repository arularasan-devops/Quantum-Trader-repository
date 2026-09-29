"""Is the option book actually being captured? — read-only coverage audit.

RESEARCH ONLY. Places no order, reads no live feed, writes nothing except the
report file you ask for. It answers the one question that gates every "net"
number in this system: are option costs MEASURED from a quoted bid/ask, or
ASSUMED from a family median standing in for a book we never saw?

    .venv/bin/python chain_coverage.py --instruments NIFTY,BANKNIFTY
    .venv/bin/python chain_coverage.py --out ~/chain_coverage.json

Three sources are audited, because a gap in any one of them produces the same
symptom — an assumed cost — with a different fix:

* the research store (``app.research.capture`` writes it from the live tick loop);
* ``data/chain_history/*.jsonl`` from ``chain_recorder.py``;
* ``data/flow_signals.jsonl`` — the paper legs themselves, where the audit found
  0 of 1,169 legs carrying a quoted spread at entry.
"""
from __future__ import annotations

import argparse
import glob
import json
import os

from app.config import settings
from app.research import chain_audit


def _usable(bid: object, ask: object) -> bool:
    return chain_audit.usable_spread(bid, ask) is not None


def _recorder_files() -> dict:
    """Coverage of the forward chain recordings, per file."""
    out = []
    pattern = os.path.join(settings.data_dir, "chain_history", "*_chain.jsonl")
    for path in sorted(glob.glob(pattern)):
        snaps = quoted = legs = 0
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    snap = json.loads(line)
                except json.JSONDecodeError:
                    continue
                chain = snap.get("chain")
                if not isinstance(chain, list):
                    continue
                snaps += 1
                for q in chain:
                    if not isinstance(q, dict):
                        continue
                    legs += 1
                    if _usable(q.get("bid"), q.get("ask")):
                        quoted += 1
        out.append({
            "file": os.path.basename(path),
            "snapshots": snaps,
            "legs": legs,
            "quoted_legs": quoted,
            "quoted_pct": round(100.0 * quoted / legs, 2) if legs else None,
        })
    return {"files": out,
            "note": ("legs recorded before the recorder stored a book read as 0 "
                     "quoted; that is a missing measurement, not a zero spread")}


def _flow_legs() -> dict:
    """How the recorded Flow paper legs were costed, measured vs assumed."""
    path = os.path.join(settings.data_dir, "flow_signals.jsonl")
    total = measured = assumed = uncosted = 0
    econ = {"ECONOMIC": 0, "UNECONOMIC": 0, "UNMEASURED": 0, "ABSENT": 0}
    if not os.path.exists(path):
        return {"legs": 0, "note": "no Flow paper legs have been recorded yet"}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                leg = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            src = leg.get("cost_spread_source")
            if src == chain_audit.MEASURED:
                measured += 1
            elif src == chain_audit.ASSUMED:
                assumed += 1
            else:
                uncosted += 1
            entry_econ = leg.get("entry_economics")
            verdict = (entry_econ.get("verdict")
                       if isinstance(entry_econ, dict) else None)
            econ[verdict if verdict in econ else "ABSENT"] += 1
    return {
        "legs": total,
        "cost_measured": measured,
        "cost_assumed": assumed,
        "cost_unknown": uncosted,
        "measured_pct": round(100.0 * measured / total, 2) if total else None,
        "entry_economics": econ,
        "note": ("legs logged before the economics test existed count as ABSENT; "
                 "they are not evidence either way"),
    }


def _refusals() -> dict:
    """What the economics gate turned away, and on what basis.

    Refused legs never become paper trades, so without this the gate's own
    effect is invisible: it could be saving cost or refusing the day's best
    move and the book would look identical either way.
    """
    path = os.path.join(settings.data_dir, "flow_refusals.jsonl")
    if not os.path.exists(path):
        return {"refused": 0,
                "note": "the economics gate has not turned any leg away yet"}
    total = measured = 0
    ratios: list[float] = []
    sessions: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            if row.get("cost_spread_source") == chain_audit.MEASURED:
                measured += 1
            ratio = row.get("ratio")
            if isinstance(ratio, (int, float)):
                ratios.append(float(ratio))
            session = row.get("session")
            if isinstance(session, str):
                sessions.add(session)
    ratios.sort()
    return {
        "refused": total,
        "sessions": len(sessions),
        "refused_on_measured_spread": measured,
        "refused_on_assumed_spread": total - measured,
        "median_ratio": round(ratios[len(ratios) // 2], 2) if ratios else None,
        "note": ("a refusal made on an assumed spread is a refusal made on an "
                 "estimate; only the measured ones are evidence"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", default="NIFTY,BANKNIFTY,CRUDEOIL")
    ap.add_argument("--out", default=None, help="write the report as JSON")
    args = ap.parse_args()

    names = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    report = {
        "research_store": chain_audit.audit_all(names),
        "chain_recorder": _recorder_files(),
        "flow_paper_legs": _flow_legs(),
        "economics_refusals": _refusals(),
    }

    print("§1 research store (captured by the live tick loop)")
    for a in report["research_store"]["instruments"]:
        print(f"  {a['instrument']:10s} rows={a['option_rows']:<8} "
              f"snapshots={a['snapshots']:<6} quoted={a['quoted_pct']}%  "
              f"delta={a['delta_rows']}  {a['verdict']}")
        print(f"             {a['note']}")
    tot = report["research_store"]["totals"]
    print(f"  TOTAL rows={tot['option_rows']} quoted={tot['quoted_pct']}%  "
          f"measured={tot['cost_basis'][chain_audit.MEASURED]} "
          f"assumed={tot['cost_basis'][chain_audit.ASSUMED]}")

    print("\n§2 forward chain recordings")
    files = report["chain_recorder"]["files"]
    if not files:
        print("  none — chain_recorder.py has not written anything yet")
    for f in files:
        print(f"  {f['file']:28s} snapshots={f['snapshots']:<6} "
              f"legs={f['legs']:<8} quoted={f['quoted_pct']}%")

    print("\n§3 Flow paper legs")
    fl = report["flow_paper_legs"]
    print(f"  legs={fl.get('legs')}  measured={fl.get('measured_pct')}%  "
          f"assumed={fl.get('cost_assumed')}  unknown={fl.get('cost_unknown')}")
    if fl.get("entry_economics"):
        print(f"  entry economics: {fl['entry_economics']}")

    print("\n§4 legs the economics gate refused")
    rf = report["economics_refusals"]
    print(f"  refused={rf.get('refused')}  sessions={rf.get('sessions', 0)}  "
          f"median ratio={rf.get('median_ratio')}x  "
          f"refused on a measured spread={rf.get('refused_on_measured_spread', 0)}")
    print(f"  {rf['note']}")

    print("\nreading: a MEASURED cost is one the market quoted. Until the quoted "
          "share is high, every net figure in this system is an assumption with "
          "a number attached, and no rule can be called profitable from it.")

    if args.out:
        with open(os.path.expanduser(args.out), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
