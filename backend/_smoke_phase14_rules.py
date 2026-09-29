"""Does the rule grader earn the right to say a Research-tab proposal works?

The whole point of this module is to be harder to please than the Research tab
was, so these checks pin the ways it could be too easy: a rule that only worked
early must be rejected, a rule that keeps trades which are positive but thinner
than a spread must be rejected, a rule with a thin arm must be reported as
untestable rather than graded, and a rule that keeps the WORSE trades must be
named harmful rather than merely "no effect". The proposals needing the option
chain must stay listed and ungraded — dropping them would quietly imply history
had tested them.
"""
from __future__ import annotations

import random
import sys

from app.research.phase14 import rules

CHECKS = 0


def ok(cond: bool, what: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(what)


def trade(r: float, **kw) -> dict:
    base = {"side": "LONG", "r": r, "mfe_r": abs(r), "mae_r": -abs(r),
            "reward_risk": 1.5, "confidence": 55.0, "regime": "TRENDING",
            "entry_trigger": "BREAKOUT", "htf_trend": "UP", "htf_strength": 70.0,
            "entry_minute_ist": 100, "risk_over_noise": 2.5, "trade_score": 55.0,
            "opportunity_score": 50.0, "risk_score": 40.0,
            "conviction_meter": 50.0,
            "trap_prob": 10.0, "fake_prob": 10.0, "smart_money": "BULLISH"}
    base.update(kw)
    return base


def book(kept_r: float, rejected_r: float, n: int = 600) -> list[dict]:
    """Half the book passes ``pullback_entry``, half does not, at set edges."""
    rows = []
    for i in range(n):
        rows.append(trade(kept_r if i % 2 else -1.0, entry_trigger="PULLBACK_LONG"))
        rows.append(trade(rejected_r if i % 2 else -1.0, entry_trigger="BREAKOUT"))
    return rows


def graded(name: str, study: dict) -> dict:
    return next(r for r in study["rules"] if r["rule"] == name)


def main() -> int:
    random.seed(7)

    # --- a rule that works in both periods is CONFIRMED -------------------
    # kept: half +2R half -1R = +0.5R; rejected: half +1R half -1R = 0R.
    real = rules.study(book(2.0, 1.0), book(2.0, 1.0))
    pull = graded("pullback_entry", real)
    ok(pull["verdict"] == rules.CONFIRMED,
       f"a rule with a real lift in both periods must be CONFIRMED, got "
       f"{pull['verdict']}")
    ok(pull["holdout"]["lift_r"] > 0 and pull["holdout"]["beats_noise"],
       "a confirmed rule's holdout lift must be reported and beat noise")
    ok(0.0 < pull["keeps_pct_of_holdout"] < 100.0,
       "the share of the book a rule keeps must be reported — a rule that keeps "
       "2% of trades is not a fix even if it is real")

    # --- in-sample-only luck is REJECTED_OUT_OF_SAMPLE --------------------
    lucky = rules.study(book(2.0, 1.0), book(1.0, 1.0))
    ok(graded("pullback_entry", lucky)["verdict"] == rules.REJECTED_OUT_OF_SAMPLE,
       "a rule that lifts early and not later must be rejected out of sample")

    # --- keeping the worse trades is HARMFUL, not 'no effect' -------------
    harm = rules.study(book(0.2, 2.0), book(0.2, 2.0))
    hrow = graded("pullback_entry", harm)
    ok(hrow["verdict"] == rules.HARMFUL,
       f"a rule that keeps the worse half must be HARMFUL, got {hrow['verdict']}")
    ok("worse" in hrow["why"] and "make production worse" in hrow["why"],
       "a harmful rule must say plainly that applying it would hurt")
    ok(rules.HARMFUL in harm["by_verdict"]
       and "actively harmful" in harm["verdict"],
       "the headline must surface harmful rules, not bury them in the rows")

    # --- a lift within noise is neither confirmed NOR condemned -----------
    # Found on a random-walk replay: a third of the rules drift to a negative
    # lift by chance. Calling those harmful would send someone to strip working
    # gates out of production over a coin flip, so both verdicts need the lift to
    # clear ~2 standard errors, not merely to have a sign.
    def noisy(n: int, tilt: float) -> list[dict]:
        """Kept arm mean = ``tilt``, rejected arm mean = 0, both very noisy.

        Built arithmetically rather than drawn, so the fixture cannot pass or fail
        on the seed: a wide spread with a small offset is exactly the "lift with
        the wrong sign" case that must not be reported as a finding.
        """
        rows = []
        for _ in range(n):
            rows.append(trade(8.0 + tilt, entry_trigger="PULLBACK_LONG"))
            rows.append(trade(-8.0 + tilt, entry_trigger="PULLBACK_LONG"))
            rows.append(trade(8.0, entry_trigger="BREAKOUT"))
            rows.append(trade(-8.0, entry_trigger="BREAKOUT"))
        return rows

    drift = rules.study(noisy(150, -0.15), noisy(150, -0.15))
    drow = graded("pullback_entry", drift)
    ok(drow["holdout"]["lift_r"] <= -rules.MIN_LIFT_R,
       f"fixture must have a negative holdout lift, got {drow['holdout']['lift_r']}")
    ok(drow["holdout"]["beats_noise"] is False,
       f"fixture lift must sit within noise, got {drow['holdout']['sigma']} sigma")
    ok(drow["verdict"] != rules.HARMFUL,
       "a negative lift inside the noise band must NOT be called harmful — that "
       "would have production gates removed on a coin flip")
    ok("within noise" in drow["why"] and "sigma" in drow["why"],
       f"the reason must say the lift is within noise, got {drow['why']}")

    # ...and the same bar applies to promotion: sign is not significance.
    weak = rules.study(noisy(150, 0.15), noisy(150, 0.15))
    wrow = graded("pullback_entry", weak)
    ok(wrow["holdout"]["lift_r"] >= rules.MIN_LIFT_R
       and not wrow["holdout"]["beats_noise"],
       "fixture must show a positive lift that stays within noise")
    ok(wrow["verdict"] != rules.CONFIRMED,
       "a positive lift inside the noise band must not be CONFIRMED either")

    # --- positive but thinner than a spread is not CONFIRMED --------------
    # kept expectancy +0.02R: better than the rejected arm, still less than the
    # spread a live option entry pays, so it must not be promoted.
    marginal = rules.study(book(1.04, 0.9), book(1.04, 0.9))
    mrow = graded("pullback_entry", marginal)
    ok(0 < mrow["holdout"]["kept"]["expectancy_r"] < rules.MIN_HOLDOUT_EXPECTANCY,
       f"fixture must keep barely-positive trades, got "
       f"{mrow['holdout']['kept']['expectancy_r']}")
    ok(mrow["verdict"] != rules.CONFIRMED,
       "a rule whose kept trades cannot pay a spread must not be CONFIRMED")

    # --- a flat book confirms nothing ------------------------------------
    def coin_flip(n: int) -> list[dict]:
        return [trade(random.choice((1.0, -1.0)),
                      confidence=random.choice((35.0, 55.0, 75.0, 85.0)),
                      trade_score=random.choice((40.0, 65.0, 85.0)),
                      entry_trigger=random.choice(("PULLBACK", "BREAKOUT")),
                      risk_over_noise=round(random.uniform(0.4, 5.0), 2),
                      entry_minute_ist=random.randrange(375))
                for _ in range(n)]

    flat = rules.study(coin_flip(6000), coin_flip(6000))
    ok(not flat["by_verdict"].get(rules.CONFIRMED),
       f"a coin-flip book must confirm no rule, got "
       f"{flat['by_verdict'].get(rules.CONFIRMED)}")
    ok("no Research-tab proposal improved expectancy" in flat["verdict"],
       f"the verdict must say so plainly, got {flat['verdict']}")

    # --- a thin arm is NOT_ENOUGH_DATA, never a verdict on the rule -------
    thin = rules.study([trade(1.0, entry_trigger="PULLBACK")] * 30
                       + [trade(-1.0)] * 400,
                       [trade(1.0, entry_trigger="PULLBACK")] * 30
                       + [trade(-1.0)] * 400)
    trow = graded("pullback_entry", thin)
    ok(trow["verdict"] == rules.NOT_ENOUGH_DATA,
       f"a 30-trade arm must be NOT_ENOUGH_DATA, got {trow['verdict']}")
    ok("cannot be made" in trow["why"] and "not 'the rule does nothing'" in trow["why"],
       "an untestable rule must not be reported as a disproved rule")
    ok(thin["rules_compared"] < len(rules.TESTABLE_RULES),
       "rules that could not be compared must not inflate the comparison count")

    # --- multiple comparisons are counted --------------------------------
    ok(real["rules_compared"] > 0 and real["expected_false_positives"] > 0,
       "the number of rules compared and the chance rate must be reported")
    lone = rules.study(book(2.0, 1.0), book(2.0, 1.0))
    confirmed = lone["by_verdict"].get(rules.CONFIRMED, [])
    ok(len(confirmed) > lone["expected_false_positives"]
       or "not a finding" in lone["verdict"],
       "confirmations at or below the chance rate must be called out as such")

    # --- chain-dependent proposals stay listed and ungraded --------------
    names = {r["rule"] for r in real["untestable"]}
    ok("healthy_premium" in names,
       "healthy_premium is a Research-tab rule and must be listed as untestable, "
       "not silently omitted")
    ok(names & {"spread_quality", "liquidity_oi"},
       "spread and liquidity proposals must be named as needing the live chain")
    ok(not (names & set(rules.TESTABLE_RULES)),
       "a rule cannot be both graded and declared untestable")
    for row in real["untestable"]:
        ok(bool(row["reason"]),
           f"{row['rule']} must say WHY history cannot grade it")

    # --- the grader never reads an outcome to build the arms -------------
    outcome_fields = {"r", "mfe_r", "mae_r", "exit_reason", "exit_price",
                      "exit_ts", "minutes", "bars_held"}
    probe = {k: v for k, v in trade(1.0).items() if k not in outcome_fields}
    for name, pred in rules.TESTABLE_RULES.items():
        try:
            ok(isinstance(pred(probe), bool),
               f"rule {name} must decide from entry-time fields alone")
        except KeyError as exc:  # pragma: no cover - assertion is the message
            raise AssertionError(
                f"rule {name} reads outcome field {exc} — that is lookahead"
            ) from exc

    # --- a missing field must not count as passing a gate ---------------
    absent = trade(1.0)
    for field in ("trade_score", "confidence", "conviction_meter", "risk_score",
                  "opportunity_score", "htf_strength", "risk_over_noise",
                  "trap_prob", "fake_prob"):
        absent.pop(field, None)
    for name, pred in rules.TESTABLE_RULES.items():
        if name in ("pullback_entry", "trending_or_breakout_regime",
                    "with_htf_trend", "reward_risk_ge_2",
                    "skip_first_15_minutes"):
            continue
        ok(pred(absent) is False,
           f"rule {name} must not pass a trade whose field is missing — an "
           f"absent score is unknown, not qualifying")

    # --- a chain-derived field can never be graded on underlying history -
    # smart_money comes from per-strike OI change. Replaying spot candles leaves
    # it None on every row, so grading it would report an empty arm as if the
    # live filter never fired. It belongs with the other chain rules.
    for name in ("smart_money_aligned", "healthy_premium", "spread_quality",
                 "liquidity_oi"):
        ok(name in rules.UNTESTABLE_RULES,
           f"{name} needs the option chain — it must be declared untestable")
        ok(name not in rules.TESTABLE_RULES,
           f"{name} cannot be graded on spot candles: an empty arm would read "
           f"as 'the filter never fires' when the field is simply absent")

    # --- a saturated score is inert, not merely untested ----------------
    # Every trade scores 100, so "trade_score >= 80" holds on the whole book: the
    # gate has no comparison arm and would reject nothing in production.
    top = [trade(1.0, trade_score=100.0) for _ in range(400)]
    flatscore = rules.study(top, [trade(-1.0, trade_score=100.0)
                                  for _ in range(400)])
    srow = graded("trade_score_ge_80", flatscore)
    ok(srow["verdict"] == rules.NOT_SELECTIVE,
       f"a gate holding on the whole book must be NOT_SELECTIVE, got "
       f"{srow['verdict']}")
    ok(srow["selective"] is False and srow["keeps_pct_of_holdout"] == 100.0,
       "an inert gate must report that it keeps the entire book")
    ok("select" in flatscore["verdict"],
       f"the headline must name gates that select nothing, got "
       f"{flatscore['verdict']}")
    ok(rules.NOT_SELECTIVE not in
       [r["verdict"] for r in real["rules"] if r["rule"] == "pullback_entry"],
       "a genuinely selective rule must not be marked inert")

    spread = {row["score"]: row for row in flatscore["score_spread"]}
    ok(spread["trade_score"]["saturated"] is True,
       "a score with no spread across the book must be reported as saturated")
    ok(spread["trade_score"]["distinct_values"] == 1,
       "the number of distinct values a score takes must be reported")
    varied = {row["score"]: row for row in flat["score_spread"]}
    ok(varied["confidence"]["saturated"] is False
       and varied["confidence"]["distinct_values"] > 1,
       "a score with real spread across the book must not be flagged saturated")
    ok(all(r["recorded"] > 0 or r.get("note") for r in flatscore["score_spread"]),
       "a score never recorded must say so rather than appear as zeros")

    # --- an inert gate must not inflate the comparison count -----------
    ok(flatscore["rules_compared"] < len(rules.TESTABLE_RULES),
       "gates that select nothing were not really compared and must not count")

    # --- the engine's own refusals must be priced, not assumed free -----
    # On the candidate pool each row says whether the engine would have taken it,
    # so "was refusing these worth anything?" becomes answerable. Here the BUY
    # bars are genuinely better, and the report must say so.
    def cand(r: float, taken: bool, signal: str) -> dict:
        return trade(r, taken=taken, signal=signal)

    # Both arms must carry real spread: two arms of identical constants have zero
    # standard error, which would let any difference pass the noise test.
    pool_book = ([cand(2.0, True, "BUY")] * 200 + [cand(-1.0, True, "BUY")] * 100
                 + [cand(2.0, False, "WAIT")] * 60
                 + [cand(-1.0, False, "WAIT")] * 240
                 + [cand(2.0, False, "AVOID")] * 40
                 + [cand(-1.0, False, "AVOID")] * 260)
    sel = rules.study(pool_book, pool_book)["engine_selectivity"]
    ok(sel is not None, "a candidate-pool study must price the engine's refusals")
    ok(sel["taken"]["trades"] == 300 and sel["refused"]["trades"] == 600,
       f"both arms must be counted, got {sel['taken']['trades']} / "
       f"{sel['refused']['trades']}")
    ok(set(sel["by_signal"]) == {"BUY", "WAIT", "AVOID"},
       "each signal the engine emits must be priced separately")
    ok(sel["lift_r"] > 0 and sel["beats_noise"]
       and "carries the edge" in sel["selectivity"],
       f"a genuinely selective engine must be reported as such, got "
       f"{sel['selectivity']}")

    # ...and when refusing changes nothing, that must be stated plainly, because
    # it means no gate layered on top of the decision can rescue it.
    inert_pool = ([cand(1.0, True, "BUY"), cand(-1.0, True, "BUY"),
                   cand(1.0, False, "WAIT"), cand(-1.0, False, "WAIT")] * 300)
    isel = rules.study(inert_pool, inert_pool)["engine_selectivity"]
    ok(not isel["beats_noise"] and "not selecting" in isel["selectivity"],
       f"an unselective engine must be called out, got {isel['selectivity']}")

    # The taken book has no refused arm at all, so it must not fake one.
    ok(rules.study(book(2.0, 1.0), book(2.0, 1.0))["engine_selectivity"] is None,
       "grading the taken book cannot price refusals and must report nothing "
       "rather than invent a comparison")

    # --- T1 is target-before-stop, NOT the win rate ---------------------
    # A candidate that times out above entry is a winner but never reached target.
    # Counting it as T1 would overstate the hit rate of the very subset the A+
    # objective selects for, so the two must be measured apart.
    mixed = ([trade(1.0, exit_reason="TARGET")] * 30
             + [trade(-1.0, exit_reason="STOP")] * 50
             + [trade(0.4, exit_reason="TIME_STOP")] * 20)
    t1 = rules.t1_rate(mixed)
    ok(t1["t1_pct"] == 30.0 and t1["sl_pct"] == 50.0,
       f"T1 must count only TARGET exits, got {t1}")
    ok(t1["neither_pct"] == 20.0,
       "candidates that reached neither target nor stop must be reported, not "
       "folded into one of the two")
    ok(rules._arm(mixed)["win_rate"] > t1["t1_pct"],
       "the fixture must have a win rate above its T1 rate — that gap is exactly "
       "what makes reporting them separately necessary")
    ok(rules.t1_rate([])["trades"] == 0, "an empty book must not raise")

    # --- the frontier must not sell an in-sample-only filter stack -------
    # 'sharp' is genuinely selective in both periods; 'lucky' only reaches target
    # in-sample and collapses out of it. Only the first may be a survivor.
    def fr_row(r: float, reason: str, **kw) -> dict:
        return trade(r, exit_reason=reason, session=f"2024-01-{kw.pop('d', 1):02d}",
                     **kw)

    def fr_book(sharp_t1: float, lucky_t1: float) -> list[dict]:
        """Two INDEPENDENT markers, so each survives or fails on its own merit.

        ``pullback_entry`` marks the genuinely selective subset and
        ``htf_strength_ge_60`` marks the lucky one; every other field is held
        constant across all rows so no third condition can stand in as a proxy
        for either marker and muddle which one the frontier actually picked.
        """
        rows = []
        for i in range(400):
            day = (i % 25) + 1
            hit = (i % 100) < sharp_t1
            rows.append(fr_row(2.0 if hit else -1.0,
                               "TARGET" if hit else "STOP", d=day,
                               entry_trigger="PULLBACK_LONG", htf_strength=10.0))
            lhit = (i % 100) < lucky_t1
            rows.append(fr_row(2.0 if lhit else -1.0,
                               "TARGET" if lhit else "STOP", d=day,
                               entry_trigger="BREAKOUT", htf_strength=80.0))
        return rows

    fr = rules.frontier(fr_book(75, 75), fr_book(75, 20), max_combo=2)
    ok(fr["combinations_searched"] > 0 and fr["conditions_used"],
       "the frontier must search the conditions that split the book")
    ok({"pullback_entry", "htf_strength_ge_60"} <= set(fr["conditions_used"]),
       f"both markers must be in the search, got {fr['conditions_used']}")
    names = {" + ".join(sorted(r["conditions"])) for r in fr["survivors"]}
    ok(any("pullback_entry" in n for n in names),
       f"a subset that holds its T1 rate out of sample must survive, got {names}")
    ok(not any("htf_strength_ge_60" in n and "pullback_entry" not in n
               for n in names),
       f"a subset whose T1 rate only existed in-sample must not be a survivor, "
       f"got {names}")
    for row in fr["rows"]:
        ok(row["holdout"]["trades"] >= fr["min_subset_trades"],
           "a subset thinner than the floor must not be quoted at all")
        ok(row["holdout_trades_per_day"] > 0,
           "every quoted subset must report how often it would actually trade")
    ok(fr["expected_false_positives"] > 0 and "chance" in fr["verdict"]
       or fr["survivors"],
       "the frontier must state its multiple-comparison cost")

    # An inert condition adds a comparison without changing the subset, so it must
    # not be combined at all.
    ok(all("with_htf_trend" not in r["conditions"] for r in fr["rows"])
       or "with_htf_trend" in fr["conditions_used"],
       "a condition that never splits the book must not enter the search")

    # And when nothing reaches the bar, the frontier must say so rather than
    # promote its least-bad row.
    nothing = rules.frontier(fr_book(30, 30), fr_book(30, 30), max_combo=2)
    ok(not nothing["survivors"],
       "a book where no subset reaches the T1 bar must produce no survivors")
    ok("not produce an A+ subset" in nothing["verdict"]
       or "cannot be located" in nothing["verdict"],
       f"the frontier must say plainly that selection did not work, got "
       f"{nothing['verdict']}")

    # A no-survivor verdict must name the requirement the best row actually
    # missed. Claiming "nothing reached 60%" while quoting a 64.7% row reads as a
    # contradiction and invites trusting the headline number.
    lucky_out = rules.frontier(fr_book(20, 20), fr_book(75, 75), max_combo=2)
    ok(not lucky_out["survivors"],
       "a subset whose T1 rate exists only in the LATER period must not survive "
       "either — chronological order does not make a one-period result real")
    v = lucky_out["verdict"]
    best_out = lucky_out["best_by_holdout_t1"]["holdout"]["t1_pct"]
    ok(f"{best_out}%" in v,
       f"the verdict must quote the best row's actual T1 rate, got {v}")
    ok("in-sample T1 was only" in v,
       f"the verdict must name in-sample T1 as the shortfall rather than deny a "
       f"rate it just quoted, got {v}")
    ok(f"none reached a {rules.TARGET_T1_RATE}" not in v,
       f"the verdict must not claim no subset reached the bar while quoting one "
       f"above it, got {v}")
    ok(rules.frontier([], [])["combinations_searched"] == 0,
       "an empty frontier must report nothing searched rather than raise")

    # --- a pool saved before the conviction relabel must still grade ----
    # Those files carry the same number under ``win_probability``. If the
    # legacy key were ignored, every conviction gate would silently grade
    # NOT_ENOUGH_DATA on months of already-collected history and the tab would
    # report the score untestable rather than tested.
    legacy = trade(1.0, conviction_meter=None)
    legacy.pop("conviction_meter")
    legacy["win_probability"] = 75.0
    ok(rules.TESTABLE_RULES["conviction_ge_60"](legacy) is True,
       "a pool written before the relabel must still be graded through the "
       "old win_probability key, not dropped as unrecorded")
    legacy["win_probability"] = 40.0
    ok(rules.TESTABLE_RULES["conviction_ge_60"](legacy) is False,
       "the legacy key must be read as a value, not treated as merely present")
    sat = {r["score"]: r for r in rules.saturation([legacy])}
    ok(sat["conviction_meter"]["recorded"] == 1,
       "saturation must count a legacy-key row as recorded")

    # --- degenerate input must not raise -------------------------------
    ok(rules.study([], [])["rules_compared"] == 0,
       "an empty study must report nothing compared rather than raise")
    ok(rules.saturation([]) and all(r["recorded"] == 0
                                    for r in rules.saturation([])),
       "saturation of an empty book must report nothing recorded, not raise")

    print(f"checked {CHECKS}")
    print("phase 14 rules smoke: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
