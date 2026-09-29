"""Phase 20 smoke — research universe, per-instrument ranking, vehicle choice.

Run: .venv/bin/python _smoke_phase20.py

What this asserts, in the order it matters:

1. the universe is no longer index-only: the five indexes, the five MCX names and
   the optionable equities are all in it, each with its own record;
2. what may be claimed is stated per instrument — a five-year direction study
   where a cash series exists, never for MCX, and historical option profitability
   for nobody, because expired chains are not fetchable;
3. an instrument refused on a validated negative prior stays out of research,
   while a name that was never measured is admitted rather than refused on
   absence of evidence;
4. ranking is per instrument and a pooled statistic is refused outright;
5. no instrument may be called profitable on a thin book, or on a pooled positive
   that does not survive the chronological holdout and the folds — the same bar a
   negative has to clear;
6. Futures vs CE vs PE is compared only at the same signal, same direction, and
   only between legs whose round trip was actually costed;
7. a vehicle preference is withheld until there are enough same-signal
   comparisons, and the gap names itself;
8. nothing in Phase 20 can place an order.
"""
from __future__ import annotations

import os
import pathlib
import re
import tempfile

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="p20smoke-"))

from app.analysis import instrument_family as fam  # noqa: E402
from app.research.phase17 import capture as p17capture  # noqa: E402
from app.research.phase19 import grading_ab as ab  # noqa: E402
from app.research.phase20 import direction  # noqa: E402
from app.research.phase20 import ranking  # noqa: E402
from app.research.phase20 import universe as uni  # noqa: E402
from app.research.phase20 import vehicle  # noqa: E402

CHECKS = 0
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)


# ---------------------------------------------------------------- 1. universe
names = [p["instrument"] for p in uni.universe()]
check(len(names) == len(set(names)), "each instrument appears once in the universe")
for want in uni.INDEX_STUDY:
    check(want in names, f"the index study name {want} is in the universe")
for want in uni.MCX_STUDY:
    check(want in names, f"the MCX study name {want} is in the universe")
check(len(uni.equity_study()) >= 40,
      "a broad optionable equity universe is present, not a handful")
groups = uni.study_groups()
check(set(groups) == {fam.INDEX, fam.MCX, fam.STOCK},
      "the universe separates indexes, MCX and equities")
check(not (set(groups[fam.INDEX]) & set(groups[fam.STOCK])),
      "and an index is never counted as a stock")
check(all(uni.plan(n)["family"] == fam.MCX for n in uni.MCX_STUDY),
      "every MCX study name classifies as MCX")

# ------------------------------------------------------- 2. claim availability
nifty = uni.plan("NIFTY")
crude = uni.plan("CRUDEOIL")
tcs = uni.plan("TCS")
check(nifty["history_basis"] == uni.CASH_INDEX, "an index has a cash index series")
check(tcs["history_basis"] == uni.CASH_EQUITY, "an equity has its NSE cash series")
check(crude["history_basis"] == uni.FUTURES_CONTRACT_ONLY,
      "a commodity has no cash series, only the futures contract")
check(nifty["five_year_direction_study"] and tcs["five_year_direction_study"],
      "a five-year direction study is available for indexes and equities")
check(not crude["five_year_direction_study"],
      "and is NOT claimed for MCX, whose history starts at contract listing")
for name in ("NIFTY", "TCS", "CRUDEOIL", "GOLD"):
    plan = uni.plan(name)
    check(plan["may_claim_historical_option_profit"] is False,
          f"historical option profitability is unclaimable for {name}")
    check("HISTORICAL_OPTION_CHAINS_UNAVAILABLE" in plan["option_basis"],
          f"and {name} says why: no expired chain to read a fill from")
    check(plan["option_evidence_source"] == uni.OPTION_EVIDENCE_SOURCE,
          f"{name}'s option evidence can only come from live costed paper")
check(uni.HISTORICAL_OPTION_CHAINS_AVAILABLE is False,
      "the unavailability is a constant, not a threshold someone can relax")
check(uni.may_claim("NIFTY", "SOMETHING_ELSE")[0] is False,
      "an unknown claim is refused rather than allowed by default")

# ----------------------------------------------------------- 3. admission rules
check(uni.plan("ICICIBANK")["research_admitted"] is False,
      "the one validated negative prior stays out of research")
check(uni.plan("ICICIBANK")["refused_because"] == ab.VALIDATED_NEGATIVE,
      "and the refusal names the label it rests on")
check(uni.plan("TCS")["research_admitted"] is True
      and uni.plan("TCS")["prior_label"] == ab.NEGATIVE_BUT_UNDER_BAR,
      "a negative that missed the bar is admitted but still labelled negative")
check(uni.plan("KALYANKJIL")["prior_label"] == ab.INSUFFICIENT_DATA
      and uni.plan("KALYANKJIL")["research_admitted"] is True,
      "a never-measured name is admitted: absence of evidence is not a finding")
check("ICICIBANK" not in uni.capture_universe(),
      "capture does not spend storage on a book research will not study")
check("CRUDEOIL" in uni.capture_universe() and "TCS" in uni.capture_universe(),
      "but MCX and equities are captured, so A+ is not an index-only system")
