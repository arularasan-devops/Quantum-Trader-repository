"""Does the one-command market scan refuse the things it should? RESEARCH ONLY."""
from __future__ import annotations

import argparse

import market_scan as ms

CHECKS = 0


def ok(cond: bool, what: str) -> None:
    global CHECKS
    CHECKS += 1
    assert cond, what


def args(**kw: object) -> argparse.Namespace:
    base = {"instruments": None, "all": False}
    base.update(kw)
    return argparse.Namespace(**base)


def main() -> int:
    # --- the instrument list ---------------------------------------------
    ok(ms._names(args()) == ms.DEFAULT_SCAN,
       "the default scan is the index families plus the liquid single stocks")
    ok("NIFTY" in ms._names(args()),
       "NIFTY stays in the scan so the new instruments are ranked against the "
       "one instrument that has already been graded")
    ok(ms._names(args(instruments="nifty, banknifty")) == ["NIFTY", "BANKNIFTY"],
       "names are trimmed and upper-cased, and the caller's order is kept")

    # --- an unknown or untradeable name is refused, not silently dropped --
    for bad in ("NOTAREALNAME", "CRUDEOIL"):
        try:
            ms._names(args(instruments=f"NIFTY,{bad}"))
        except SystemExit as exc:
            ok(bad in str(exc),
               f"{bad} is named in the refusal so the reason is obvious")
        else:
            ok(False, f"{bad} should be refused rather than replayed")
    ok("CRUDEOIL" not in ms.TRADEABLE,
       "MCX commodities are excluded: they have no cash series to replay, so "
       "grading them would be grading nothing")

    # --- --all is the whole registry, and is opt-in -----------------------
    every = ms._names(args(all=True))
    ok(len(every) > len(ms.DEFAULT_SCAN) and set(every) == ms.TRADEABLE,
       "--all grades every NFO/BFO name the engine knows")
    ok(len(ms.DEFAULT_SCAN) < 15,
       "the default is a short list: collecting the whole registry at 1-minute "
       "resolution is an overnight job and must be asked for explicitly")

    # --- a step that fails stops the chain -------------------------------
    try:
        ms._step("a step that cannot work", ["definitely_not_a_script.py"])
    except SystemExit as exc:
        ok("failed" in str(exc),
           "a failed step raises instead of letting the next step grade a pool "
           "that was never written")
    else:
        ok(False, "a failing step must stop the chain")

    # --- the ranking print refuses to rank one instrument ----------------
    ms._ranking({"ranking_is_comparable": False, "instruments_graded": 1,
                 "ranking": []})
    ok(True, "a one-instrument study prints the reason instead of a ranking")
    ms._ranking({
        "ranking_is_comparable": True, "instruments_graded": 2,
        "ranking": [
            {"rank": 1, "instrument": "B", "band": "WILD", "holdout_rows": 200,
             "holdout_t1_pct": 44.0, "holdout_expectancy_r": 0.07,
             "median_mfe_points": 16.0, "session_share_pct": 70.0,
             "verdict": "SURVIVES_SO_FAR"},
            {"rank": None, "instrument": "C", "band": "QUIET",
             "holdout_rows": 9, "holdout_t1_pct": None,
             "holdout_expectancy_r": None, "median_mfe_points": None,
             "session_share_pct": None, "verdict": "REQUIRES_MORE_DATA"}]})
    ok(True, "an unranked cohort with null numbers prints n/a rather than 0%")

    print(f"checked {CHECKS}")
    print("market scan smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
