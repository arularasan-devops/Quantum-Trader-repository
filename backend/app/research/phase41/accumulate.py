"""Phase 41 — the same verdict per session, cumulatively, and stressed.

Every figure here comes from Phase 40's own :func:`classify` over subsets of one
pass of raced legs. Nothing is recomputed by a second implementation, because a
second implementation of a measurement is a second measurement.

Three views, because a single pooled number cannot tell them apart:

*Per session.* One session's answer, on that session's legs alone. Most will
read INSUFFICIENT_DATA, and that is the honest label for a day.

*Cumulative, in date order.* The answer as it would have read after one session,
two, three. A share drifting toward resolution and a share sitting at fifty-fifty
look identical in a single total and completely different here.

*Leave one out.* Each session removed in turn. A pooled verdict that survives
every removal is describing the market it sampled; one that flips when a single
day is dropped is describing that day, and the sequence view alone would not
have shown it.

The deviate is Phase 40's, applied to legs that overlap and therefore are not
independent draws — so all three views are repeated on the non-overlapping
subsample, and the caveat travels with the number rather than living in a
footnote.

Of those two frames, the non-overlapping one alone decides the published verdict.
The overlapping frame's deviate rises with the square root of how densely the
session was sampled, so at a fixed share it crosses any threshold eventually
without a single new fact arriving; it is carried as description and its deviate
is stamped :data:`Z_NOT_A_TEST`.
"""
from __future__ import annotations

from app.research.phase40 import (
    INSUFFICIENT,
    MIXED,
)
from app.research.phase40 import firstevent as fe
from app.research.phase40 import verdict as v
from app.research.phase41 import (
    DESCRIPTIVE_FRAME,
    DOMINATED,
    GOVERNING_FRAME,
    HORIZON,
    ONE_SESSION,
    REPORTING_RULE,
    STABLE,
    UNSTABLE,
    Z,
    Z_NOT_A_TEST,
)


def sessions_of(legs: list[dict]) -> list[str]:
    """Session labels in date order. Labels are ISO dates, so this sorts."""
    return sorted({str(x["session"]) for x in legs})


def _row(legs: list[dict], *, label: str, kind: str) -> dict:
    verdict = v.classify(legs, horizon=HORIZON)
    return {
        "label": label,
        "kind": kind,
        "sessions": len(sessions_of(legs)),
        "legs": len(legs),
        "verdict": verdict["verdict"],
        "classified": verdict["classified"],
        "uncovered": verdict["uncovered"],
        "favourable_first_pct": verdict["favourable_first_pct"],
        "adverse_first_pct": verdict["adverse_first_pct"],
        "race_z": verdict["race_test"]["z"],
        "race_material": verdict["race_test"]["material"],
        "giveback_pct": verdict["given_back_pct"],
        "giveback_z": verdict["giveback_test"]["z"],
        "giveback_material": verdict["giveback_test"]["material"],
        "net_pct_median": verdict["net_pct_median"],
        "because": verdict["because"],
    }


def per_session(legs: list[dict]) -> list[dict]:
    """One row per session, on that session's legs only."""
    return [
        _row([x for x in legs if str(x["session"]) == s],
             label=s, kind="SESSION")
        for s in sessions_of(legs)
    ]


def cumulative(legs: list[dict]) -> list[dict]:
    """One row per session, on every session up to and including it."""
    out: list[dict] = []
    seen: list[str] = []
    for s in sessions_of(legs):
        seen.append(s)
        out.append(_row(
            [x for x in legs if str(x["session"]) in seen],
            label=f"{seen[0]}..{s}", kind="CUMULATIVE",
        ))
    return out


def leave_one_out(legs: list[dict]) -> list[dict]:
    """The pooled answer with each session withheld in turn."""
    labels = sessions_of(legs)
    if len(labels) < 2:
        return []
    return [
        _row([x for x in legs if str(x["session"]) != s],
             label=f"without {s}", kind="LEAVE_ONE_OUT")
        for s in labels
    ]


def stability(pooled: dict, series: list[dict], dropped: list[dict]) -> dict:
    """How the verdict behaved as sessions were added and removed.

    A label about the *sequence*, not about the market: ``STABLE`` says the
    answer did not change while the sample grew, which is a fact about this
    store and not a promise about the next session. ``DOMINATED`` is the one
    that should stop a reader — it means the pooled answer is one session's
    answer wearing the sample's clothes.
    """
    verdicts = [r["verdict"] for r in series]
    settled = [x for x in verdicts if x != INSUFFICIENT]
    if not series:
        label = ONE_SESSION
        because = (
            "no session has produced a raceable leg, so there is no sequence "
            "and no answer to be stable about"
        )
    elif len(series) < 2:
        label = ONE_SESSION
        because = (
            "one session is a measurement, not a sequence: nothing can be "
            "said yet about whether the answer holds as the store deepens"
        )
    elif len(set(settled)) > 1:
        label = UNSTABLE
        because = (
            "the cumulative verdict read "
            + " then ".join(dict.fromkeys(settled))
            + " as sessions were added, so the pooled answer is not yet a "
              "property of the sample"
        )
    elif any(r["verdict"] != pooled["verdict"]
             and r["verdict"] != INSUFFICIENT for r in dropped):
        flips = [r["label"] for r in dropped
                 if r["verdict"] not in (pooled["verdict"], INSUFFICIENT)]
        label = DOMINATED
        because = (
            f"removing {', '.join(flips)} changes the pooled verdict, so it "
            f"rests on that session rather than on the sample"
        )
    else:
        label = STABLE
        because = (
            f"the cumulative verdict stayed {pooled['verdict']} as each "
            f"session was added, and removing any single session leaves it "
            f"unchanged"
        )
    return {"stability": label, "because": because,
            "cumulative_verdicts": verdicts}


