"""What does waiting for a pullback actually cost? — Phase 13 Parts 3, 4. RESEARCH ONLY.

The user's instinct is that a call should be bought on the retrace, not on the
expansion, and the 26 Aug Crude chart is a good argument for it. Part 4 exists
because it is only half the argument: a pullback method also has to be charged for
the winners it never got filled on, and on this book the median winner reached T1
in 7.8 minutes, so a fair number of them never came back at all.

Each definition is a depth below the signal premium, expressed in the trade's own
risk so an index leg and an MCX leg are judged on the same scale, plus the
percentage-of-premium definitions the spec lists. For every definition:

* **fill** — the premium reached that depth *before* the trade resolved;
* **filled economics** — the same stop and the same first target as the production
  plan (the spec forbids re-pricing either), so the only thing that changes is what
  was paid. Reported twice: in the original risk unit, which is the comparable
  number, and in the filled trade's own smaller risk unit;
* **the cost of waiting** — for every signal that never filled, the R the immediate
  entry did make and the MFE it did reach. A method that improves its fills while
  skipping the winners is visible here and nowhere else.

Method and its limits, stated because they bound every number below:

1. fills are detected from the latched MAE and its timestamp, so a dip that reached
   the level without being the deepest point of the trade is not counted. Every
   fill rate here is therefore a **lower bound**, and the missed-move column an
   upper bound;
2. the exit is the production resolution. This study does not re-run the exit, so
   it cannot claim a pullback entry would have been managed differently;
3. underlying-level definitions (VWAP / EMA / breakout / swing / fair-value-gap
   retests) need the minute premium path and are measured by
   :mod:`app.research.phase7.policies` on a recorded chain slice, not here. This
   module deliberately reports only what the outcome ledger can support.
"""
from __future__ import annotations

from app.analysis import label_outcomes

# Depth below the signal premium, in fractions of the plan's own risk (R).
R_DEPTHS = (0.1, 0.2, 0.3, 0.5)
# Depth as a percentage of the signal premium, which is how a trader reads it.
PCT_DEPTHS = (2.0, 5.0, 10.0)


