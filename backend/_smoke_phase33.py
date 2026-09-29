"""Phase 33 smoke — the properties that stop a hold window from being a fake one.

Each check exists because the opposite mistake makes a holding period look good
when it is not: crediting the fill bar's own high to the trade, mixing price points
into a percentage excursion, letting a window cross an overnight gap, resolving a
same-minute favourable/adverse tie in favour of the trade, calling a move that
never paid its cost a profit that was given back, labelling the farthest candidate
target T1, choosing a horizon with the holdout visible, counting only the
hypotheses that got far enough to look promising, or pricing an option cohort from
a one-sided quote.

The fixture series has an exactly known forward path, so the arithmetic is checked
against numbers computed by hand.

    .venv/bin/python _smoke_phase33.py
"""
from __future__ import annotations

import json
import os
import tempfile

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase31.excursion import LONG, SHORT
from app.research.phase33 import (
    FUT_LEVELS,
    HOLD_GRID,
    MAX_HOLD,
    MEASURED,
    MFE_PERCENTILES,
    MIN_OPTION_INSTANTS,
    MIN_ROWS_PER_FAMILY,
    MIN_ROWS_PER_WINDOW,
    NO_HOLD_WINDOW,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD_HOLD,
    SESSION_CLOSE,
    UNMEASURED,
    VALIDATED_HOLD,
    cohorts,
    compare,
    families,
    hold,
    overnight,
    path as pathmod,
    report,
    select,
    universe,
)

PASS = 0
FAIL: list[str] = []

DAY = 86_400
IST_OFFSET = 19_800
OPEN_IST = 9 * 3_600 + 15 * 60
INSTRUMENT = "NIFTY"
BARS = 200


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"  FAILED: {label}")


def series_file(path: str, *, sessions: int = 40) -> None:
    """A rising fixture: +1.0 per bar, wick 0.5 each way, sessions restart flat."""
    base_day = 1_780_000_000 // DAY * DAY
    with open(path, "w", encoding="utf-8") as fh:
        for d in range(sessions):
            start = base_day + d * DAY + OPEN_IST - IST_OFFSET
            for m in range(BARS):
                o = 1_000.0 + m
                fh.write(json.dumps({
                    "time": start + m * 60,
                    "open": o,
                    "high": o + 0.5,
                    "low": o - 0.5,
                    "close": o + 0.4,
                    "volume": 1_000.0,
                }) + "\n")


def load_fixture(path: str):
    original = dict(p24data.BACKTEST_FILES)
    p24data.BACKTEST_FILES.clear()
    p24data.BACKTEST_FILES[INSTRUMENT] = path
    try:
        return p24data.load_series(INSTRUMENT)
    finally:
        p24data.BACKTEST_FILES.clear()
        p24data.BACKTEST_FILES.update(original)


def spike_series(instrument: str = "FIXTURE"):
    """One 200-bar session whose fill bar is the highest bar of the window.

    Bar 0 decides, bar 1 fills; bar 1 carries a tall high and a deep low, and every
    later bar is flat. A study that credits the fill bar's own high to the trade
    reports a favourable excursion here; an honest one reports none.
    """
    rows = []
    base = 1_780_000_000 // DAY * DAY + OPEN_IST - IST_OFFSET
    for m in range(BARS):
        if m == 1:
            o, hi, lo, cl = 1_000.0, 1_100.0, 990.0, 1_000.0
        else:
            o, hi, lo, cl = 1_000.0, 1_000.5, 999.5, 1_000.0
        rows.append({"time": base + m * 60, "open": o, "high": hi,
                     "low": lo, "close": cl, "volume": 1.0})
    return p24data.Series(instrument, rows)


def flat_path(n: int, sessions: int, ret: float, instrument: str = "FIXTURE"):
    """A Path built by hand, so window and correction rules can be checked exactly."""
    p = pathmod.Path(instrument, LONG)
    per = n // sessions
    p.entry = np.full(n, 100.0)
    p.fav = {h: np.full(n, 0.5, dtype=np.float32) for h in HOLD_GRID}
    p.adv = {h: np.full(n, -0.5, dtype=np.float32) for h in HOLD_GRID}
    p.ret = {h: np.full(n, ret, dtype=np.float32) for h in HOLD_GRID}
    p.sess_fav = np.full(n, 0.5, dtype=np.float32)
    p.sess_adv = np.full(n, -0.5, dtype=np.float32)
    p.sess_ret = np.full(n, ret, dtype=np.float32)
    p.first_touch = np.zeros((len(FUT_LEVELS), n), dtype=np.int16)
    p.first_adverse = np.zeros((len(FUT_LEVELS), n), dtype=np.int16)
    p.peak_minute = np.full(n, 5, dtype=np.int16)
    p.post_peak_low = np.full(n, -0.1, dtype=np.float32)
    p.session = np.repeat(np.arange(sessions), per)[:n]
    p.minute = np.tile(np.arange(per), sessions)[:n]
    p.ts_minute = np.arange(n, dtype=np.int64) * 60
    p.bars_available = np.full(n, MAX_HOLD + 1, dtype=np.int64)
    p.sess_minutes = np.full(n, per, dtype=np.int32)
    p.eligible = np.ones(n, dtype=bool)
    return p


