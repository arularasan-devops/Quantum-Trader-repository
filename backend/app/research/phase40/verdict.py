"""Phase 40 §13 — the one classification, from measured shares only.

The rule was written down before the first run and contains no invented
percentage. It compares shares that were measured, and asks of each comparison
only whether the difference is larger than sampling noise at a declared,
conventional deviate (:data:`app.research.phase40.MATERIALITY_Z`):

* **ENTRY / DIRECTION** — adverse-first is the larger share, materially. The
  loss happens before the money arrives, so no exit could have taken it.
* **EXIT / GIVEBACK** — favourable-first is the larger share materially, *and*
  among those legs the ones that ended below their own round-trip cost
  outnumber the ones that kept it, materially. Both halves are required: money
  that arrives first and is then kept is not a leak.
* **MIXED** — a difference exists but does not survive the test, or the money
  arrives first and is mostly retained, in which case the measured loss is not
  located by this diagnostic at all.
* **INSUFFICIENT_DATA** — fewer classified legs than Phase 39's own label
  floor, or more legs whose window the store could not cover than legs it could.

One honest limitation, stated here rather than in a footnote: the legs are
overlapping windows drawn mostly from a single session, so they are not
independent draws and the deviate below is optimistic. That is why the same
comparison is also computed on the non-overlapping subsample, and why a verdict
whose two versions disagree is reported as such instead of being resolved in
favour of the larger sample.
"""
from __future__ import annotations

from math import sqrt

from app.research.phase40 import (
    ENTRY_DOMINANT,
    EXIT_DOMINANT,
    INSUFFICIENT,
    MATERIALITY_Z,
    MIN_CLASSIFIED_FOR_VERDICT,
    MIXED,
    UNCOVERED_WINDOW,
)
from app.research.phase40 import metrics as m


def sign_test(a: int, b: int) -> dict:
    """Two-sided deviate for ``a`` against ``b`` under an even split.

    A sign test on the subset where one of the two events happened: if neither
    ordering were favoured, each of the ``a + b`` legs would fall either way
    with equal chance. Returns the deviate rather than a verdict, so the caller
    shows its arithmetic.
    """
    n = a + b
    if n <= 0:
        return {"n": 0, "z": None, "material": False}
    z = (a - b) / sqrt(n)
    return {
        "n": n,
        "share_pct": round(100.0 * a / n, 2),
        "z": round(z, 3),
        "material": abs(z) >= MATERIALITY_Z,
        "z_threshold": MATERIALITY_Z,
    }


def classify(legs: list[dict], *, horizon: str) -> dict:
    """The verdict for one population at one horizon, with its evidence."""
    row = m.horizon_row(legs, horizon=horizon)
    give = m.giveback_row(legs, horizon=horizon)
    counts = row["counts"]
    classified = row["classified"]
    uncovered = counts[UNCOVERED_WINDOW]

    race = sign_test(counts["FAVOURABLE_FIRST"], counts["ADVERSE_FIRST"])
    keep = sign_test(give["FAVOURABLE_FIRST_BUT_GIVEN_BACK"],
                     give["FAVOURABLE_FIRST_AND_RETAINED"])

    if classified < MIN_CLASSIFIED_FOR_VERDICT or uncovered > classified:
        label = INSUFFICIENT
        because = (
            f"{classified} classified leg(s) against a floor of "
            f"{MIN_CLASSIFIED_FOR_VERDICT}, and {uncovered} leg(s) whose "
            f"window the store could not cover"
        )
    elif not race["material"]:
        label = MIXED
        because = (
            f"favourable-first {counts['FAVOURABLE_FIRST']} against "
            f"adverse-first {counts['ADVERSE_FIRST']} is z={race['z']}, "
            f"inside the declared {MATERIALITY_Z}"
        )
    elif counts["ADVERSE_FIRST"] > counts["FAVOURABLE_FIRST"]:
        label = ENTRY_DOMINANT
        because = (
            f"adverse-first {row['adverse_first_pct']}% against "
            f"favourable-first {row['favourable_first_pct']}% of "
            f"{classified} classified legs, z={race['z']}"
        )
    elif keep["material"] and (
        give["FAVOURABLE_FIRST_BUT_GIVEN_BACK"]
        > give["FAVOURABLE_FIRST_AND_RETAINED"]
    ):
        label = EXIT_DOMINANT
        because = (
            f"favourable-first {row['favourable_first_pct']}% of "
            f"{classified} classified legs (z={race['z']}), and "
            f"{give['given_back_pct']}% of those ended below their own round "
            f"trip (z={keep['z']}), median "
            f"{give['given_back_fraction_of_mfe_pct_median']}% of the peak "
            f"given back"
        )
    else:
        label = MIXED
        because = (
            f"favourable-first leads at {row['favourable_first_pct']}% "
            f"(z={race['z']}), but of those "
            f"{give['retained_pct']}% kept the move and the giveback split is "
            f"z={keep['z']}, inside the declared {MATERIALITY_Z}"
        )
    return {
        "verdict": label,
        "because": because,
        "horizon": horizon,
        "classified": classified,
        "uncovered": uncovered,
        "sessions": row["sessions"],
        "favourable_first_pct": row["favourable_first_pct"],
        "adverse_first_pct": row["adverse_first_pct"],
        "neither_pct": row["neither_pct"],
        "race_test": race,
        "giveback_test": keep,
        "median_time_to_favourable_min": row["median_time_to_favourable_min"],
        "median_time_to_adverse_min": row["median_time_to_adverse_min"],
        "net_pct_median": row["net_pct_median"],
        "given_back_pct": give["given_back_pct"],
        "retained_pct": give["retained_pct"],
        "given_back_fraction_of_mfe_pct_median": (
            give["given_back_fraction_of_mfe_pct_median"]
        ),
    }
