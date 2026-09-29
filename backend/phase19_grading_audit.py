"""Grading audit — why did 29 of 30 opportunities not reach A+?

    .venv/bin/python phase19_grading_audit.py                 # every session on disk
    .venv/bin/python phase19_grading_audit.py --sessions 1     # the last session
    .venv/bin/python phase19_grading_audit.py --opportunities  # per-opportunity table
    .venv/bin/python phase19_grading_audit.py --json

The funnel established that nothing is dropping trades: the paper lifecycle
refused nothing, and the ceiling was one A+ candidate. This asks the next
question, which is about the grader rather than the plumbing.

`aplus._label` returns on the FIRST failing gate, so the recorded label names one
cause even when three apply. A tally of those labels therefore cannot answer "is
this gate the binding constraint, or merely the first one checked?" — and that is
exactly the ordering artefact worth ruling out before anyone reasons about the
gates. So every gate is re-evaluated **independently** for every recorded tick,
off the persisted row, and reported three ways:

* **failing** — how many ticks this gate refuses, ignoring the others.
* **sole blocker** — ticks where this is the ONLY thing between the candidate and
  A+. This is the actionable column: lifting a gate that is never a sole blocker
  changes nothing.
* **first (recorded)** — how often it was the label that got written down. The
  gap between "failing" and "first" is the size of the ordering artefact.

Two deliberate limits.

The counterfactual columns say what WOULD have been graded A+ had one gate been
absent. They are arithmetic over rows already recorded, not a proposal: a gate
that refuses candidates for a good reason will also show a large counterfactual,
so the number is a measurement of what the gate costs, never an argument that it
is wrong. Nothing here changes a threshold.

Scores are re-read, not recomputed. The audit reports the score the engine
recorded; it never re-scores a row, so it cannot disagree with the board.

One exception, clearly separated at the bottom of the output: the ``room`` A/B
does re-grade, because the question it answers cannot be answered any other way.
``room`` was unmeasurable on any candidate the engine was not already recommending
(no published target, no score, and A+ needs every component measured), so the
replay shows what the same recorded rows grade as once a derived target is allowed
— including a regression check that rows which DID have an engine target grade
identically. It is labelled v1/v2 throughout and changes nothing on disk.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase17 import aplus as p17aplus
from app.research.phase17 import capture as p17capture
from app.research.phase17 import store as p17store
from app.research.phase19 import grading_ab as ab
from app.research.phase19 import roomab

# The gate vocabulary, the order ``aplus._label`` checks it in, and the label each
# gate produces all live in ``grading_ab`` so the audit and the A/B experiment
# cannot drift into two readings of the same rule. Re-exported for callers.
G_DATA = ab.G_DATA
G_NO_ECON = ab.G_NO_ECON
G_VEHICLE_RED = ab.G_VEHICLE_RED
G_COST_UNKNOWN = ab.G_COST_UNKNOWN
G_SPREAD = ab.G_SPREAD
G_ENTRY_CHASED = ab.G_ENTRY_CHASED
G_ROOM = ab.G_ROOM
G_MARKET_PRIOR = ab.G_MARKET_PRIOR
G_MISSING = ab.G_MISSING
G_SCORE = ab.G_SCORE
ORDER = ab.ORDER
GATE_LABEL = ab.GATE_LABEL
gates_failed = ab.gates_failed
first_gate = ab.first_gate


def _sessions_of(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for r in rows:
        ts = r.get("signal_ts")
        if isinstance(ts, (int, float)):
            day = p17capture.ist_parts(float(ts))[0]
            if day and day not in out:
                out.append(day)
    return sorted(out)


def _within(rows: list[dict], keep: set[str] | None) -> list[dict]:
    if keep is None:
        return rows
    out = []
    for r in rows:
        ts = r.get("signal_ts")
        if isinstance(ts, (int, float)) and p17capture.ist_parts(float(ts))[0] in keep:
            out.append(r)
    return out


def opportunity_key(row: dict) -> str:
    sel = row.get("selected") or {}
    return "|".join([
        str(row.get("session") or ""),
        str(row.get("instrument") or ""),
        str(sel.get("symbol") or ""),
        str(row.get("direction") or ""),
    ])



def _tally(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return round(s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0, 2)


def ceilings() -> list[dict]:
    """Is A+ arithmetically reachable per instrument, before any market opens?

    ``market_edge`` is a fixed function of the instrument's historical prior, so
    each instrument carries a permanent score ceiling. Two structural facts fall
    out of that and neither depends on a single recorded tick:

    * an instrument whose negative prior is *validated* out of sample is refused
      outright by REJECT_MARKET, so no book, spread or entry on it can ever be
      A+. A negative the pool cannot separate from zero only lowers the ceiling;
    * for the rest, the best attainable score is bounded, and if that bound sits
      under the A+ floor the instrument is unreachable by arithmetic rather than
      by selectivity.

    The realistic column repeats the calculation with a YELLOW vehicle, since a
    GREEN one is uncommon: it is the score an otherwise flawless candidate on a
    typical book would earn.
    """
    w = p17aplus.WEIGHTS
    out: list[dict] = []
    for name, prior in sorted(
        p17aplus.PRIOR_DEV_R.items(), key=lambda kv: -kv[1]
    ):
        edge, _ = p17aplus.market_edge(name)
        edge = float(edge or 0.0)
        # Everything measured and perfect except the room term, which is the
        # candidate's own t1_score and cannot be assumed.
        base = (w["market_edge"] * edge + w["tradability"] * 100.0
                + w["entry_edge"] * 100.0 + w["data_quality"] * 100.0)
        best = base + w["vehicle_edge"] * 100.0
        typical = base + w["vehicle_edge"] * 60.0
        refused, label = p17aplus.prior_vetoes(name)
        out.append({
            "instrument": name,
            "prior_dev_r": prior,
            "prior_label": label,
            "market_edge": round(edge, 2),
            "refused_on_prior": refused,
            "ceiling_green_vehicle": round(best + w["room"] * 100.0, 2),
            "ceiling_yellow_vehicle": round(typical + w["room"] * 100.0, 2),
            "t1_score_needed_green": (
                None if refused else
                round(max(0.0, (p17aplus.A_PLUS_MIN_SCORE - best) / w["room"]), 1)
            ),
            "t1_score_needed_yellow": (
                None if refused else
                round(max(0.0, (p17aplus.A_PLUS_MIN_SCORE - typical) / w["room"]), 1)
            ),
            "a_plus_reachable": (
                not refused
                and best + w["room"] * 100.0 >= p17aplus.A_PLUS_MIN_SCORE
            ),
        })
    return out


def build(sessions: int | None) -> dict:
    all_obs = p17store.observations(tail_bytes=None)
    present = _sessions_of(all_obs)
    keep = set(present[-sessions:]) if sessions else None
    rows = _within(all_obs, keep)

    per_tick: list[tuple[dict, list[str]]] = [(r, gates_failed(r)) for r in rows]

    # Does the audit reproduce the engine's cascade? A mismatch means the audit is
    # reading a different rule than the one that graded the row, and every number
    # below it would be describing the wrong gate.
    mismatches: list[dict] = []
    for r, failed in per_tick:
        recorded = str(((r.get("aplus") or {}).get("a_plus_label")) or "")
        g = first_gate(failed)
        expected = GATE_LABEL.get(g or "")
        if expected is None:
            # G_MISSING / G_SCORE do not map to one label: below the A+ floor the
            # engine writes WATCH, REJECT_ROOM or REJECT_DATA depending on the
            # score and whether reach existed. Not a mismatch, just unmapped.
            continue
        if recorded != expected:
            mismatches.append({
                "observation_id": r.get("observation_id"),
                "recorded_label": recorded,
                "audit_first_gate": g,
                "audit_expected_label": expected,
                "all_gates_failed": failed,
            })

    gate_rows: list[dict] = []
    for g in ORDER:
        failing = [(r, f) for r, f in per_tick if g in f]
        sole = [(r, f) for r, f in failing if len(f) == 1]
        first = [(r, f) for r, f in per_tick if first_gate(f) == g]
        # Counterfactual: with this gate absent, which ticks clear everything else?
        without = [
            (r, f) for r, f in failing
            if not [x for x in f if x != g]
        ]
        gate_rows.append({
            "gate": g,
            "failing_ticks": len(failing),
            "failing_opportunities": len({opportunity_key(r) for r, _ in failing}),
            "sole_blocker_ticks": len(sole),
            "sole_blocker_opportunities": len({opportunity_key(r) for r, _ in sole}),
            "first_recorded_ticks": len(first),
            "would_be_a_plus_if_absent_ticks": len(without),
            "would_be_a_plus_if_absent_opportunities": len(
                {opportunity_key(r) for r, _ in without}
            ),
        })

    # Per-opportunity view: the 30, not the 8,775.
    grouped: dict[str, list[tuple[dict, list[str]]]] = {}
    for r, f in per_tick:
        grouped.setdefault(opportunity_key(r), []).append((r, f))

    opportunities: list[dict] = []
    for key, items in sorted(grouped.items()):
        labels = [
            str(((r.get("aplus") or {}).get("a_plus_label")) or "UNGRADED")
            for r, _ in items
        ]
        scores = [
            float((r.get("aplus") or {}).get("a_plus_score") or 0.0)
            for r, _ in items
        ]
        ever_clear = [f for _, f in items if not f]
        # The blockers this opportunity never escaped across its whole life. A
        # gate it failed on some ticks but not others is a timing question, not a
        # verdict, so the two are separated.
        always = set(items[0][1])
        for _, f in items[1:]:
            always &= set(f)
        sometimes = set()
        for _, f in items:
            sometimes |= set(f)
        comps: dict[str, float | None] = {}
        for name in p17aplus.WEIGHTS:
            vals = [
                float(v) for r, _ in items
                if isinstance(
                    v := (((r.get("aplus") or {}).get("components") or {})
                          .get(name, {}) or {}).get("value"),
                    (int, float),
                )
            ]
            comps[name] = _median(vals)
        opportunities.append({
            "opportunity": key,
            "instrument": items[0][0].get("instrument"),
            "symbol": (items[0][0].get("selected") or {}).get("symbol"),
            "direction": items[0][0].get("direction"),
            "ticks": len(items),
            "labels": _tally(labels),
            "reached_a_plus": p17aplus.A_PLUS in labels,
            "best_score": round(max(scores), 2) if scores else None,
            "median_score": _median(scores),
            "blocked_on_every_tick": sorted(always),
            "blocked_on_some_tick": sorted(sometimes - always),
            "clear_on_some_tick": bool(ever_clear),
            "median_components": comps,
        })

    # READY / PENDING_DATA / REJECTED at the opportunity level, under today's
    # rule. This says how much of the refusal is "we judged it" versus "we could
    # not yet judge it" — the two are indistinguishable in the label tally.
    states = {s: 0 for s in ab.STATES}
    for items in grouped.values():
        best = max((ab.state_of(f) for _, f in items),
                   key=lambda s: ab.RANK[s])
        states[best] += 1

    a_plus_opps = [o for o in opportunities if o["reached_a_plus"]]
    return {
        "question": "Why did the graded candidates not reach A+?",
        "sessions_requested": sessions,
        "sessions_present": present,
        "sessions_measured": _sessions_of(rows),
        "ticks": len(rows),
        "opportunities": len(opportunities),
        "a_plus_opportunities": len(a_plus_opps),
        "a_plus_floor": p17aplus.A_PLUS_MIN_SCORE,
        "watch_floor": p17aplus.WATCH_MIN_SCORE,
        "weights": dict(p17aplus.WEIGHTS),
        "cascade_order": list(ORDER),
        "cascade_mismatches": mismatches[:20],
        "cascade_mismatch_count": len(mismatches),
        "gates": gate_rows,
        "opportunity_states": states,
        "ceilings": ceilings(),
        "room_ab": roomab.replay(rows),
        "per_opportunity": opportunities,
        "prior_basis": p17aplus.PRIOR_BASIS,
        "thresholds_unchanged": True,
        "research_only": True,
    }


def _print(result: dict, *, show_opportunities: bool) -> None:
    print("GRADING AUDIT — why did the candidates not reach A+?")
    measured = result["sessions_measured"]
    print(f"sessions measured: {', '.join(measured) if measured else 'none'}"
          f"   (on disk: {len(result['sessions_present'])})")
    print(f"{result['ticks']} tick(s) · {result['opportunities']} distinct "
          f"opportunity(ies) · {result['a_plus_opportunities']} reached A+ "
          f"(floor {result['a_plus_floor']:.0f}, watch {result['watch_floor']:.0f})")
    print()

    if result["cascade_mismatch_count"]:
        print(f"!! {result['cascade_mismatch_count']} row(s) where this audit's "
              "cascade disagrees with the recorded label.")
        print("   The gate columns below describe a different rule than the one "
              "that graded those rows — fix this before reading them.")
        for m in result["cascade_mismatches"][:5]:
            print(f"   {m['observation_id']}: recorded {m['recorded_label']}, "
                  f"audit {m['audit_expected_label']} ({m['audit_first_gate']})")
        print()
    else:
        print("cascade check: the recorded label matches the first gate this "
              "audit finds, on every mappable row.\n")

    print("gate                            fails      sole blocker    first   "
          "A+ if absent")
    print("                            ticks/opps    ticks/opps      record   "
          "ticks/opps")
    for g in result["gates"]:
        print(f"  {g['gate']:<25} "
              f"{g['failing_ticks']:>6}/{g['failing_opportunities']:<4} "
              f"{g['sole_blocker_ticks']:>8}/"
              f"{g['sole_blocker_opportunities']:<4} "
              f"{g['first_recorded_ticks']:>8} "
              f"{g['would_be_a_plus_if_absent_ticks']:>7}/"
              f"{g['would_be_a_plus_if_absent_opportunities']}")
    print("\n  fails        = refuses this row, ignoring every other gate")
    print("  sole blocker = the ONLY thing between this row and A+")
    print("  first record = how often it was the label actually written down")
    print("  A+ if absent = would have been A+ with this gate removed "
          "(arithmetic, not a proposal)")

    st = result["opportunity_states"]
    print("\njudged, or not yet judgeable? (opportunities, today's rule)")
    print(f"  {ab.READY:<22} {st[ab.READY]:>5}   every gate clear, everything "
          "measured")
    print(f"  {ab.PENDING_DATA:<22} {st[ab.PENDING_DATA]:>5}   no gate refuses "
          "it — a component is not measured yet")
    print(f"  {ab.REJECTED:<22} {st[ab.REJECTED]:>5}   refused on its merits")
    print("  PENDING_DATA is not A+ and never enters the costed book; it is "
          "separated so that")
    print("  'we could not measure it yet' stops looking like 'we judged it "
          "and it was bad'.")

    print("\nis A+ even reachable? (fixed by the instrument prior, not by today)")
    print("instrument     prior     ceiling   ceiling   t1 needed  reachable   "
          "prior label")
    print("                dev R     GREEN    YELLOW     YELLOW")
    for c in result["ceilings"]:
        if c["refused_on_prior"]:
            print(f"  {c['instrument']:<12} {c['prior_dev_r']:+.4f}        —         — "
                  f"        —  REFUSED_MARKET  {c['prior_label']}")
            continue
        print(f"  {c['instrument']:<12} {c['prior_dev_r']:+.4f}  "
              f"{c['ceiling_green_vehicle']:>8.2f}  "
              f"{c['ceiling_yellow_vehicle']:>8.2f}  "
              f"{c['t1_score_needed_yellow']:>9.1f}  "
              f"{'yes' if c['a_plus_reachable'] else 'NO':<10}  "
              f"{c['prior_label']}")
    print("  a prior refuses the instrument only where the five-year pool showed "
          "the negative holding")
    print("  out of sample. A prior it cannot separate from zero lowers the "
          "ceiling instead — research")
    print("  eligibility only, and no production status changed either way.")

    if show_opportunities:
        print("\nper opportunity (the real denominator):")
        for o in result["per_opportunity"]:
            mark = "A+" if o["reached_a_plus"] else "  "
            print(f"\n {mark} {o['instrument']:<11} {o['symbol'] or '-':<20} "
                  f"{o['direction'] or '-':<8} {o['ticks']:>5} ticks   "
                  f"best {o['best_score']}   median {o['median_score']}")
            print(f"      labels: {o['labels']}")
            if o["blocked_on_every_tick"]:
                print(f"      blocked on EVERY tick: "
                      f"{', '.join(o['blocked_on_every_tick'])}")
            if o["blocked_on_some_tick"]:
                print(f"      blocked on some ticks only: "
                      f"{', '.join(o['blocked_on_some_tick'])}")
            print(f"      median components: {o['median_components']}")

    _print_room_ab(result["room_ab"])

    print("\nNo threshold was changed by running this. It re-reads recorded "
          "rows and recorded scores.")


def _print_room_ab(ab_res: dict) -> None:
    """The one re-graded section: what a derived T1 changes on rows already held."""
    print("\nroom A/B — what a MODELLED T1 changes (re-graded, nothing written)")
    print(f"  rows re-graded                {ab_res['rows_considered']:>6}")
    print(f"  room measured, v1             {ab_res['room_measured_v1']:>6}   "
          "engine target only")
    print(f"  room measured, v2             {ab_res['room_measured_v2']:>6}   "
          "engine target or derived")
    for why, n in (ab_res.get("room_still_unmeasured_because") or {}).items():
        print(f"    still unmeasured: {why:<38} {n:>5}")
    print(f"  A+ opportunities, v1          {ab_res['a_plus_opportunities_v1']:>6}")
    print(f"  A+ opportunities, v2          {ab_res['a_plus_opportunities_v2']:>6}")
    print(f"  of which promotable           "
          f"{ab_res['promotable_opportunities_v2']:>6}   "
          "ENGINE_TARGET rows only ever enter the costed book")
    print(f"  t1 basis: {ab_res['t1_basis']}")
    if ab_res["label_moves"]:
        print(f"  label moves: {ab_res['label_moves']}")
    if ab_res["regression_mismatch_count"]:
        print(f"  !! {ab_res['regression_mismatch_count']} ENGINE_TARGET row(s) "
              "graded differently — the change was meant to be inert there:")
        for m in ab_res["regression_mismatches"][:5]:
            print(f"     {m['observation_id']}: {m['recorded']} -> {m['regraded']}")
    print(f"  VERDICT: {ab_res['verdict']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sessions", type=int, default=None,
                    help="audit only the last N recorded sessions")
    ap.add_argument("--opportunities", action="store_true",
                    help="print the per-opportunity breakdown")
    ap.add_argument("--json", action="store_true", help="print JSON")
    args = ap.parse_args()

    result = build(args.sessions)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return
    _print(result, show_opportunities=args.opportunities)


if __name__ == "__main__":
    main()