def _num(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _first_milestone_min(row: dict) -> float | None:
    """When the trade first did something — the deadline a dip has to beat."""
    times = [_num(row.get(k)) for k in ("minutes_to_t1", "minutes_to_stop")]
    known = [t for t in times if t is not None]
    if known:
        return min(known)
    return _num(row.get("minutes_to_resolution"))


def _depth_r(row: dict, depth_r: float | None, depth_pct: float | None) -> float | None:
    """The definition's depth for this row, always expressed in R."""
    if depth_r is not None:
        return depth_r
    entry = _num(row.get("entry"))
    risk = _num(row.get("risk_points"))
    if not entry or not risk or risk <= 0 or depth_pct is None:
        return None
    return (entry * depth_pct / 100.0) / risk


def simulate(row: dict, depth_r: float | None = None,
             depth_pct: float | None = None) -> dict:
    """One definition against one resolved call."""
    depth = _depth_r(row, depth_r, depth_pct)
    mae_r = _num(row.get("mae_r"))
    gross = _num(row.get("gross_r"))
    cost = _num(row.get("cost_r"))
    when_dip = _num(row.get("minutes_to_mae"))
    deadline = _first_milestone_min(row)
    if depth is None or depth <= 0 or mae_r is None or gross is None:
        return {"evaluated": False, "filled": False}
    dipped = mae_r <= -depth
    in_time = (when_dip is not None and deadline is not None
               and when_dip <= deadline)
    filled = bool(dipped and (in_time or deadline is None))
    if not filled:
        return {
            "evaluated": True, "filled": False,
            "missed_gross_r": gross,
            "missed_net_r": (round(gross - cost, 3) if cost is not None else None),
            "missed_mfe_r": _num(row.get("mfe_r")),
            "immediate_continuation": bool(row.get("target_before_stop")),
        }
    # Same stop, same first target: only the price paid changes.
    same_unit = round(gross + depth, 3)
    own_unit = round(same_unit / max(0.05, 1.0 - depth), 3)
    mfe = _num(row.get("mfe_r"))
    return {
        "evaluated": True, "filled": True,
        "depth_r": round(depth, 3),
        "gross_r_same_unit": same_unit,
        "gross_r_own_risk": own_unit,
        "net_r_same_unit": (round(same_unit - cost, 3) if cost is not None else None),
        "mfe_r_same_unit": (round(mfe + depth, 3) if mfe is not None else None),
        "minutes_to_fill": when_dip,
        "target_before_stop": bool(row.get("target_before_stop")),
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ys = sorted(values)
    mid = len(ys) // 2
    return round(ys[mid] if len(ys) % 2 else (ys[mid - 1] + ys[mid]) / 2.0, 4)


def definition(name: str, rows: list[dict], *, depth_r: float | None = None,
               depth_pct: float | None = None) -> dict:
    """One pullback definition across every resolved call. Both sides reported."""
    pairs = [(r, simulate(r, depth_r, depth_pct)) for r in rows]
    fills = [f for _, f in pairs]
    usable = [f for f in fills if f["evaluated"]]
    took = [f for f in usable if f["filled"]]
    missed = [f for f in usable if not f["filled"]]
    net = [f["net_r_same_unit"] for f in took if f["net_r_same_unit"] is not None]
    # The book comparison must be over one identical subset of signals, so both
    # sides are computed on the rows that are evaluable *and* costed.
    comparable = [(r, f) for r, f in pairs
                  if f["evaluated"] and _num(r.get("net_r")) is not None]
    book_net = [(f["net_r_same_unit"] or 0.0) if f["filled"] else 0.0
                for _, f in comparable]
    control_net = [_num(r.get("net_r")) for r, _ in comparable]
    control_net = [v for v in control_net if v is not None]
    # Why a definition looks better than the control, split into the only two
    # things it can be: a cheaper price on the trades it did take, or the trades
    # it declined. On a book with negative expectancy the second term flatters
    # any method that simply trades less, so it is never left implicit.
    filled_pairs = [(r, f) for r, f in comparable if f["filled"]]
    missed_pairs = [(r, f) for r, f in comparable if not f["filled"]]
    control_filled = _mean([v for v in (_num(r.get("net_r"))
                                        for r, _ in filled_pairs) if v is not None])
    control_missed = _mean([v for v in (_num(r.get("net_r"))
                                        for r, _ in missed_pairs) if v is not None])
    pull_filled = _mean([f["net_r_same_unit"] or 0.0 for _, f in filled_pairs])
    n_cmp = len(comparable)
    share_filled = (len(filled_pairs) / n_cmp) if n_cmp else None
    from_entry = (None if (share_filled is None or pull_filled is None
                           or control_filled is None)
                  else round(share_filled * (pull_filled - control_filled), 4))
    from_declining = (None if (share_filled is None or control_missed is None)
                      else round(-(1.0 - share_filled) * control_missed, 4))
    missed_net = [f["missed_net_r"] for f in missed if f["missed_net_r"] is not None]
    missed_mfe = [f["missed_mfe_r"] for f in missed if f["missed_mfe_r"] is not None]
    wins = [v for v in net if v > 0]
    return {
        "definition": name,
        "depth_r": depth_r,
        "depth_pct_of_premium": depth_pct,
        "evaluated": len(usable),
        "unevaluable": len(fills) - len(usable),
        "filled": len(took),
        "fill_rate_pct_lower_bound": (round(100.0 * len(took) / len(usable), 1)
                                      if usable else None),
        "missed": len(missed),
        "median_minutes_to_fill": _median([f["minutes_to_fill"] for f in took
                                           if f["minutes_to_fill"] is not None]),
        "filled_expectancy_net_r": _mean(net),
        "filled_win_rate_net_pct": (round(100.0 * len(wins) / len(net), 1)
                                    if net else None),
        "filled_target_before_stop_pct": (round(100.0 * sum(
            1 for f in took if f["target_before_stop"]) / len(took), 1)
            if took else None),
        "filled_median_mfe_r": _median([f["mfe_r_same_unit"] for f in took
                                        if f["mfe_r_same_unit"] is not None]),
        # What waiting gave up: the immediate entry's own result on the signals
        # this definition never got into.
        "missed_expectancy_net_r": _mean(missed_net),
        "missed_net_r_total": (round(sum(missed_net), 3) if missed_net else None),
        "missed_median_mfe_r": _median(missed_mfe),
        "missed_winners": sum(1 for v in missed_net if v > 0),
        "immediate_continuation_pct": (round(100.0 * sum(
            1 for f in missed if f["immediate_continuation"]) / len(missed), 1)
            if missed else None),
        "control_expectancy_net_r": _mean(control_net),
        # The only fair headline: what the whole book would have made under this
        # definition, counting a skipped signal as zero rather than as absent.
        "book_expectancy_net_r_zero_for_missed": _mean(book_net),
        "compared_on_signals": n_cmp,
        "control_expectancy_on_filled_r": control_filled,
        "control_expectancy_on_declined_r": control_missed,
        "pullback_expectancy_on_filled_r": pull_filled,
        "gain_from_better_entry_r": from_entry,
        "gain_from_declining_trades_r": from_declining,
        "improvement_source": (
            None if from_entry is None or from_declining is None
            else ("DECLINED_TRADES" if from_declining > from_entry
                  else "BETTER_ENTRY")),
        # A deep dip is itself a forecast: the subset that offered the retrace is
        # the subset that went on to lose. Where this is true, the entry gain is
        # measured on the book's worst trades and cannot be read as an edge.
        "dip_selects_losers": (
            None if control_filled is None or not control_net
            else bool(control_filled < sum(control_net) / len(control_net))),
        "beats_control_on_book_expectancy": (
            None if not book_net or not control_net else
            bool(sum(book_net) / len(book_net)
                 > sum(control_net) / len(control_net))),
    }


def study(rows: list[dict]) -> dict:
    """Parts 3/4 — every definition, with the cost of waiting beside the benefit."""
    sessions = len({r["session"] for r in rows if r.get("session")})
    definitions = [definition(f"R_RETRACE_{int(d * 100)}", rows, depth_r=d)
                   for d in R_DEPTHS]
    definitions += [definition(f"PCT_RETRACE_{p:g}", rows, depth_pct=p)
                    for p in PCT_DEPTHS]
    ranked = [d for d in definitions
              if d["filled"] >= label_outcomes.MIN_COHORT
              and d["book_expectancy_net_r_zero_for_missed"] is not None]
    best = (max(ranked, key=lambda d: d["book_expectancy_net_r_zero_for_missed"])
            if ranked else None)
    by_entry = [d for d in ranked if d["gain_from_better_entry_r"] is not None]
    best_entry = (max(by_entry, key=lambda d: d["gain_from_better_entry_r"])
                  if by_entry else None)
    return {
        "part": "13 Parts 3, 4",
        "research_only": True,
        "resolved": len(rows),
        "sessions": sessions,
        "control": label_outcomes.cohort("CONTROL_IMMEDIATE", rows, sessions),
        "definitions": definitions,
        "best_by_book_expectancy": (None if best is None else {
            "definition": best["definition"],
            "book_expectancy_net_r_zero_for_missed":
                best["book_expectancy_net_r_zero_for_missed"],
            "fill_rate_pct_lower_bound": best["fill_rate_pct_lower_bound"],
            "improvement_source": best["improvement_source"],
            "still_negative": (best["book_expectancy_net_r_zero_for_missed"] < 0),
            "status": label_outcomes.IN_SAMPLE_ONLY,
        }),
        "best_by_entry_gain": (None if best_entry is None else {
            "definition": best_entry["definition"],
            "gain_from_better_entry_r": best_entry["gain_from_better_entry_r"],
            "fill_rate_pct_lower_bound": best_entry["fill_rate_pct_lower_bound"],
            "dip_selects_losers": best_entry["dip_selects_losers"],
            "status": label_outcomes.IN_SAMPLE_ONLY,
        }),
        "headline_caveat": (
            "the definition with the highest book expectancy on this book is the "
            "one that trades least, and no definition turns the book positive. "
            "Read improvement_source and dip_selects_losers before reading the "
            "ranking: where the retrace is what predicted the loss, a better fill "
            "price is being measured on the worst trades in the book"),
        "status": (label_outcomes.IN_SAMPLE_ONLY if ranked
                   else label_outcomes.INSUFFICIENT),
        "level_based_definitions": {
            "status": "MEASURED_ELSEWHERE",
            "policies": ["VWAP_RETEST", "EMA9_RETEST", "EMA20_RETEST", "PREV_MID",
                         "BREAKOUT_RETEST", "SWING_RETEST", "FVG_RETEST",
                         "NEXT_CANDLE_CONFIRM"],
            "note": ("these need the minute premium path, so they run in "
                     "app.research.phase7.policies over a recorded chain slice "
                     "rather than over the outcome ledger; each one reports the "
                     "signals it declined, the winners among them and their "
                     "immediate-entry expectancy, so a policy is never credited "
                     "only for the trades it chose to take"),
        },
        "notes": [
            "fill detection uses the latched MAE and its timestamp, so a dip that "
            "touched the level without being the deepest point is not counted: "
            "every fill rate here is a lower bound and every missed-move figure "
            "an upper bound",
            "filled trades keep the production stop and the production first "
            "target; only the price paid differs",
            "book expectancy counts a skipped signal as zero rather than as "
            "absent, but on a book whose control expectancy is negative that is "
            "not enough on its own: declining trades raises it mechanically. "
            "gain_from_better_entry_r against gain_from_declining_trades_r "
            "separates the two, and a definition whose improvement_source is "
            "DECLINED_TRADES is evidence about the book, not about the entry",
            "no definition here is wired to anything: the production entry is "
            "unchanged",
        ],
    }