def board_row(inst: str, ts: int, strike: float, kind: str,
              bid: float | None, ask: float | None) -> dict:
    two = bid is not None and ask is not None and bid > 0 and ask > bid
    return {"source": "SOURCE_B", "instrument": inst, "ts": ts, "strike": strike,
            "option_type": kind, "premium": ask or 0.0, "bid": bid, "ask": ask,
            "spread_pct": None, "iv": None, "delta": None, "oi": None,
            "volume": None, "provenance": "REAL_BROKER",
            "pricing": MEASURED if two else UNMEASURED}


def main() -> int:  # noqa: PLR0915 - one linear checklist, deliberately flat
    global PASS

    # ---------------------------------------------------------------- path rules
    s = spike_series()
    p = pathmod.build(s, LONG)
    ok(p.entry[0] == s.open[1],
       "the fill is the next bar's open, never the decision bar's close")
    ok(float(p.fav[MAX_HOLD][0]) <= 0.06,
       "the fill bar's own high is excluded from the favourable excursion")
    ok(float(p.adv[1][0]) < -0.9,
       "the fill bar's low is included in the adverse excursion")
    ok(abs(float(p.adv[1][0]) + 1.0) < 0.01,
       "the fill bar's adverse extreme is a percentage, not a price difference")

    rising = None
    with tempfile.TemporaryDirectory() as tmp:
        fpath = os.path.join(tmp, "fix.jsonl")
        series_file(fpath)
        rising = load_fixture(fpath)
    rp = pathmod.build(rising, LONG)
    last = BARS - 1
    ok(not rp.eligible[last],
       "a bar with fewer than the longest horizon's bars left is not eligible")
    ok(not np.isfinite(rp.ret[MAX_HOLD][last]),
       "a holding window never crosses into the next session")
    ok(bool(rp.eligible[0]) and int(rp.eligible.sum()) ==
       (BARS - MAX_HOLD - 1) * 40,
       "eligibility is decided by the longest horizon on every session")
    # Fill at 1,001 on bar 1; ten minutes later bar 11 closes at 1,011.4.
    ok(abs(float(rp.ret[10][0]) - 100.0 * (1_011.4 - 1_001.0) / 1_001.0) < 1e-3,
       "the close-to-close return at a horizon is measured, not the excursion")
    ok(int(rp.peak_minute[0]) == MAX_HOLD,
       "the peak minute is the last new favourable high, on a monotone rise")
    sp = pathmod.build(rising, SHORT)
    ok(float(sp.fav[10][0]) < 0 < float(rp.fav[10][0]),
       "a short's favourable side is the mirror of a long's on the same bars")

    try:
        rp.level_index(0.123)
        ok(False, "an unfrozen move level is refused")
    except KeyError:
        ok(True, "an unfrozen move level is refused")

    m0 = np.zeros(len(rising), dtype=bool)
    m0[0] = True
    ok(pathmod.reach_rate(rp, 0.10, MAX_HOLD, m0) == 1.0,
       "a distance the path reached inside the horizon counts as reached")
    ok(pathmod.reach_rate(rp, 2.00, 10, m0) == 0.0,
       "a distance reached after the horizon does not count as reached")

    # A hand-built tie: favourable and adverse touched in the same minute.
    tie = pathmod.Path("FIXTURE", LONG)
    tie.first_touch = np.full((len(FUT_LEVELS), 1), 4, dtype=np.int16)
    tie.first_adverse = np.full((len(FUT_LEVELS), 1), 4, dtype=np.int16)
    one = np.ones(1, dtype=bool)
    ok(pathmod.adverse_first_rate(tie, 0.10, 10, one) == 1.0,
       "a same-minute favourable/adverse tie resolves as adverse-first")

    # --------------------------------------------------------------- hold tables
    cost = np.full(len(rising), 0.04)
    mask = rp.eligible.copy()
    table = hold.hold_table(rp, mask, cost)
    ok(len(table) == len(HOLD_GRID) + 1
       and table[-1]["horizon"] == SESSION_CLOSE,
       "the hold table carries every frozen horizon plus the session close")
    row = table[HOLD_GRID.index(10)]
    ok(abs(row["net_expectancy_pct"] - (row["gross_expectancy_pct"] - 0.04)) < 1e-4,
       "net expectancy is gross minus the round-trip cost, at every horizon")
    ok(row["mfe_pct_median"] >= row["gross_expectancy_pct"],
       "the favourable excursion is never smaller than what a closed hold got")
    ok(row["giveback_pct_mean"] is not None and row["giveback_pct_mean"] > 0,
       "giveback is the best look minus what the hold actually kept")
    ok(row["sessions"] == 40 and row["n"] == int(mask.sum()),
       "every row reports its own sample and independent session count")

    dd = hold._max_drawdown(np.array([1.0, -3.0, 1.0]))
    ok(abs(dd - 3.0) < 1e-9,
       "max drawdown is peak-to-trough of the chronological equity curve")
    ok(abs(hold._pf(np.array([2.0, -1.0])) - 2.0) < 1e-9,
       "profit factor is gross wins over gross losses")

    gb = hold.giveback(rp, mask, cost)
    ok(gb["peak_minute_median"] is not None and gb["n"] == int(mask.sum()),
       "giveback reports the median peak minute over its own sample")
    ok(len(gb["retained_by_horizon"]) == len(HOLD_GRID)
       and all(r["retained_gain_rate_pct_per_min"] is not None
               for r in gb["retained_by_horizon"]),
       "retained profit and its per-minute gain rate are reported at every horizon")
    ok(gb["giveback_exceeds_gain_after_minute"] in HOLD_GRID
       and gb["fastest_giveback_minute"] in HOLD_GRID,
       "the minute holding starts costing more than it adds is a frozen horizon")
    tiny = hold.giveback(rp, mask, np.full(len(rising), 100.0))
    ok(tiny["profitable_at_some_point_rate"] == 0.0,
       "a move that never cleared its own cost was never a profit to give back")

    tg = hold.empirical_targets(rp, mask, cost, MAX_HOLD)
    ok([t["target_from_percentile"] for t in tg] == list(MFE_PERCENTILES),
       "candidate targets come from the frozen percentile grid only")
    ok(all(t["basis"] == "EMPIRICAL_REACH_RATE" for t in tg),
       "a reach rate is labelled a reach rate, never a probability")
    ok(tg[0]["level_pct"] >= tg[-1]["level_pct"],
       "a higher reach percentile names a nearer target")
    ok(all(isinstance(t["clears_cost"], bool) for t in tg),
       "each candidate target says whether it clears its own cost")

    # ----------------------------------------------------------------- selection
    # 100 sessions, so the 20% holdout still clears the 20-session floor.
    n = 10_000
    good = flat_path(n, 100, ret=0.10)
    c2 = np.full(n, 0.04)
    win = select.windows(good, good.eligible)
    ok(set(win) == {select.DEV, select.VAL, select.HOLDOUT},
       "the split is development, validation and an untouched holdout")
    ok(not (win[select.DEV] & win[select.VAL]).any()
       and not (win[select.VAL] & win[select.HOLDOUT]).any(),
       "the three windows never share a row")
    dev_sess = set(np.unique(good.session[win[select.DEV]]).tolist())
    hold_sess = set(np.unique(good.session[win[select.HOLDOUT]]).tolist())
    ok(max(dev_sess) < min(hold_sess),
       "the split is chronological by session, never random and never by row")
    ok(len(select.folds(good, good.eligible)) == 5,
       "walk-forward uses five chronological folds")

    res = select.evaluate(good, good.eligible, c2, "ALL")
    ok(res["horizons_tested"] == len(HOLD_GRID) + 1,
       "every horizon looked at is counted, including the session close")
    ok(res["selected_horizon"] is not None
       and res["selection_basis"] == "BEST_DEVELOPMENT_NET_EXPECTANCY",
       "the holding window is chosen on development data only")
    ok(res["status"] == RESEARCH_LEAD_HOLD and not res["failures"],
       "a cohort that survives every economic test is a lead, not yet validated")
    ok([r["cost_multiple"] for r in res["cost_stress"]] == [1.0, 1.5, 2.0],
       "the holdout is re-priced at 1.5x and 2x costs")
    ok([r["top_winners_removed_pct"] for r in res["outlier_trims"]] == [0.0, 1.0, 5.0],
       "the holdout is re-priced with the top 1% and 5% of winners removed")

    losing = flat_path(n, 100, ret=0.01)
    lose = select.evaluate(losing, losing.eligible, c2, "ALL")
    ok(lose["status"] == NO_HOLD_WINDOW and lose["selected_horizon"] is None,
       "nothing is carried to the holdout when development is not positive")

    thin = flat_path(300, 30, ret=0.10)
    small = select.evaluate(thin, thin.eligible, np.full(300, 0.04), "ALL")
    ok(small["status"] == REQUIRES_MORE_DATA
       and str(MIN_ROWS_PER_WINDOW) in small["reason"],
       "a window below the sample floor reports the count, not a verdict")

    corr = select.apply_correction([dict(res)])
    ok(corr["hypotheses_counted"] == len(HOLD_GRID) + 1,
       "the correction denominator counts every horizon that was evaluated")
    lead = dict(res)
    lead["holdout_p_value"] = 0.4
    c3 = select.apply_correction([lead])
    ok(c3["validated"] == 0 and lead["status"] == RESEARCH_LEAD_HOLD
       and "Benjamini" in lead["reason"],
       "an economic survivor that fails the correction stays a research lead")
    strong = dict(res)
    strong["holdout_p_value"] = 1e-9
    c4 = select.apply_correction([strong])
    ok(c4["verdict"] == VALIDATED_HOLD and strong["status"] == VALIDATED_HOLD,
       "a lead that clears the correction is the only thing called validated")
    ok(select.apply_correction([])["verdict"] == NO_HOLD_WINDOW,
       "no candidate at all is a no-hold-window verdict, not an empty success")

    # ------------------------------------------------------------------ families
    coh = families.build(rising, LONG)
    ok(coh.masks[families.ALL].sum() > 0
       and set(coh.masks) == set(families.NAMES),
       "every frozen family, including the ALL control, gets a mask")
    ok(not coh.masks[families.ALL][~coh.warmup].any(),
       "no family selects a bar whose ATR warmup is incomplete")
    _, into = pathmod._prepare(rising)
    orm = coh.masks["OPENING_RANGE"]
    ok(not orm.any() or int(into[orm].max()) < families.OPENING_MINUTES,
       "the opening-range family uses the instrument's own session, not a clock")
    ok(families.rankable(np.ones(MIN_ROWS_PER_FAMILY, dtype=bool))
       and not families.rankable(np.ones(MIN_ROWS_PER_FAMILY - 1, dtype=bool)),
       "a family below the sample floor is described but never ranked")
    short_coh = families.build(rising, SHORT)
    ok(short_coh.masks["REVERSAL"].shape == coh.masks["REVERSAL"].shape,
       "the reversal family is the side's own shapes, per side")

    # ----------------------------------------------------------------- overnight
    on = overnight.measure(rising, rp, mask)
    ok(on["basis"] == MEASURED and on["sessions"] <= 39,
       "the last session is dropped rather than resolved against nothing")
    ok(on["to_next_open"]["median_pct"] is not None
       and f"to_next_open_plus_{overnight.NEXT_OPEN_PLUS}m" in on,
       "overnight is reported at the next open and 30 minutes after it")
    on_short = overnight.measure(rising, sp, mask)
    ok((on["overnight_gap"]["mean_pct"] or 0.0)
       == -(on_short["overnight_gap"]["mean_pct"] or 0.0),
       "the overnight gap is signed in the trade's own favour")

    # ------------------------------------------------------------------- cohorts
    ok(cohorts.moneyness(100.0, 100.0, cohorts.CE) == "ATM",
       "a strike on the underlying is ATM")
    ok(cohorts.moneyness(105.0, 100.0, cohorts.CE) == "OTM"
       and cohorts.moneyness(105.0, 100.0, cohorts.PE) == "ITM",
       "the same strike is OTM for a call and ITM for a put")
    ok(cohorts.moneyness(100.0, None, cohorts.CE) == "UNKNOWN",
       "a row with no measured underlying gets no moneyness band")
    board = [board_row("NIFTY", 1_780_000_000, 100.0, cohorts.CE, 9.0, 10.0),
             board_row("NIFTY", 1_780_000_060, 100.0, cohorts.PE, None, 10.0),
             board_row("NIFTY", 1_780_000_060, 100.0, cohorts.PE, 12.0, 11.0)]
    sp_res = cohorts.split(board, {("NIFTY", 1_780_000_000): 100.0})
    ok(sp_res["total_rows"] == 3 and sp_res["total_two_sided_rows"] == 1,
       "a one-sided or crossed quote is counted for coverage and never priced")
    ok(sp_res["premium_hold_status"] == REQUIRES_MORE_DATA
       and sp_res["min_required_per_cohort"] == MIN_OPTION_INSTANTS,
       "an option cohort under the floor reports the shortfall, not a number")
    ok(sp_res["dte_status"] == UNMEASURED,
       "DTE cohorts are unmeasured while the board carries no expiry")
    ok(sp_res["vehicle_comparison_status"] == REQUIRES_MORE_DATA,
       "futures-versus-CE-versus-PE needs two-sided option quotes it does not have")

    # ------------------------------------------------------------- engine vs board
    engine = [{"source": "SOURCE_A", "instrument": "NIFTY", "ts": 1_780_000_000,
               "signal": compare.BUY},
              {"source": "SOURCE_A", "instrument": "NIFTY", "ts": 1_780_000_120,
               "signal": "AVOID"}]
    cov = compare.coverage(engine, board)
    ok(cov["engine_rows"] == 2 and cov["engine_buys"] == 1,
       "the engine pool keeps every label, not only the BUYs")
    ok(cov["board_rows"] == 3 and cov["board_rows_priceable"] == 1,
       "the board pool is counted independently of the engine's opinion")
    ok(cov["board_minutes_without_engine_buy"] == 1,
       "board minutes the engine never acted on are counted")

    diag = compare.underlying_diagnosis(
        engine, board, {"FIXTURE": {LONG: good}}, {"FIXTURE": c2}, horizon=120)
    ok(diag["interpretation"] == "COUNTERFACTUAL"
       and diag["vehicle"] == "UNDERLYING_ONLY",
       "the engine-versus-board comparison is underlying-only and hindsight")

    # -------------------------------------------------------------------- report
    fam_row = {
        "family": "ALL", "n": int(mask.sum()), "sessions": 40, "rankable": True,
        "hold_table": table, "move_distribution": hold.move_distribution(
            rp, mask, MAX_HOLD),
        "giveback": gb, "empirical_targets": tg,
        "overnight": on, "selection": res,
    }
    fake = {
        "answerability": {
            "five_year_instruments": ["FIXTURE"],
            "short_capture_instruments": [],
            "options": {"real_two_sided_observations": 1,
                        "min_required": MIN_OPTION_INSTANTS},
            "engine": {"actionable_buys": 1, "sessions": 1},
        },
        "five_year_instruments": ["FIXTURE"],
        "instruments": [{"instrument": "FIXTURE",
                         "sides": {"LONG": {"families": [fam_row]}}}],
        "selections": [res],
        "correction": corr,
        "engine_vs_board": {"coverage": cov, "underlying_diagnosis": diag},
        "option_cohorts": sp_res,
        "verdict": corr["verdict"],
        "verdict_scope": "FUTURES_UNDERLYING_ONLY",
        "verdict_note": "note",
        "production_changed": False,
    }
    cards = report.signal_cards(fake)
    ok(len(cards) == 1 and cards[0]["usage"] == report.RESEARCH_ONLY,
       "every signal card carries its research-only status")
    t = {row["t"]: row for row in cards[0]["candidate_targets"]}
    ok(t["T1"]["level_pct"] <= t["T3"]["level_pct"]
       and t["T1"]["from_percentile"] == 90,
       "T1 is the nearest candidate target, read off the highest reach percentile")
    ok(cards[0]["best_net_hold"]["basis"] ==
       "BEST_ON_FULL_HISTORY_NOT_A_SELECTION",
       "the best historical hold is labelled as not being a selection")
    ans = report.answers(fake)
    ok(len(ans) == len(universe.QUESTION_SOURCES),
       "all 21 questions are answered, none quietly dropped")
    ok(all(v["basis"] in (MEASURED, REQUIRES_MORE_DATA, "COUNTERFACTUAL",
                          "EMPIRICAL_REACH_RATE")
           for v in ans.values()),
       "every answer carries the basis it was computed on")
    ok(ans["ce_or_pe_better_net_economics"]["basis"] == REQUIRES_MORE_DATA,
       "CE-versus-PE stays unanswered rather than inferred from the underlying")
    md = report.markdown(fake, cards, ans)
    ok(f"VERDICT: {corr['verdict']}" in md
       and "production changed: no" in md,
       "the report leads with the verdict and states production was untouched")

    with tempfile.TemporaryDirectory() as tmp:
        out = report.write(fake, out_dir=tmp)
        names = set(os.listdir(tmp))
        ok(names == set(report.FILES) and out["dir"] == tmp,
           "the artefact set is written where it is asked to be, and nowhere else")
        cards_on_disk = json.loads(
            open(os.path.join(tmp, "p33_signal_cards.json")).read())
        ok(cards_on_disk[0]["family"] == "ALL",
           "the signal cards survive serialisation")

    print(f"PHASE 33 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
