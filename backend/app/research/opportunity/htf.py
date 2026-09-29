"""The cross-timeframe table: does the edge grow faster than the cost?

This module answers one question and refuses the others. The first cycle
established that the binding constraint is not direction but arithmetic — the
modelled round trip was roughly three times the best gross edge any candidate
could demonstrate on a usable sample. The round trip is charged per trade, so
the ratio can only move if the per-trade move gets bigger, and the per-trade
move is bounded by the bar.

So the table below reports, per timeframe: how much gross edge a trade earned,
what the round trip cost, and the ratio between them. ``cost_multiple`` below
1.0 is the only reading that means a candidate pays for its own execution.

Three things this report will not do:

* **It will not rank by net and present the winner.** The best row of a family
  is where luck accumulates, so every row carries its trade count and the table
  separates "best on a usable sample" from "best", which are different rows and
  in the first cycle were 8× apart.
* **It will not pool timeframes.** A 15-minute breakout and a one-minute
  breakout are two hypotheses, counted as two in the false-discovery
  correction, and averaged together they would hide whichever one is real.
* **It will not call a positive row a finding.** A cost multiple below 1.0 in
  training is a lead for validation, holdout and then live shadow, where the
  cost stops being modelled. Nothing here can promote anything, and a modelled
  cost is the single number this entire conclusion is most sensitive to.
"""
from __future__ import annotations

from app.research.opportunity import SCREEN_FILE
from app.research.opportunity import bars as oppbars
from app.research.opportunity import screen, stats, store

# A usable sample for reading a per-trade mean. Below this the mean is a
# statement about a handful of trades, and in the first cycle the top three
# gross edges of 82 candidates had 2, 0 and 11 trades behind them.
USABLE_TRADES = 100

# The share of overnight trades at which a timeframe stops being a longer
# version of the intraday study and becomes a different one. Half is a
# declared line, not a measured threshold, and it changes only the wording of
# the verdict: no row is dropped or rescored on it.
OVERNIGHT_MATERIAL = 0.50


def _rows() -> list[dict]:
    """The latest screening row per candidate.

    The store is append-only and a re-measurement writes a new row rather than
    editing the old one, so a candidate can hold more than one. The last row
    written is the one read: counting both would enter the same candidate into
    the comparison twice and let a re-measured family outvote a screened one.
    """
    latest: dict[str, dict] = {}
    for r in store.read(SCREEN_FILE):
        if r.get("screenable"):
            latest[str(r.get("candidate_id"))] = r
    return list(latest.values())


def _summarise(rows: list[dict], period: str) -> dict:
    """Per-timeframe aggregates over one chronological period."""
    by_tf: dict[int, list[dict]] = {}
    for r in rows:
        tf = int(r.get("timeframe_minutes") or 1)
        by_tf.setdefault(tf, []).append(r)

    out: list[dict] = []
    for tf in sorted(by_tf):
        got = by_tf[tf]
        enough = [r for r in got
                  if int((r.get(period) or {}).get("trades") or 0) >= USABLE_TRADES]
        # A row screened before gross and cost were recorded separately carries
        # no gross figure. It is counted as unrecorded and left out of every
        # mean below, because reading a missing field as 0.0 would publish a
        # zero edge that was never measured.
        usable = [r for r in enough
                  if (r.get(period) or {}).get("gross_mean_pct") is not None]
        gross = [float((r[period] or {})["gross_mean_pct"]) for r in usable]
        best = max(usable,
                   key=lambda r: float((r[period] or {})["gross_mean_pct"]),
                   default=None)
        holds = [h for h in ((r.get(period) or {}).get("median_hold_min")
                             for r in usable) if h is not None]
        out.append({
            "timeframe": oppbars.label(tf),
            "timeframe_minutes": tf,
            "candidates": len(got),
            "usable_candidates": len(usable),
            "gross_unrecorded": len(enough) - len(usable),
            "median_gross_pct": stats.median(gross),
            "best_gross_pct": max(gross) if gross else None,
            "gross_positive": sum(1 for g in gross if g > 0),
            "best_candidate": (best or {}).get("candidate_name"),
            "best_trades": int(((best or {}).get(period) or {}).get("trades") or 0)
            if best else None,
            "best_cost_multiple": ((best or {}).get(period) or {}).get(
                "cost_multiple") if best else None,
            "best_net_pct": ((best or {}).get(period) or {}).get("net_mean_pct")
            if best else None,
            "median_hold_min": stats.median(holds),
            # How much of the best row was held through a close. A wide bar and
            # a bar-counted time stop can run past the session end, and an
            # overnight future carries a gap the intraday cost model does not
            # price. A high share here means the timeframe is being compared on
            # a different kind of trade, not a longer version of the same one.
            "best_overnight_share": ((best or {}).get(period) or {}).get(
                "overnight_share") if best else None,
            "pays_for_itself": bool(gross) and max(gross) > (
                screen.MODELLED_ROUND_TRIP_PCT),
        })
    return {
        "period": period,
        "usable_trade_floor": USABLE_TRADES,
        "round_trip_pct": screen.MODELLED_ROUND_TRIP_PCT,
        "timeframes": out,
    }