def resolution(pooled: dict) -> dict:
    """How many classified legs the *current* split would need to resolve.

    Arithmetic, not a forecast, and it is easy to misread as one. The deviate
    grows with the square root of the count at a fixed share, so holding the
    observed share exactly and asking when |z| reaches the declared threshold
    gives ``n * (Z / z)^2``. Two things it does not say: the observed share is
    itself uncertain and will move, and if the true split is even it never
    resolves at any sample size — an even split is a finding, not a delay.
    """
    race = pooled.get("race_test") or {}
    n, z = race.get("n") or 0, race.get("z")
    if not n or z is None:
        return {
            "measurable": False,
            "note": "no legs have raced, so nothing can be projected",
        }
    if z == 0:
        return {
            "measurable": True, "already_material": False,
            "classified_now": n,
            "classified_needed_at_this_share": None,
            "multiple_of_current": None,
            "note": (
                f"the split is exactly even at n={n}. No sample size resolves "
                f"an even split, and an even split is itself a finding — no "
                f"usable timing difference in this mechanism — rather than a "
                f"delay"
            ),
        }
    if race.get("material"):
        return {
            "measurable": True, "already_material": True,
            "note": (
                f"the split is already outside the declared {Z} at n={n}"
            ),
        }
    needed = int(n * (Z / abs(z)) ** 2) + 1 if z else None
    return {
        "measurable": True,
        "already_material": False,
        "classified_now": n,
        "classified_needed_at_this_share": needed,
        "multiple_of_current": round(needed / n, 1) if needed else None,
        "note": (
            "arithmetic at the share observed today, not a prediction: the "
            "share will move as sessions arrive, and an even split never "
            "reaches the threshold at any sample size"
        ),
    }


def build(legs: list[dict]) -> dict:
    """The accumulation view, over all legs and over the independent subset.

    Both frames are computed identically and in full. The difference between
    them is not in the arithmetic but in what the caller may do with it: only
    :data:`GOVERNING_FRAME` supplies ``verdict``, and the other frame's deviate
    carries :data:`Z_NOT_A_TEST` so that no reader has to remember the caveat.
    """
    indep = fe.independent(legs, horizon=HORIZON)
    out: dict = {}
    for name, rows in ((DESCRIPTIVE_FRAME, legs), (GOVERNING_FRAME, indep)):
        pooled = v.classify(rows, horizon=HORIZON)
        series = cumulative(rows)
        dropped = leave_one_out(rows)
        governs = name == GOVERNING_FRAME
        out[name] = {
            "frame": name,
            "governs_verdict": governs,
            "z_valid_for_testing": governs,
            "z_caveat": None if governs else Z_NOT_A_TEST,
            "pooled": pooled,
            "per_session": per_session(rows),
            "cumulative": series,
            "leave_one_out": dropped,
            "stability": stability(pooled, series, dropped),
            "resolution": resolution(pooled),
        }
    agree = (
        out[DESCRIPTIVE_FRAME]["pooled"]["verdict"]
        == out[GOVERNING_FRAME]["pooled"]["verdict"]
    )
    out["frames_agree"] = agree
    out["frames_note"] = (
        "The overlapping and non-overlapping frames give the same verdict, so "
        "the answer does not depend on counting the same price swing twice."
        if agree else
        "The two frames disagree. The non-overlapping figure is the one to "
        "trust: overlapping windows drawn from one session are not "
        "independent draws, and the deviate over them is optimistic."
    )
    gov = out[GOVERNING_FRAME]
    out["reporting_rule"] = REPORTING_RULE
    out["governing_frame"] = GOVERNING_FRAME
    out["verdict"] = gov["pooled"]["verdict"]
    out["verdict_because"] = gov["pooled"]["because"]
    out["stability"] = gov["stability"]
    out["resolution"] = gov["resolution"]
    out["descriptive_frame"] = DESCRIPTIVE_FRAME
    out["descriptive_verdict"] = out[DESCRIPTIVE_FRAME]["pooled"]["verdict"]
    out["governing_note"] = (
        "The verdict is the non-overlapping one. The all-legs figures describe "
        "every leg the engine could have taken and are not a test: their "
        "deviate grows with how densely each session was sampled."
    )
    out["still_unresolved"] = (
        gov["pooled"]["verdict"] in (MIXED, INSUFFICIENT)
    )
    return out