check(p17capture.eligible("CRUDEOIL") and p17capture.eligible("SBIN"),
      "the Phase 17 capture layer reads the same universe")
check(not p17capture.eligible("NOT_A_REAL_NAME"),
      "and an unregistered name is never captured")


# ------------------------------------------------- 4./5. per-instrument ranking
def leg(instrument: str, session: str, r: float, **kw: object) -> dict:
    row = {"instrument": instrument, "session": session, "r": r,
           "exit_reason": "TARGET" if r > 0 else "STOP"}
    row.update(kw)
    return row


def book(instrument: str, n: int, r_of) -> list[dict]:
    return [leg(instrument, f"2026-01-{1 + i % 25:02d}", r_of(i)) for i in range(n)]


good = book("CRUDEOIL", 250, lambda i: 1.0 if i % 3 else -0.6)
thin = book("TCS", 9, lambda i: 0.9)
ranked = ranking.rank([*good, *thin])
by_name = {r["instrument"]: r for r in ranked}
check(len(ranked) == 2 and set(by_name) == {"CRUDEOIL", "TCS"},
      "each instrument is ranked on its own book")
check(by_name["CRUDEOIL"]["family"] == fam.MCX
      and by_name["TCS"]["family"] == fam.STOCK,
      "and carries its family, so families are never silently merged")
check(by_name["TCS"]["verdict"] == ranking.INSUFFICIENT_DATA
      and by_name["TCS"]["may_be_called_profitable"] is False,
      "nine winning legs is not a profitable instrument, it is no verdict yet")
check(by_name["CRUDEOIL"]["rank"] < by_name["TCS"]["rank"],
      "a measured instrument outranks one without enough evidence")
refusal = ranking.pooled_statistic_refused([*good, *thin])
check(refusal["pooled_statistic"] == "REFUSED"
      and set(refusal["families"]) == {fam.MCX, fam.STOCK},
      "a single number across instruments is refused, and says which it spans")

# A pooled positive that one stretch of the history carried must not survive.
carried = ([leg("GOLD", f"2026-01-{1 + i:02d}", 3.0) for i in range(40)]
           + [leg("GOLD", f"2026-02-{1 + i:02d}", -0.05) for i in range(28)]
           + [leg("GOLD", f"2026-03-{1 + i:02d}", -0.05) for i in range(28)])
carried_verdict = ab.validate_instrument("GOLD", carried)
check(carried_verdict["pooled_verdict"] == ab.VALIDATED_POSITIVE,
      "the pooled mean of a one-period winner does clear the bar")
check(carried_verdict["verdict"] != ab.VALIDATED_POSITIVE
      and carried_verdict["downgraded_because"] is not None,
      "but out of sample it is downgraded, and the reason is named")
check(ranking.rank(carried)[0]["may_be_called_profitable"] is False,
      "so the ranking refuses to call it profitable")
steady = book("SILVER", 250, lambda i: 1.0 if i % 3 else -0.6)
steady_verdict = ab.validate_instrument("SILVER", steady)
check(steady_verdict["verdict"] == ab.VALIDATED_POSITIVE
      and steady_verdict["walk_forward"]["stable_positive"],
      "a positive that holds out of sample and fold to fold still validates")

# --------------------------------------------- 6./7. futures vs CE vs PE
TS = 1788150000


def opt(instrument: str, vehicle_name: str, r: float, *, ts: float = TS,
        direction_word: str = "BULLISH", costed: bool = True) -> dict:
    return {"instrument": instrument, "vehicle": vehicle_name,
            "direction": direction_word, "signal_ts": ts, "net_r": r,
            "cost_status": "MEASURED" if costed else "UNKNOWN", "strike": 100}


def fut(instrument: str, r: float, *, ts: float = TS,
        direction_word: str = "LONG") -> dict:
    return {"instrument": instrument, "vehicle": "FUTURES",
            "direction": direction_word, "entry_ts": ts, "net_r": r}


pairs = vehicle.pair([opt("CRUDEOIL", "CE", 0.4), opt("CRUDEOIL", "PE", -1.0)],
                     [fut("CRUDEOIL", 0.9, ts=TS + 60)])
check(len(pairs) == 1, "one market signal produces one comparison row")
one = pairs[0]
check(set(one["net_r_by_vehicle"]) == {"CE", "PE", "FUTURES"},
      "with all three vehicles on it, taken at the same signal")
check(one["comparable"] and one["best_vehicle"] == "FUTURES",
      "and the best vehicle on that signal is named")
check(one["direction"] == vehicle.UP,
      "BULLISH and LONG are recognised as the same market direction")

far = vehicle.pair([opt("CRUDEOIL", "CE", 0.4)],
                   [fut("CRUDEOIL", 0.9, ts=TS + 4 * 3600)])
check(len(far) == 2 and all(not r["comparable"] for r in far),
      "a futures entry hours later is a different signal, not a comparison")
check(any(r["not_comparable_because"].get("FUTURES") == vehicle.NOT_CAPTURED
          for r in far),
      "and the absent vehicle is reported as not captured, never as a zero")

