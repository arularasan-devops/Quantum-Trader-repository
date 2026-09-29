"""§5/§6/§16 — the historical fast filter.

This is the stage that makes a wide funnel affordable: cheap, bounded,
chronological tests that remove candidates before any of them consumes live
capture. Four things about how it is done matter more than the speed.

**Entry is the next bar's open, never the decision bar's close.** The close is
the price that triggered the rule; filling at it is a fill at a price that was
only knowable once the bar had ended. One bar of delay is the smallest honest
assumption a candle series supports, and it is applied to every candidate.

**A bar that contains both the target and the stop is scored as the stop.**
One-minute bars do not say which came first, and the optimistic reading of that
ambiguity is worth several percent of annual return purely as a modelling
choice. The pessimistic reading costs nothing except candidates that were never
real.

**Costs are modelled and declared as modelled.** Candle history carries no bid
or ask, so the round-trip is a percentage assumption, not a measured spread.
Every screened number therefore carries ``SPREAD_UNMEASURED``, and nothing here
can promote anything: the screen's best verdict is eligibility for live shadow
observation, where real quotes exist.

**The kill rules are the pre-registered ones and nothing else.** They are
listed in :mod:`app.research.opportunity` before any candidate is scored, and
none of them reads "currently losing on a small sample" — that one is named
there as an explicit non-reason, because it is the rule a fast funnel reaches
for by default and it selects for luck.

Subsampling is declared, not hidden: the screen evaluates every ``bar_stride``
bar. A five-year one-minute series is ~500k bars and a cycle scores hundreds of
candidates, so the alternative to a stated stride is a screen nobody ever
finishes running. The stride is recorded on every result row.
"""
from __future__ import annotations

import datetime as dt
import zoneinfo

import numpy as np

from app.research.opportunity import (
    CATASTROPHIC_NET_PCT,
    FDR_Q,
    HISTORICAL_REJECTED,
    HISTORICAL_TESTING,
    KILL_CATASTROPHIC,
    KILL_LOOK_AHEAD,
    KILL_NEGATIVE_IN_TRAIN,
    KILL_ONE_SESSION,
    KILL_OUTLIER,
    KILL_TOO_FEW,
    KILL_UNSTABLE,
    MAX_ONE_SESSION_SHARE,
    MAX_ONE_TRADE_SHARE,
    MIN_SCREEN_SESSIONS,
    MIN_TRAIN_TRADES,
    NO_EXECUTABLE_PRICE,
    NO_HISTORY,
    NOT_SIGNIFICANT,
    SCHEMA_VERSION,
    SCREEN_FILE,
    SHADOW,
    SURVIVES_CORRECTED,
    UNCORRECTED_ONLY,
    UNMEASURED,
)
from app.research.opportunity import bars as oppbars
from app.research.opportunity import guard, mechanisms as mech
from app.research.opportunity import registry, stats, store
from app.research.opportunity.generator import EXITS

_IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# Evaluate one bar in this many. Declared on every row; see the module note.
DEFAULT_STRIDE = 5

# Modelled round-trip cost as a percentage of entry, used because candle data
# has no book. Deliberately not optimistic.
MODELLED_ROUND_TRIP_PCT = 0.06


def _series(instrument: str, timeframe: int = 1):
    return oppbars.series_at(instrument, timeframe)


def clear_cache() -> None:
    oppbars.clear_cache()


def _timeframe(candidate: dict) -> int:
    """The candidate's declared bar length in minutes; one minute if unstated.

    Unstated means the candidate predates the timeframe sweep. It is read as one
    minute rather than re-fingerprinted, because a registered definition is
    immutable and a definition that changes shape when the code grows is not a
    definition.
    """
    entry = candidate.get("entry_definition") or {}
    return int(entry.get("timeframe_minutes") or 1)


