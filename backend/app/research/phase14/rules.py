"""Grade the Research tab's own proposals against multi-year history.

The Research tab produces statements of the form "trades where <condition> held
won more often than trades where it did not" -- pullback entries, healthy
premium, trending regime, trade score >= 80 -- and those statements have been the
basis for production gates. Two things are wrong with using them that way:

* they are **observational and unsplit**. The rule is measured on the same
  sessions that suggested it, so a rule that merely describes a good week is
  indistinguishable from a rule that works;
* they are measured on a **handful of trades**. A single session records tens of
  signals, so any conditional win rate cut from it is mostly sampling noise.

This module re-asks each of those proposals the only way that can answer them:
replay the engine over years of history, split chronologically, and compare the
trades where the condition held against the trades where it did not -- the
*lift* -- separately in the early period and in the later holdout. A proposal is
only ``CONFIRMED`` if it lifted expectancy in the early period AND still lifted
it in the holdout AND the trades it keeps are actually profitable out of sample.

The distinction that matters most here: a rule can raise win rate and still lose
money, and a rule can look excellent in-sample and be worthless out of it. Both
are reported rather than hidden, and a proposal that cannot be tested on
underlying candles at all (anything needing the option chain: premium health,
spread, OI, IV) is reported as ``UNTESTABLE_ON_HISTORY`` instead of being quietly
dropped or, worse, approximated.
"""
from __future__ import annotations

import math
from collections.abc import Callable

# Trades each arm of a split needs, in each period, before the lift is quoted.
# A rule is compared four ways (on/off x early/late), so a thin arm anywhere
# makes the whole comparison meaningless.
MIN_ARM_TRADES = 100

# Expectancy the kept trades must clear in holdout. Zero is not enough: the live
# trade pays premium and spread, and the recorded book loses roughly 0.05R-0.1R
# of underlying edge to that before anything else happens.
MIN_HOLDOUT_EXPECTANCY = 0.05

# Lift smaller than this is treated as no effect regardless of sign, so a rule
# is not promoted on a third of a tick per trade.
MIN_LIFT_R = 0.02

CONFIRMED = "CONFIRMED"
REJECTED_OUT_OF_SAMPLE = "REJECTED_OUT_OF_SAMPLE"
NO_EFFECT = "NO_EFFECT"
HARMFUL = "HARMFUL"
NOT_ENOUGH_DATA = "NOT_ENOUGH_DATA"
NOT_SELECTIVE = "NOT_SELECTIVE"
UNTESTABLE = "UNTESTABLE_ON_HISTORY"

# Anything needing the option chain. History has underlying candles only, so
# these proposals are named and left ungraded rather than approximated from spot.
UNTESTABLE_RULES: dict[str, str] = {
    "healthy_premium": (
        "needs the option's own premium path; the chain is not in history"),
    "spread_quality": (
        "needs historical bid/ask; Angel's history endpoint returns OHLCV only"),
    "liquidity_oi": (
        "needs historical OI/volume per strike, which expired contracts no "
        "longer expose"),
    "smart_money_aligned": (
        "the engine derives smart money from per-strike OI CHANGE "
        "(estimated_smart_money(chain, ...)); with no chain in history the "
        "field is never populated, so an empty arm here means UNTESTABLE, "
        "not that the live filter never fires"),
}


def _pullback(t: dict) -> bool:
    return "PULLBACK" in (t.get("entry_trigger") or "").upper()


def _with_htf(t: dict) -> bool:
    trend = (t.get("htf_trend") or "").upper()
    if trend in ("UP", "CE", "LONG"):
        return t["side"] == "LONG"
    if trend in ("DOWN", "PE", "SHORT"):
        return t["side"] == "SHORT"
    return False


# The engine's conviction reading. Pools saved before it was relabelled carry
# the same number under the old ``win_probability`` key, so both are read and an
# existing pool file does not have to be regenerated to be graded.
CONVICTION = "conviction_meter"
CONVICTION_LEGACY = "win_probability"


def _value(t: dict, field: str) -> float | None:
    v = t.get(field)
    if v is None and field == CONVICTION:
        v = t.get(CONVICTION_LEGACY)
    return None if v is None else float(v)