opposed = vehicle.pair([opt("CRUDEOIL", "CE", 0.4)],
                       [fut("CRUDEOIL", 0.9, direction_word="SHORT")])
check(len(opposed) == 2,
      "a long future never pairs with a call bought on a bearish read")

gross = vehicle.pair([opt("CRUDEOIL", "CE", 5.0, costed=False),
                      opt("CRUDEOIL", "PE", -1.0)],
                     [fut("CRUDEOIL", 0.9)])
check("CE" not in gross[0]["legs"]
      and gross[0]["not_comparable_because"]["CE"] == vehicle.COST_NOT_MEASURED,
      "an uncosted leg cannot win a comparison against costed ones")

multi = vehicle.pair([opt("CRUDEOIL", "CE", 2.0), opt("CRUDEOIL", "CE", -2.0),
                      opt("CRUDEOIL", "PE", 0.1)], [])
check(multi[0]["legs"]["CE"]["legs"] == 2
      and multi[0]["legs"]["CE"]["net_r"] == 0.0,
      "two call strikes at one signal average, so CE is not credited with its best")
check(multi[0]["best_vehicle"] == "PE",
      "and the losing average loses the comparison it would have won on one strike")

uncosted_only = vehicle.pair([opt("CRUDEOIL", "CE", 2.0, costed=False),
                              opt("CRUDEOIL", "CE", 0.5)], [])
check("CE" in uncosted_only[0]["legs"]
      and "CE" not in uncosted_only[0]["not_comparable_because"],
      "one uncosted leg does not mark a vehicle absent when another was costed")

unknown_dir = vehicle.pair([opt("CRUDEOIL", "CE", 0.4, direction_word="SIDEWAYS")],
                           [fut("CRUDEOIL", 0.9)])
check(len(unknown_dir) == 2,
      "an unreadable direction is reported alone rather than joined on a guess")

thin_pairs = vehicle.pair(
    [opt("GOLD", "CE", 0.5, ts=TS + i * 86400) for i in range(4)]
    + [opt("GOLD", "PE", -0.5, ts=TS + i * 86400) for i in range(4)],
    [fut("GOLD", 0.2, ts=TS + i * 86400) for i in range(4)])
summary = vehicle.by_instrument(thin_pairs)[0]
check(summary["comparable_signals"] == 4
      and summary["enough_to_prefer_a_vehicle"] is False,
      "four signals is not enough to prefer a vehicle")
check(summary["preferred_vehicle"] is None
      and summary["verdict"] == vehicle.INSUFFICIENT_DATA
      and summary["ordering_so_far"][0] == "CE",
      "so the ordering is shown but the preference is withheld")

many = []
for i in range(40):
    day = TS + i * 86400
    many.append(opt("GOLD", "CE", 0.5, ts=day))
    many.append(opt("GOLD", "PE", -0.4, ts=day))
enough = vehicle.by_instrument(vehicle.pair(many, []))[0]
check(enough["enough_to_prefer_a_vehicle"] and enough["preferred_vehicle"] == "CE",
      "with enough same-signal comparisons a preference is stated")
check(enough["by_vehicle"]["FUTURES"]["resolved"] == 0
      and enough["missing_because"].get("FUTURES_NOT_CAPTURED") == 40,
      "and the vehicle that was never captured says so, 40 times")
families = vehicle.by_family(vehicle.pair(many, []))
check([r["instrument"] for r in families[fam.MCX]] == ["GOLD"]
      and not families[fam.STOCK],
      "vehicle results stay grouped by family")

# ------------------------------------------------- direction study + coverage
pool = [*book("NIFTY", 200, lambda i: 0.5 if i % 2 else -0.5),
        *book("TCS", 200, lambda i: -0.4 if i % 4 else 1.0)]
rep = direction.report(pool)
check(len(rep["per_instrument"]) == 2, "the direction study is per instrument")
check(rep["pooled_statistic"] == "REFUSED", "and refuses a pooled number")
check(all(r["claims"] == "UNDERLYING_DIRECTION_ONLY" for r in rep["per_instrument"]),
      "every row states that it is direction evidence only")
cov = rep["coverage"]
check(cov["measured_count"] == 2 and cov["universe"] > 50,
      "coverage says how much of the universe the pool actually measured")
check("CRUDEOIL" in cov["cannot_be_measured_on_these_terms"],
      "MCX is listed as unmeasurable on these terms, not as untested")
check("SBIN" in cov["not_collected_yet"],
      "an equity with a cash series but no rows yet is listed as uncollected")
check(not cov["off_universe_in_pool"], "and nothing off-universe is smuggled in")

# ----------------------------------------------------------- 8. no order path
ORDER = re.compile(r"place_order|placeOrder|\bbuy\(|modify_order|cancel_order")
for path in sorted(pathlib.Path("app/research/phase20").glob("*.py")):
    check(not ORDER.search(path.read_text()),
          f"{path.name} contains no order call")

print(f"checked {CHECKS}")
if FAILURES:
    for f in FAILURES:
        print(f"FAIL: {f}")
    raise SystemExit(1)
print("phase20 smoke: OK")
