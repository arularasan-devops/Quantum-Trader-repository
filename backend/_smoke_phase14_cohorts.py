"""Does the cohort study earn the right to point at a subset of the book?

The failure mode here is not a crash, it is a convincing false positive: slice a
flat book forty ways and something always looks good. These checks pin the
properties that keep the study honest — only entry-time fields are sliced, a
cohort needs the trade count in BOTH periods, the holdout bar is a real bar and
not merely "above zero", and the number of comparisons made is reported so a
lucky cohort cannot be quoted as a finding.
"""
from __future__ import annotations

import random
import sys

from app.research.phase14 import cohorts

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
            "entry_minute_ist": 100, "risk_over_noise": 2.5}
    base.update(kw)
    return base


def main() -> int:
    random.seed(11)

    # --- a flat book must not produce a winner ----------------------------
    def coin_flip(n: int) -> list[dict]:
        # Independent random outcomes, so no cohort has a real edge; anything the
        # study reports here is a false positive it should have suppressed.
        return [trade(random.choice((1.0, -1.0)),
                      confidence=random.choice((35.0, 45.0, 55.0, 65.0, 85.0)),
                      entry_minute_ist=random.randrange(375),
                      risk_over_noise=round(random.uniform(0.4, 5.0), 2))
                for _ in range(n)]

    flat_early, flat_late = coin_flip(4000), coin_flip(4000)
    flat = cohorts.study(flat_early, flat_late, min_trades=50)
    ok(flat["candidates"] == [],
       f"a coin-flip book must yield no surviving cohort, got "
       f"{[c['cohort'] for c in flat['candidates']]}")
    ok("no cohort survived" in flat["verdict"],
       f"the verdict must say so plainly, got {flat['verdict']}")
    ok(flat["cohorts_tested"] > 0 and flat["expected_false_positives"] > 0,
       "the number of comparisons and the chance rate must both be reported")

    # --- a real subset edge must be found in both periods -----------------
    def book(edge_r: float, n: int = 400) -> list[dict]:
        rows = []
        for i in range(n):
            # the good cohort: high confidence, wide stop
            rows.append(trade(edge_r if i % 3 else -1.0, confidence=85.0,
                              risk_over_noise=3.0))
            rows.append(trade(1.0 if i % 2 else -1.0, confidence=35.0,
                              risk_over_noise=0.6))
        return rows

    found = cohorts.study(book(1.6), book(1.6), min_trades=100)
    labels = {c["cohort"] for c in found["candidates"]}
    ok(any(lab.startswith("conf:80") for lab in labels),
       f"the profitable confidence cohort must be surfaced, got {labels}")
    ok(not any(lab.startswith("conf:30") for lab in labels),
       f"the break-even cohort must not be surfaced, got {labels}")
    ok("hypotheses to confirm" in found["verdict"],
       f"a survivor must be labelled a hypothesis, not a finding: {found['verdict']}")

    # --- in-sample-only luck must be rejected ----------------------------
    lucky = cohorts.study(book(1.6), book(-0.2), min_trades=100)
    ok(lucky["candidates"] == [],
       "a cohort that only works in-sample must be rejected")

    # --- a cohort thin in either period is not quoted --------------------
    thin_late = [t for t in book(1.6) if t["confidence"] < 50][:400]
    thin = cohorts.study(book(1.6), thin_late, min_trades=100)
    ok(not any(c["cohort"].startswith("conf:80") for c in thin["candidates"]),
       "a cohort absent from holdout may not survive on in-sample size alone")
    rows = {r["cohort"]: r for d in thin["dimensions"] if d["dimension"] == "confidence"
            for r in d["cohorts"]}
    ok(rows["conf:80-89"]["enough_trades"] is False,
       "a cohort thin in one period must be marked as such, not silently dropped")

    # --- merely non-negative is not enough: the live trade pays a spread --
    # 2 wins of 0.545R against 1 loss of 1R is +0.03R: positive, and still less
    # than the spread a real option entry would pay.
    marginal = cohorts.study(book(0.545), book(0.545), min_trades=100)
    conf_rows = {r["cohort"]: r for d in marginal["dimensions"]
                 if d["dimension"] == "confidence" for r in d["cohorts"]}
    hi = conf_rows["conf:80-89"]
    ok(0 < hi["holdout"]["expectancy_r"] < cohorts.MIN_HOLDOUT_EXPECTANCY,
       f"fixture must be barely positive, got {hi['holdout']['expectancy_r']}")
    ok(hi["survives_holdout"] is False,
       "a barely-positive cohort must not survive — spread would eat it")

    # --- nothing may be sliced on the outcome ----------------------------
    outcome_fields = {"r", "mfe_r", "mae_r", "exit_reason", "exit_price",
                      "exit_ts", "minutes", "bars_held"}
    probe = trade(1.0)
    for dim, key in cohorts.DIMENSIONS.items():
        stripped = {k: v for k, v in probe.items() if k not in outcome_fields}
        try:
            label = key(stripped)
        except KeyError as exc:  # pragma: no cover - the assertion is the message
            raise AssertionError(
                f"dimension {dim} reads outcome field {exc} — that is lookahead"
            ) from exc
        ok(isinstance(label, str) and label,
           f"dimension {dim} must label a trade from entry-time fields alone")

    # --- unknown/missing entry data is labelled, not guessed -------------
    blank = cohorts.study(
        [trade(1.0, confidence=None, entry_minute_ist=None, risk_over_noise=None,
               htf_trend=None, htf_strength=None)] * 200,
        [trade(-1.0, confidence=None, entry_minute_ist=None, risk_over_noise=None,
               htf_trend=None, htf_strength=None)] * 200,
        min_trades=10)
    labels = {r["cohort"] for d in blank["dimensions"] for r in d["cohorts"]}
    ok("conf:unknown" in labels and "tod:unknown" in labels,
       f"missing entry data must be its own cohort, got {labels}")
    ok("stop/noise:unknown" in labels and "htf:flat/none" in labels,
       f"missing stop/HTF data must not be bucketed as a real value: {labels}")

    # --- share of book is reported, so a 2% cohort cannot pose as a fix --
    shares = [r["share_of_holdout_pct"] for d in flat["dimensions"]
              for r in d["cohorts"]]
    ok(all(0.0 <= s <= 100.0 for s in shares), "cohort shares must be percentages")
    ok(cohorts.study([], [], min_trades=10)["candidates"] == [],
       "an empty study must not raise")

    print(f"checked {CHECKS}")
    print("phase 14 cohort smoke: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