def _at_least(field: str, floor: float) -> Callable[[dict], bool]:
    def pred(t: dict) -> bool:
        v = _value(t, field)
        return v is not None and v >= floor
    return pred


def _below(field: str, ceiling: float) -> Callable[[dict], bool]:
    def pred(t: dict) -> bool:
        v = _value(t, field)
        return v is not None and v < ceiling
    return pred


# Every proposal the Research tab makes that underlying history CAN grade, stated
# as the gate the live engine would apply. The label is written as the production
# rule, not as the statistic, so a CONFIRMED row is directly implementable.
TESTABLE_RULES: dict[str, Callable[[dict], bool]] = {
    "pullback_entry": _pullback,
    "trending_or_breakout_regime": (
        lambda t: (t.get("regime") or "").upper() in ("TRENDING", "BREAKOUT")),
    "trade_score_ge_80": _at_least("trade_score", 80.0),
    "trade_score_ge_60": _at_least("trade_score", 60.0),
    "confidence_ge_70": _at_least("confidence", 70.0),
    "opportunity_ge_60": _at_least("opportunity_score", 60.0),
    "conviction_ge_60": _at_least(CONVICTION, 60.0),
    "risk_score_lt_50": _below("risk_score", 50.0),
    "with_htf_trend": _with_htf,
    "htf_strength_ge_60": _at_least("htf_strength", 60.0),
    "reward_risk_ge_2": _at_least("reward_risk", 2.0),
    "no_trap_signal": _below("trap_prob", 30.0),
    "no_fake_breakout": _below("fake_prob", 30.0),
    # Not a Research-tab proposal but the one Phase 14 itself raised: a stop
    # inside a single candle's normal range is resolved by noise, not direction.
    "stop_wider_than_one_candle": _at_least("risk_over_noise", 1.0),
    "skip_first_15_minutes": _at_least("entry_minute_ist", 15.0),
}


# Scores the Research tab cuts its proposals on. If one of these is saturated --
# nearly every trade scoring the same -- then "trades scoring >= 80 did better"
# was measured against a nearly empty comparison arm, and the gate cannot
# select anything in production either.
SCORE_FIELDS = ("trade_score", "confidence", "opportunity_score", "risk_score",
                CONVICTION, "htf_strength", "trap_prob", "fake_prob")

# A gate keeping more than this share of the book is not selecting; one keeping
# less than the floor cannot carry the day's trading even if it is real.
SATURATED_PCT = 95.0
NEGLIGIBLE_PCT = 5.0