def horizon_bars(exit_rule: dict, timeframe: int) -> int:
    """The time stop in bars of this candidate's own timeframe.

    A rule stated in bars is used as stated: across a timeframe comparison, the
    same number of bars gives every arm the same number of decision units, and
    lets the wall-clock hold scale with the bar — which is the thing under test.
    A rule stated in minutes is converted, because reading a 120-minute stop as
    120 *bars* of 15 minutes is a 30-hour hold wearing a two-hour label.
    """
    if exit_rule.get("time_stop_bars"):
        return max(1, int(exit_rule["time_stop_bars"]))
    minutes = int(exit_rule.get("time_stop_min") or 60)
    return max(1, minutes // max(1, int(timeframe)))


def _resolve_exit(candidate: dict) -> dict:
    """The exit the candidate was registered with, not the one the code has now.

    The stored definition wins on every field it carries, and the named rule in
    :data:`EXITS` only supplies what a definition omits. Reading the constant
    instead would re-screen a frozen candidate under whatever geometry the
    current source happens to hold, which is exactly the silent drift the
    fingerprint exists to prevent.
    """
    stored = dict(candidate.get("exit_definition") or {})
    base = dict(EXITS.get(str(stored.get("rule")), EXITS["T1_THEN_BREAKEVEN"]))
    base.update({k: v for k, v in stored.items() if k != "rule"})
    return base


def _atr(series, i: int, lookback: int = 30) -> float:
    lo = max(0, i - lookback + 1)
    rng = series.high[lo:i + 1] - series.low[lo:i + 1]
    return float(np.mean(rng)) if rng.size else 0.0


def simulate_trade(series, entry_idx: int, direction: str, exit_rule: dict,
                   atr: float, timeframe: int = 1) -> dict | None:
    """One trade from the bar after the decision, resolved on later bars.

    Returns ``None`` when the series ends before the trade could resolve: an
    unresolved trade is not a flat trade, and counting it as zero would reward
    candidates that trade near the end of the data.
    """
    n = len(series.ts)
    if entry_idx + 1 >= n or atr <= 0:
        return None
    fill_idx = entry_idx + 1
    entry = float(series.open[fill_idx])
    if entry <= 0:
        return None
    sign = 1.0 if direction == mech.LONG else -1.0
    stop_dist = float(exit_rule["stop_atr"]) * atr
    t1_dist = (float(exit_rule["t1_atr"]) * atr
               if exit_rule.get("t1_atr") else None)
    horizon = horizon_bars(exit_rule, timeframe)
    stop = entry - sign * stop_dist
    target = entry + sign * t1_dist if t1_dist else None
    trail_frac = (exit_rule.get("trail") or {}).get("giveback_frac")

    best = entry
    reached_t1 = False
    last = min(n - 1, fill_idx + horizon)
    for j in range(fill_idx, last + 1):
        hi, lo = float(series.high[j]), float(series.low[j])
        favourable = hi if sign > 0 else lo
        best = max(best, favourable) if sign > 0 else min(best, favourable)

        hit_stop = (lo <= stop) if sign > 0 else (hi >= stop)
        hit_t1 = target is not None and (
            (hi >= target) if sign > 0 else (lo <= target))

        # Ambiguity inside a bar is always resolved against the trade.
        if hit_stop:
            return _result(series, fill_idx, j, entry, stop, sign, "STOP",
                           best, reached_t1)
        if hit_t1 and not reached_t1:
            reached_t1 = True
            if exit_rule.get("breakeven_after_t1"):
                stop = entry + sign * (MODELLED_ROUND_TRIP_PCT / 100.0) * entry
            if trail_frac is None:
                return _result(series, fill_idx, j, entry, target, sign,
                               "TARGET", best, True)
        if reached_t1 and trail_frac is not None:
            give = abs(best - entry) * float(trail_frac)
            trail_stop = best - sign * give
            if (sign > 0 and lo <= trail_stop) or (sign < 0 and hi >= trail_stop):
                return _result(series, fill_idx, j, entry, trail_stop, sign,
                               "TRAIL", best, True)
    if last <= fill_idx:
        return None
    return _result(series, fill_idx, last, entry, float(series.close[last]),
                   sign, "TIME_STOP", best, reached_t1)


def _result(series, fill_idx: int, exit_idx: int, entry: float, exit_px: float,
            sign: float, reason: str, best: float, reached_t1: bool) -> dict:
    gross_pct = 100.0 * sign * (exit_px - entry) / entry
    net_pct = gross_pct - MODELLED_ROUND_TRIP_PCT
    return {
        "entry_ts": int(series.ts[fill_idx]),
        "exit_ts": int(series.ts[exit_idx]),
        # IST day, not UTC day: an MCX session runs to 23:30 IST and a UTC
        # bucket would split it, inflating the session count that the
        # one-session concentration test divides by.
        "session": int((int(series.ts[fill_idx]) + 19_800) // 86_400),
        # A time stop counted in bars runs past the close once the bar is wide
        # enough: 30 bars of 60m is longer than any session. Such a trade is
        # held overnight, which is a different trade — it carries gap risk and
        # the modelled round trip is an intraday assumption. The resolution is
        # not changed here, because the candidate definitions are frozen; the
        # crossing is recorded so the share is a reported number and not a
        # silent property of the wider bars.
        "crossed_session": (
            int((int(series.ts[exit_idx]) + 19_800) // 86_400)
            != int((int(series.ts[fill_idx]) + 19_800) // 86_400)
        ),
        "entry": entry,
        "exit": exit_px,
        "gross_pct": gross_pct,
        "net_pct": net_pct,
        "cost_pct": MODELLED_ROUND_TRIP_PCT,
        "cost_basis": "MODELLED_NO_BOOK_IN_CANDLE_DATA",
        "exit_reason": reason,
        "reached_t1": reached_t1,
        "hold_min": int((int(series.ts[exit_idx]) - int(series.ts[fill_idx])) // 60),
        "best_pct": 100.0 * sign * (best - entry) / entry,
        "spread": UNMEASURED,
    }


def _run_candidate(candidate: dict, *, stride: int) -> dict:
    """Every trade this candidate would have taken, or the reason there are none."""
    entry = candidate.get("entry_definition") or {}
    mech_name = str(entry.get("mechanism"))
    spec = mech.GRID.get(mech_name)
    if spec is None:
        return {"trades": [], "absent": "UNKNOWN_MECHANISM"}

    scope = candidate.get("instrument_scope") or []
    if not scope:
        return {"trades": [], "absent": NO_HISTORY}
    timeframe = _timeframe(candidate)
    primary = _series(str(scope[0]), timeframe)
    if primary is None or len(primary) == 0:
        return {"trades": [], "absent": NO_HISTORY}

    vehicles = candidate.get("vehicle_scope") or []
    if any(v in ("CE", "PE") for v in vehicles):
        # An option candidate cannot be priced on candle history at all. This is
        # an absence of data, not a negative result, and the two must not share
        # a row in any table.
        return {"trades": [], "absent": NO_EXECUTABLE_PRICE}

    other = None
    pair = entry.get("pair_with")
    if pair:
        other = _series(str(pair), timeframe)
        if other is None or len(other) == 0:
            return {"trades": [], "absent": NO_HISTORY}

    params = dict(entry.get("params") or {})
    lookback = int(params.get("lookback") or 30)
    exit_rule = _resolve_exit(candidate)
    fn = spec["fn"]
    n = len(primary)
    trades: list[dict] = []
    busy_until_ts = -1
    other_index = ({int(t): k for k, t in enumerate(other.ts)}
                   if other is not None else None)

    # The stride bounds one-minute compute; it is a wall-clock sampling
    # interval, not a bar count. Applying 20 unchanged to daily bars would
    # sample one session in twenty and call the result a daily study.
    effective_stride = max(1, int(stride) // max(1, timeframe))

    for i in range(lookback, n - 1, effective_stride):
        if int(primary.ts[i]) <= busy_until_ts:
            continue
        w = mech.window_at(primary, i, lookback)
        if w is None:
            continue
        if other_index is not None:
            k = other_index.get(int(primary.ts[i]))
            if k is None:
                continue  # no same-instant quote on the other leg: not a pair
            w2 = mech.window_at(other, k, lookback)
            if w2 is None:
                continue
            decision = fn(w, w2, **params)
        else:
            decision = fn(w, **params)
        if decision is None:
            continue
        trade = simulate_trade(primary, i, decision.direction, exit_rule,
                               _atr(primary, i), timeframe)
        if trade is None:
            continue
        trade["direction"] = decision.direction
        trade["why"] = decision.why
        trades.append(trade)
        # One position at a time: overlapping entries turn one lucky move into
        # a dozen independent-looking winners. Compared on timestamps rather
        # than bar indices — a held minute and a series index only coincide
        # inside one continuous session, and adding a minute count to an index
        # silently shortens the block across every overnight gap.
        busy_until_ts = trade["exit_ts"]
    return {"trades": trades, "absent": None, "timeframe_minutes": timeframe,
            "effective_stride": effective_stride,
            "bars": n, "horizon_bars": horizon_bars(exit_rule, timeframe)}


def _period_slice(trades: list[dict], window: list[int]) -> list[dict]:
    lo, hi = int(window[0]), int(window[1])
    return [t for t in trades if lo <= t["entry_ts"] < hi]


def _metrics(trades: list[dict]) -> dict:
    """The period's numbers, with gross and cost kept apart from net.

    Net alone cannot say whether a candidate has no edge or has an edge it
    cannot pay for, and those want opposite responses. ``cost_multiple`` is the
    ratio the first cycle's diagnostic had to be reverse-engineered to obtain:
    how many times the round trip exceeds the gross edge it consumes. It is
    ``None`` when gross is not positive, because there is then no edge for the
    cost to be a multiple of — not a ratio of zero.
    """
    nets = [t["net_pct"] for t in trades]
    gross = [t["gross_pct"] for t in trades]
    costs = [t["cost_pct"] for t in trades]
    gross_mean = stats.mean(gross)
    cost_mean = stats.mean(costs)
    return {
        "trades": len(trades),
        "gross_mean_pct": gross_mean,
        "cost_mean_pct": cost_mean,
        "cost_multiple": (cost_mean / gross_mean
                          if gross_mean and gross_mean > 0 and cost_mean
                          else None),
        "median_hold_min": stats.median([t["hold_min"] for t in trades]),
        "sessions": len({t["session"] for t in trades}),
        # The share of trades that were still open at a close. An intraday cost
        # model says nothing about the gap they were exposed to.
        "overnight_share": (
            sum(1 for t in trades if t.get("crossed_session")) / len(trades)
            if trades else None
        ),
        "net_total_pct": sum(nets),
        "net_mean_pct": stats.mean(nets),
        "win_rate": stats.win_rate(nets),
        "profit_factor": stats.profit_factor(nets),
        "max_drawdown_pct": stats.max_drawdown(nets),
        "p_value": stats.p_value(nets),
    }


def _concentration(trades: list[dict]) -> dict:
    """How much of the result rests on one session, and on one trade."""
    nets = [t["net_pct"] for t in trades]
    total = sum(nets)
    if not trades or total <= 0:
        return {"one_session_share": None, "one_trade_share": None,
                "basis": "no positive total to concentrate"}
    by_session: dict[int, float] = {}
    for t in trades:
        by_session[t["session"]] = by_session.get(t["session"], 0.0) + t["net_pct"]
    return {
        "one_session_share": max(by_session.values()) / total,
        "one_trade_share": max(nets) / total,
        "sessions": len(by_session),
    }


def _kill(train: dict, conc: dict, sub_stability: dict,
          trades: list[dict]) -> str | None:
    """The pre-registered kill rules, in order. None of them reads a small sample."""
    if train["trades"] < MIN_TRAIN_TRADES or train["sessions"] < MIN_SCREEN_SESSIONS:
        return KILL_TOO_FEW
    if train["net_mean_pct"] <= CATASTROPHIC_NET_PCT:
        return KILL_CATASTROPHIC
    if train["net_mean_pct"] <= 0:
        return KILL_NEGATIVE_IN_TRAIN
    if sub_stability.get("agree") is False:
        return KILL_UNSTABLE
    share = conc.get("one_session_share")
    if share is not None and share > MAX_ONE_SESSION_SHARE:
        return KILL_ONE_SESSION
    tshare = conc.get("one_trade_share")
    if tshare is not None and tshare > MAX_ONE_TRADE_SHARE:
        return KILL_OUTLIER
    return None


def _sub_periods(trades: list[dict], window: list[int]) -> dict:
    """Thirds of the training window, to see whether it agrees with itself."""
    lo, hi = int(window[0]), int(window[1])
    step = max((hi - lo) // 3, 1)
    means: list[float | None] = []
    for k in range(3):
        part = _period_slice(trades, [lo + k * step,
                                      lo + (k + 1) * step if k < 2 else hi])
        means.append(stats.mean([t["net_pct"] for t in part]) if part else None)
    return stats.stability(means)


def screen_candidate(candidate: dict, *, stride: int = DEFAULT_STRIDE) -> dict:
    """Screen one candidate. No status is written here; the cycle does that."""
    cid = candidate["candidate_id"]
    run = _run_candidate(candidate, stride=stride)
    base = {
        "candidate_id": cid,
        "candidate_name": candidate.get("candidate_name"),
        "mechanism_family": candidate.get("mechanism_family"),
        "instrument_scope": candidate.get("instrument_scope"),
        "vehicle_scope": candidate.get("vehicle_scope"),
        "definition_fingerprint": candidate.get("definition_fingerprint"),
        "bar_stride": stride,
        "timeframe_minutes": _timeframe(candidate),
        "timeframe": oppbars.label(_timeframe(candidate)),
        "effective_stride": run.get("effective_stride"),
        "bars_available": run.get("bars"),
        "horizon_bars": run.get("horizon_bars"),
        "cost_basis": "MODELLED_NO_BOOK_IN_CANDLE_DATA",
        "execution_evidence": UNMEASURED,
        "screened_at": dt.datetime.now(_IST).isoformat(),
        "schema_version": SCHEMA_VERSION,
    }
    if run["absent"]:
        return {**base, "screenable": False, "absent": run["absent"],
                "kill_reason": None,
                "note": (
                    "not a negative result. There is nothing on disk this "
                    "candidate could have been tested against."
                )}

    trades = run["trades"]
    train = _metrics(_period_slice(trades, candidate["train_period"]))
    validation = _metrics(_period_slice(trades, candidate["validation_period"]))
    holdout = _metrics(_period_slice(trades, candidate["holdout_period"]))
    conc = _concentration(_period_slice(trades, candidate["train_period"]))
    sub = _sub_periods(trades, candidate["train_period"])
    kill = _kill(train, conc, sub, trades)
    return {
        **base,
        "screenable": True,
        "absent": None,
        "total_trades": len(trades),
        "train": train,
        "validation": validation,
        "holdout": holdout,
        "concentration": conc,
        "stability": sub,
        "kill_reason": kill,
        "p_value": validation.get("p_value"),
        "p_value_basis": "VALIDATION_PERIOD_MEAN_NET_PER_TRADE_VS_ZERO",
        "holdout_untouched": True,
    }


def run(*, stride: int = DEFAULT_STRIDE, limit: int | None = None,
        q: float = FDR_Q, remeasure: bool = False) -> dict:
    """Screen every candidate awaiting a historical verdict, then correct.

    The correction is applied over everything scored in this run. A candidate
    that clears the raw bar but not the corrected one is kept and labelled
    :data:`UNCORRECTED_ONLY` rather than dropped — it is a lead worth watching,
    and hiding the distinction is what makes a scanner dishonest.

    ``remeasure`` re-runs candidates that already hold a historical rejection,
    so a row screened before gross and cost were recorded separately gains
    those columns. It re-measures and nothing else: the rejection stands, the
    status is not touched, the row cannot be admitted to shadow, and it is kept
    out of the correction family. A rejected candidate that could be re-scored
    into an admission would be a second bite at the same hypothesis, which is
    the exact error the correction exists to prevent.
    """
    from app.research.opportunity import DISCOVERY

    la = guard.audit(mech.ADMISSION_FUNCTIONS)
    cands = registry.candidates()
    pending = [c for c in cands
               if c.get("status") in (DISCOVERY, HISTORICAL_TESTING)]
    if limit:
        pending = pending[:limit]
    again = ([c for c in cands if c.get("status") == HISTORICAL_REJECTED]
             if remeasure else [])

    results: list[dict] = []
    for cand in pending:
        registry.set_status(cand["candidate_id"], HISTORICAL_TESTING)
        res = screen_candidate(cand, stride=stride)
        if la["status"] != guard.CLEAN:
            res["kill_reason"] = KILL_LOOK_AHEAD
            res["look_ahead"] = la
        results.append(res)

    remeasured: list[dict] = []
    for cand in again:
        res = screen_candidate(cand, stride=stride)
        res["remeasured"] = True
        res["remeasure_note"] = (
            "re-measured for the gross and cost columns; the earlier "
            "historical rejection stands and this row admits nothing"
        )
        res["significance"] = NOT_SIGNIFICANT
        res["shadow_admitted"] = False
        remeasured.append(res)
        store.append(SCREEN_FILE, res)

    scored = [r for r in results if r.get("screenable") and not r.get("kill_reason")]
    bh = stats.benjamini_hochberg(
        [(r["candidate_id"], r.get("p_value")) for r in scored], q)
    survivors = set(bh["survivors"])

    promoted, rejected, unmeasurable = [], [], []
    for r in results:
        if not r.get("screenable"):
            unmeasurable.append(r)
            r["significance"] = None
            store.append(SCREEN_FILE, r)
            continue
        if r.get("kill_reason"):
            r["significance"] = NOT_SIGNIFICANT
            registry.set_status(r["candidate_id"], HISTORICAL_REJECTED,
                                reason=r["kill_reason"])
            rejected.append(r)
            store.append(SCREEN_FILE, r)
            continue
        cid = r["candidate_id"]
        r["significance"] = (SURVIVES_CORRECTED if cid in survivors
                             else UNCORRECTED_ONLY)
        move = registry.set_status(cid, SHADOW)
        r["shadow_admitted"] = bool(move.get("ok"))
        promoted.append(r)
        store.append(SCREEN_FILE, r)

    return {
        "screened": len(results),
        "remeasured": len(remeasured),
        "hypotheses_counted": bh["m"],
        "fdr": bh,
        "eligible_for_shadow": len(promoted),
        "survives_correction": sum(
            1 for r in promoted if r["significance"] == SURVIVES_CORRECTED),
        "uncorrected_only": sum(
            1 for r in promoted if r["significance"] == UNCORRECTED_ONLY),
        "historically_rejected": len(rejected),
        "not_screenable": len(unmeasurable),
        "absences": _absence_counts(unmeasurable),
        "kill_reasons": _kill_counts(rejected),
        "look_ahead_audit": la,
        "bar_stride": stride,
        "standing_limit": (
            "every figure here is modelled on candle data with no book. "
            "Nothing screened can be promoted; the best outcome is admission "
            "to live shadow observation, where quotes are real."
        ),
    }


def _absence_counts(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        key = str(r.get("absent"))
        out[key] = out.get(key, 0) + 1
    return out


def _kill_counts(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        key = str(r.get("kill_reason"))
        out[key] = out.get(key, 0) + 1
    return out


def results() -> list[dict]:
    return store.read(SCREEN_FILE)