def report() -> dict:
    """The timeframe comparison over train, validation and the holdout.

    The holdout is reported because withholding a number that has already been
    computed is its own dishonesty — but it is reported *beside* the others and
    it admits nothing: eligibility is decided on training and validation, and
    the holdout column exists so a reader can see whether a candidate that
    cleared them also survived a period it never influenced.
    """
    rows = _rows()
    if not rows:
        return {
            "status": "NOTHING_SCREENED",
            "reason": ("no screened candidate is on the record. Run a cycle; "
                       "this report reads results, it does not produce them."),
            "timeframes": [oppbars.label(t) for t in oppbars.TIMEFRAMES],
        }
    train = _summarise(rows, "train")
    best = [t for t in train["timeframes"] if t["best_gross_pct"] is not None]
    leader = max(best, key=lambda t: t["best_gross_pct"], default=None)
    unrecorded = sum(t["gross_unrecorded"] for t in train["timeframes"])
    pays = any(t["pays_for_itself"] for t in best)
    if not best:
        verdict = (
            "NO_TIMEFRAME_HAS_A_READABLE_GROSS_EDGE"
            if not unrecorded else
            f"GROSS_NOT_RECORDED_ON_{unrecorded}_ROW_S_RESCREEN_TO_COMPARE"
        )
    elif pays:
        verdict = (
            "AT_LEAST_ONE_TIMEFRAME_SHOWS_A_GROSS_EDGE_ABOVE_THE_MODELLED_COST"
        )
        # The leader is allowed to keep its edge and still be the wrong
        # comparison. Past half the trades held through a close, the round trip
        # it beat is an intraday assumption applied to an overnight position,
        # so the reading is named rather than presented as a like-for-like win
        # over the one-minute family.
        night = (leader or {}).get("best_overnight_share")
        if night is not None and night >= OVERNIGHT_MATERIAL:
            verdict += (
                f"_BUT_{round(night * 100)}PCT_OF_THE_LEADER_WAS_HELD_"
                "THROUGH_A_CLOSE_AND_THE_ROUND_TRIP_IS_INTRADAY"
            )
    else:
        verdict = "NO_TIMEFRAME_SHOWS_A_GROSS_EDGE_ABOVE_THE_MODELLED_COST"
    return {
        "status": "MEASURED",
        "screened_rows": len(rows),
        "gross_unrecorded_rows": unrecorded,
        "train": train,
        "validation": _summarise(rows, "validation"),
        "holdout": _summarise(rows, "holdout"),
        "leading_timeframe": (leader or {}).get("timeframe"),
        "any_timeframe_pays_for_itself": pays,
        "verdict": verdict,
        "limits": [
            "a row screened before gross and cost were recorded separately "
            "carries no gross figure; it is counted as unrecorded and excluded "
            "from every mean rather than read as a zero edge",
            "gross above the modelled cost in training is a lead for "
            "validation, holdout and live shadow, not a result",
            "the cost is modelled: a candle carries no book, so the round trip "
            "is an assumption and it is the number this conclusion is most "
            "sensitive to",
            "a bar-counted time stop on a wide bar can run past the close, so "
            "the overnight share is reported per timeframe: a candidate held "
            "through a close carries a gap the modelled intraday round trip "
            "does not price, and is not the same trade as its 1m counterpart",
            "a bar containing both target and stop is scored as the stop at "
            "every timeframe; the wider the bar, the more that convention "
            "costs, and it is not relaxed to make a wider bar look better",
            "each timeframe is a separate hypothesis in the false-discovery "
            "correction; nothing here is pooled across bar lengths",
        ],
    }