def saturation(trades: list[dict]) -> list[dict]:
    """How much spread each score actually has across the replayed book."""
    out = []
    for field in SCORE_FIELDS:
        vals = [v for v in (_value(t, field) for t in trades) if v is not None]
        if not vals:
            out.append({"score": field, "recorded": 0,
                        "note": "never recorded on a replayed trade"})
            continue
        buckets: dict[str, int] = {}
        for v in vals:
            lo = min(80, int(v // 20) * 20)
            buckets[f"{lo}-{lo + 19}"] = buckets.get(f"{lo}-{lo + 19}", 0) + 1
        top = max(buckets.items(), key=lambda kv: kv[1])
        out.append({
            "score": field,
            "recorded": len(vals),
            "distinct_values": len({round(v, 1) for v in vals}),
            "min": round(min(vals), 1),
            "median": round(sorted(vals)[len(vals) // 2], 1),
            "max": round(max(vals), 1),
            "buckets": dict(sorted(buckets.items())),
            "largest_bucket_pct": round(100.0 * top[1] / len(vals), 1),
            "saturated": bool(100.0 * top[1] / len(vals) >= SATURATED_PCT),
        })
    return out


# --- the A+ frontier -------------------------------------------------------
# The objective is not "a rule with a positive lift" but a SELECTIVE system: few
# trades, a high share of them reaching target before stop, still positive after
# the spread, and holding up chronologically. Those pull against each other --
# every condition added raises the hit rate and shrinks the sample, until the
# result is six trades and no evidence. The frontier makes that trade-off
# explicit instead of letting a stack of filters be tuned until it looks good.

# Combinations are searched up to this width. Deliberately small: each extra
# condition multiplies the number of comparisons, and with ~16 rules a width of 3
# is already ~700 combinations, of which ~35 look good at p<0.05 by chance alone.
MAX_COMBO = 3

# A subset must keep at least this many candidates per period, and produce at
# least this many trades per day, or it is recorded as too thin to trade rather
# than as a discovery.
MIN_SUBSET_TRADES = 150
MIN_TRADES_PER_DAY = 0.5

# Target-before-stop rate that counts as an A+ candidate. Below this the vehicle
# cannot absorb the spread; far above it, check the sample before believing it.
TARGET_T1_RATE = 60.0


def t1_rate(trades: list[dict]) -> dict:
    """Share of candidates that reached target BEFORE stop.

    This is the metric the A+ objective is actually stated in, and it is NOT the
    win rate: a candidate can end positive by timing out above entry without ever
    touching target, and on a 1-minute book many do. Counting those as T1 would
    overstate the hit rate of exactly the subset being selected for.
    """
    n = len(trades)
    if not n:
        return {"trades": 0, "t1": 0, "t1_pct": 0.0, "sl": 0, "sl_pct": 0.0,
                "neither_pct": 0.0}
    t1 = sum(1 for t in trades if t.get("exit_reason") == "TARGET")
    sl = sum(1 for t in trades if t.get("exit_reason") == "STOP")
    return {
        "trades": n,
        "t1": t1,
        "t1_pct": round(100.0 * t1 / n, 1),
        "sl": sl,
        "sl_pct": round(100.0 * sl / n, 1),
        # Timed out or flattened at the close: neither target nor stop. A subset
        # that is mostly this is not "selective", it is undecided.
        "neither_pct": round(100.0 * (n - t1 - sl) / n, 1),
    }


def _sessions(trades: list[dict]) -> int:
    """Distinct IST sessions in a book, for a per-day frequency.

    Counted from the ``session`` the replay already stamped on each row rather
    than re-derived from the timestamp, so this cannot drift from the session
    boundary the replay used to flatten trades.
    """
    return len({t["session"] for t in trades if t.get("session")})


def frontier(in_sample: list[dict], holdout: list[dict], *,
             max_combo: int = MAX_COMBO,
             min_subset_trades: int = MIN_SUBSET_TRADES,
             min_trades_per_day: float = MIN_TRADES_PER_DAY,
             rules_: dict[str, Callable[[dict], bool]] | None = None) -> dict:
    """Search condition combinations for the best OOS target-before-stop rate.

    Reports, for every combination that stays above the sample floors, its
    holdout T1 rate, expectancy and trades/day -- and marks a combination as a
    candidate only when the holdout T1 rate holds up against the in-sample one.
    A stack of filters whose T1 rate only appears in-sample is the exact failure
    this exists to expose, so both periods are always shown side by side.

    The count of combinations searched is returned with the results because it is
    the only honest way to read them: at 700 comparisons, the best-looking subset
    is expected to look good by chance, and a frontier without that number
    attached is a machine for manufacturing A+ setups that do not exist.
    """
    rules_ = rules_ or TESTABLE_RULES
    # Only conditions that actually split the book are worth combining: an inert
    # one adds a comparison without changing the subset.
    usable = []
    for name, pred in rules_.items():
        kept = sum(1 for t in holdout if pred(t))
        share = (100.0 * kept / len(holdout)) if holdout else 0.0
        if NEGLIGIBLE_PCT <= share <= SATURATED_PCT:
            usable.append(name)

    combos: list[tuple[str, ...]] = []
    for width in range(1, max_combo + 1):
        combos.extend(_combinations(usable, width))

    rows = []
    for combo in combos:
        preds = [rules_[n] for n in combo]

        def keep(t: dict, _p: list = preds) -> bool:
            return all(p(t) for p in _p)

        a = [t for t in in_sample if keep(t)]
        b = [t for t in holdout if keep(t)]
        if len(a) < min_subset_trades or len(b) < min_subset_trades:
            continue
        b_days = max(1, _sessions(b))
        per_day = round(len(b) / b_days, 2)
        a_t1, b_t1 = t1_rate(a), t1_rate(b)
        a_arm, b_arm = _arm(a), _arm(b)
        rows.append({
            "conditions": list(combo),
            "in_sample": {**a_t1, "expectancy_r": a_arm["expectancy_r"]},
            "holdout": {**b_t1, "expectancy_r": b_arm["expectancy_r"],
                        "profit_factor": b_arm["profit_factor"]},
            "holdout_trades_per_day": per_day,
            "keeps_pct_of_holdout": round(100.0 * len(b) / len(holdout), 1)
            if holdout else 0.0,
            # Both periods must clear the bar, and the holdout must not be
            # materially worse than the in-sample -- a T1 rate that decays out of
            # sample is the signature of an overfitted filter stack.
            "holds_up": bool(b_t1["t1_pct"] >= TARGET_T1_RATE
                             and a_t1["t1_pct"] >= TARGET_T1_RATE
                             and b_t1["t1_pct"] >= a_t1["t1_pct"] - 5.0),
            "tradable_frequency": bool(per_day >= min_trades_per_day),
        })

    rows.sort(key=lambda r: (-r["holdout"]["t1_pct"], -r["holdout_trades_per_day"]))
    survivors = [r for r in rows
                 if r["holds_up"] and r["tradable_frequency"]
                 and r["holdout"]["expectancy_r"] > 0]
    best = rows[0] if rows else None
    return {
        "combinations_searched": len(combos),
        "combinations_with_enough_sample": len(rows),
        # 5% of the searched combinations will clear any given bar by chance. A
        # survivor count at or below this is not a finding.
        "expected_false_positives": round(0.05 * len(combos), 1),
        "conditions_used": usable,
        "min_subset_trades": min_subset_trades,
        "target_t1_pct": TARGET_T1_RATE,
        "best_by_holdout_t1": best,
        "survivors": survivors[:20],
        "rows": rows[:60],
        "verdict": _frontier_verdict(rows, survivors, len(combos)),
    }


def _frontier_shortfall(row: dict) -> str:
    """Which A+ requirement this subset missed, in the order that matters."""
    a_t1 = row["in_sample"]["t1_pct"]
    b_t1 = row["holdout"]["t1_pct"]
    if b_t1 < TARGET_T1_RATE:
        return f"below the {TARGET_T1_RATE}% target-before-stop bar out of sample"
    if a_t1 < TARGET_T1_RATE:
        return (f"its in-sample T1 was only {a_t1}%, so the out-of-sample "
                "figure is the luckier of the two periods rather than a "
                "repeatable rate")
    if b_t1 < a_t1 - 5.0:
        return (f"its T1 decayed from {a_t1}% in-sample, the signature of an "
                "overfitted filter stack")
    if not row["tradable_frequency"]:
        return "too few trades per day to carry a session"
    if row["holdout"]["expectancy_r"] <= 0:
        return "a positive T1 rate but non-positive expectancy — the losers are "\
               "bigger than the winners"
    return "it missed one of the sample or frequency floors"


def _frontier_verdict(rows: list[dict], survivors: list[dict],
                      searched: int) -> str:
    if not rows:
        return ("no condition combination kept enough candidates in both periods "
                "to be measured — the A+ subset cannot be located on this sample, "
                "which is a statement about the data, not proof that none exists")
    best = rows[0]
    if not survivors:
        # Name the requirement the best row actually failed. Saying "none reached
        # the target rate" while quoting a row above that rate reads as a
        # contradiction and invites someone to trust the headline number.
        return (
            f"searched {searched} combinations; none cleared every bar at once. "
            f"The best out-of-sample T1 was {', '.join(best['conditions'])} at "
            f"{best['holdout']['t1_pct']}% "
            f"({best['holdout_trades_per_day']}/day, "
            f"{best['holdout']['expectancy_r']:+}R) — {_frontier_shortfall(best)}. "
            "Selectivity alone did not produce an A+ subset, so the fix is not "
            "another filter")
    chance = round(0.05 * searched, 1)
    lead = survivors[0]
    honest = (" — but that is at or below the number expected by chance at this "
              f"many comparisons ({chance}), so it is a hypothesis to re-test, "
              "not a result") if len(survivors) <= chance else (
        " — each still has to survive the full history and a second instrument "
        "before it may gate production")
    return (
        f"{len(survivors)} of {searched} combinations held a "
        f"{TARGET_T1_RATE}%+ target-before-stop rate in BOTH periods while "
        f"staying tradable; best is {', '.join(lead['conditions'])} at "
        f"{lead['holdout']['t1_pct']}% T1, {lead['holdout_trades_per_day']}/day, "
        f"{lead['holdout']['expectancy_r']:+}R gross{honest}")


def _combinations(items: list[str], width: int) -> list[tuple[str, ...]]:
    if width == 0:
        return [()]
    out: list[tuple[str, ...]] = []
    for i, head in enumerate(items):
        for rest in _combinations(items[i + 1:], width - 1):
            out.append((head, *rest))
    return out


def refusals(trades: list[dict]) -> dict | None:
    """What the engine's own BUY/WAIT/AVOID decision was worth, per signal.

    This is the question the daily loop cannot ask: it only records what was
    taken, so a refusal is free by construction. On the candidate pool the
    refused bars have outcomes too, so the engine's selectivity can finally be
    priced — if WAIT and AVOID bars did as well as BUY bars, the decision is not
    selecting anything and the scores in front of it are decoration.
    """
    if not any("taken" in t for t in trades):
        return None
    taken = _arm([t for t in trades if t.get("taken")])
    refused = _arm([t for t in trades if t.get("taken") is False])
    by_signal = {}
    for sig in sorted({str(t.get("signal")) for t in trades if t.get("signal")}):
        by_signal[sig] = _arm([t for t in trades if t.get("signal") == sig])
    lift = _lift(taken, refused)
    return {
        "taken": taken,
        "refused": refused,
        "by_signal": by_signal,
        **lift,
        "selectivity": (
            "the engine's BUY bars did no better than the bars it refused — its "
            "decision is not selecting, so gates layered on top of it cannot fix "
            "the problem"
            if not lift["beats_noise"] or lift["lift_r"] <= 0 else
            f"the engine's BUY bars beat the bars it refused by "
            f"{lift['lift_r']:+}R per trade ({lift['sigma']} sigma) — the "
            f"decision itself carries the edge"),
    }


def _arm(trades: list[dict]) -> dict:
    rs = [float(t["r"]) for t in trades]
    n = len(rs)
    if n == 0:
        return {"trades": 0, "expectancy_r": 0.0, "win_rate": 0.0,
                "profit_factor": None, "net_r": 0.0, "std_r": 0.0}
    mean = sum(rs) / n
    var = sum((r - mean) ** 2 for r in rs) / n
    gain = sum(r for r in rs if r > 0)
    loss = -sum(r for r in rs if r < 0)
    return {
        "trades": n,
        "expectancy_r": round(mean, 3),
        "win_rate": round(100.0 * sum(1 for r in rs if r > 0) / n, 1),
        "profit_factor": round(gain / loss, 2) if loss > 0 else None,
        "net_r": round(sum(rs), 2),
        "std_r": round(math.sqrt(var), 3),
    }


def _lift(on: dict, off: dict) -> dict:
    """Difference in expectancy between the kept and the rejected trades.

    The standard error is reported with it because a 0.03R lift measured on two
    noisy arms is not a finding, and quoting the difference alone invites reading
    it as one.
    """
    diff = on["expectancy_r"] - off["expectancy_r"]
    se = 0.0
    if on["trades"] and off["trades"]:
        se = math.sqrt(on["std_r"] ** 2 / on["trades"]
                       + off["std_r"] ** 2 / off["trades"])
    return {
        "lift_r": round(diff, 3),
        "std_error_r": round(se, 3),
        # How many standard errors the lift is from zero. Below ~2 the honest
        # reading is "indistinguishable from chance at this sample size".
        "sigma": round(diff / se, 2) if se > 0 else None,
        "beats_noise": bool(se > 0 and abs(diff) >= 2 * se),
    }


def grade(name: str, predicate: Callable[[dict], bool],
          in_sample: list[dict], holdout: list[dict], *,
          min_arm_trades: int = MIN_ARM_TRADES,
          min_holdout_expectancy: float = MIN_HOLDOUT_EXPECTANCY,
          min_lift_r: float = MIN_LIFT_R) -> dict:
    """Grade one proposal on both periods and return its verdict."""
    early_on = [t for t in in_sample if predicate(t)]
    early_off = [t for t in in_sample if not predicate(t)]
    late_on = [t for t in holdout if predicate(t)]
    late_off = [t for t in holdout if not predicate(t)]

    a_on, a_off = _arm(early_on), _arm(early_off)
    b_on, b_off = _arm(late_on), _arm(late_off)
    a_lift, b_lift = _lift(a_on, a_off), _lift(b_on, b_off)

    enough = all(arm["trades"] >= min_arm_trades
                 for arm in (a_on, a_off, b_on, b_off))
    share = (100.0 * b_on["trades"] / len(holdout)) if holdout else 0.0
    if not enough:
        # A saturated score is a distinct failure from a thin sample: the rule is
        # not untested, it is inert, and saying so is the actual finding about
        # the Research tab's score gates.
        verdict = (NOT_SELECTIVE if share >= SATURATED_PCT and holdout
                   else NOT_ENOUGH_DATA)
    elif b_lift["lift_r"] <= -min_lift_r and b_lift["beats_noise"]:
        # It reliably kept the worse trades out of sample; that is a finding too.
        # The noise test is required in BOTH directions: on random data a third of
        # the rules drift to a negative lift, and calling those harmful would send
        # someone to rip working gates out of production over a coin flip.
        verdict = HARMFUL
    elif (a_lift["lift_r"] >= min_lift_r
          and b_lift["lift_r"] >= min_lift_r
          and b_lift["beats_noise"]
          and b_on["expectancy_r"] >= min_holdout_expectancy):
        verdict = CONFIRMED
    elif a_lift["lift_r"] >= min_lift_r and a_lift["beats_noise"]:
        verdict = REJECTED_OUT_OF_SAMPLE
    else:
        verdict = NO_EFFECT

    keeps = (round(100.0 * b_on["trades"] / len(holdout), 1) if holdout else 0.0)
    return {
        "rule": name,
        "verdict": verdict,
        "in_sample": {"kept": a_on, "rejected": a_off, **a_lift},
        "holdout": {"kept": b_on, "rejected": b_off, **b_lift},
        "keeps_pct_of_holdout": keeps,
        # A gate that keeps ~everything is not a filter whatever its lift says,
        # and one that keeps almost nothing cannot carry a trading day. Both are
        # stated here so a real-but-useless rule is not read as a fix.
        "selective": bool(NEGLIGIBLE_PCT <= keeps <= SATURATED_PCT),
        "why": _why(verdict, a_lift, b_lift, b_on, min_holdout_expectancy),
    }


def _why(verdict: str, a_lift: dict, b_lift: dict, b_on: dict,
         min_holdout_expectancy: float) -> str:
    if verdict == NOT_ENOUGH_DATA:
        return ("fewer than the minimum trades in one arm of one period — the "
                "comparison cannot be made, not 'the rule does nothing'")
    if verdict == HARMFUL:
        return (f"in holdout the trades it keeps did {abs(b_lift['lift_r'])}R "
                f"per trade WORSE than the ones it rejects "
                f"({b_lift['sigma']} sigma, beyond noise) — applying it would "
                "make production worse")
    if verdict == CONFIRMED:
        return (f"lifted expectancy in both periods ({a_lift['lift_r']:+}R then "
                f"{b_lift['lift_r']:+}R at {b_lift['sigma']} sigma) and the "
                f"trades it keeps are {b_on['expectancy_r']:+}R out of sample")
    if verdict == NOT_SELECTIVE:
        return ("the condition held on essentially the whole book, so the "
                "comparison arm is nearly empty and the gate would reject "
                "almost nothing in production either")
    if verdict == REJECTED_OUT_OF_SAMPLE:
        reason = (f"holdout lift {b_lift['lift_r']:+}R"
                  if b_lift["lift_r"] < MIN_LIFT_R else
                  f"kept trades only {b_on['expectancy_r']:+}R vs the "
                  f"{min_holdout_expectancy:+}R a spread-paying trade needs")
        return (f"looked good early ({a_lift['lift_r']:+}R lift) and did not "
                f"hold up: {reason}")
    if b_lift["lift_r"] <= -MIN_LIFT_R:
        return (f"kept the worse trades in holdout ({b_lift['lift_r']:+}R) but "
                f"only at {b_lift['sigma']} sigma — within noise at this sample "
                "size, so not evidence to remove it either")
    return ("no meaningful difference between the trades it keeps and the ones "
            "it rejects, in either period")


def study(in_sample: list[dict], holdout: list[dict], *,
          min_arm_trades: int = MIN_ARM_TRADES,
          min_holdout_expectancy: float = MIN_HOLDOUT_EXPECTANCY,
          min_lift_r: float = MIN_LIFT_R,
          max_combo: int = MAX_COMBO) -> dict:
    """Grade every Research-tab proposal and summarise what survived."""
    graded = [
        grade(name, pred, in_sample, holdout,
              min_arm_trades=min_arm_trades,
              min_holdout_expectancy=min_holdout_expectancy,
              min_lift_r=min_lift_r)
        for name, pred in TESTABLE_RULES.items()
    ]
    graded.sort(key=lambda r: r["holdout"]["lift_r"], reverse=True)

    by_verdict: dict[str, list[str]] = {}
    for row in graded:
        by_verdict.setdefault(row["verdict"], []).append(row["rule"])

    comparable = sum(1 for r in graded
                     if r["verdict"] not in (NOT_ENOUGH_DATA, NOT_SELECTIVE))
    confirmed = by_verdict.get(CONFIRMED, [])
    # With this many rules compared at a 5% false-positive rate, this many are
    # expected to clear the bar on luck alone. Confirmed at or below that number
    # is not evidence of anything.
    by_chance = round(0.05 * comparable, 1)

    return {
        "in_sample_trades": len(in_sample),
        "holdout_trades": len(holdout),
        "min_arm_trades": min_arm_trades,
        "min_holdout_expectancy": min_holdout_expectancy,
        "min_lift_r": min_lift_r,
        "rules_compared": comparable,
        "expected_false_positives": by_chance,
        "by_verdict": by_verdict,
        "rules": graded,
        "untestable": [{"rule": k, "reason": v}
                       for k, v in sorted(UNTESTABLE_RULES.items())],
        # Reported next to the verdicts because a saturated score explains an
        # inert gate far better than its lift number does.
        "score_spread": saturation(in_sample + holdout),
        # Only present when graded on the candidate pool; the taken book has no
        # refused arm to price, which is the whole reason the pool exists.
        "engine_selectivity": refusals(holdout),
        # The A+ objective restated as a measurement: how high can the
        # target-before-stop rate be pushed by selection before the sample (and
        # so the evidence) collapses?
        "t1_baseline": {"in_sample": t1_rate(in_sample),
                        "holdout": t1_rate(holdout)},
        "frontier": frontier(in_sample, holdout, max_combo=max_combo),
        "verdict": _headline(confirmed, by_verdict, by_chance),
        "note": ("Every rule is graded on underlying R, gross of premium and "
                 "spread. A CONFIRMED rule has been shown to improve DIRECTION "
                 "out of sample; whether the option it would buy is tradable is "
                 "a separate question that only live chain capture can answer."),
    }


def _headline(confirmed: list[str], by_verdict: dict[str, list[str]],
              by_chance: float) -> str:
    harmful = by_verdict.get(HARMFUL, [])
    inert = by_verdict.get(NOT_SELECTIVE, [])
    if not confirmed:
        base = ("no Research-tab proposal improved expectancy out of sample — "
                "the tab's conditional win rates are not evidence for a gate")
    elif len(confirmed) <= by_chance:
        base = (f"{len(confirmed)} rule(s) cleared the bar against ~{by_chance} "
                "expected by chance — that is not a finding, confirm on other "
                "instruments before believing it")
    else:
        base = (f"{len(confirmed)} rule(s) held up out of sample: "
                f"{', '.join(confirmed)} — implementable as gates after "
                "confirmation on a second instrument")
    if harmful:
        base += (f"; {len(harmful)} rule(s) are actively harmful and should be "
                 f"removed if in production: {', '.join(harmful)}")
    if inert:
        base += (f"; {len(inert)} gate(s) hold on ~the whole book and select "
                 f"nothing: {', '.join(inert)}")
    return base
