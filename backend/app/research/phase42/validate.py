"""Phase 42 — choose a multiple where it cannot see the days it is judged on.

Seven arms measured on the same legs means the best of them is the maximum of
seven correlated figures, which is optimistic even when the arms are identical.
Two devices answer that, both of them chronological because a trading day is not
exchangeable with the day after it:

**One split.** The earliest :data:`DEV_SHARE` of sessions choose the multiple;
the remaining sessions score it, having taken no part in choosing it. Sessions
are never divided across the cut.

**Walk-forward.** The multiple is re-chosen on every prefix of sessions and
scored on the next one, which is the only form of this that resembles how a gate
would actually be changed: on what was known at the time. It also exposes the
failure a single split hides — a choice that keeps moving is not a finding, it is
noise being fitted, and that shows up here as a low agreement rate.
"""
from __future__ import annotations

from app.research.phase42 import (
    BASIS_CHANGED,
    BASIS_CONSISTENT,
    BASIS_NOTE,
    BETTER_THAN_LIVE,
    DEV_SHARE,
    INSUFFICIENT,
    LIVE_MULTIPLE,
    LOSS_REDUCTION_ONLY,
    MIN_ARM_LEGS,
    MIN_SESSION_LEGS,
    MIN_SESSIONS,
    MIN_SESSIONS_FOR_SPLIT,
    NO_BETTER_THAN_LIVE,
    POSITIVE_IN_HOLDOUT,
    THRESHOLDS,
    UNKNOWN_BASIS,
)
from app.research.phase42.arms import SessionResult, pooled


def _mean(result: SessionResult, multiple: float) -> float | None:
    stats = result.arms[multiple].admitted.stats()
    if stats["legs"] < MIN_ARM_LEGS or stats["mean_net_pct"] is None:
        return None
    return float(stats["mean_net_pct"])


def choose(results: list[SessionResult]) -> dict:
    """The arm with the best mean net over the given sessions.

    Ties break toward the *lower* multiple, and that is a deliberate asymmetry:
    a higher multiple refuses more legs, so preferring it on an equal figure
    would trade away trade count for nothing measurable.
    """
    if not results:
        return {"multiple": None, "mean_net_pct": None, "reason": INSUFFICIENT}
    pool = pooled(results)
    scored = [(m, _mean(pool, m)) for m in THRESHOLDS]
    usable = [(m, v) for m, v in scored if v is not None]
    if not usable:
        return {"multiple": None, "mean_net_pct": None, "reason": INSUFFICIENT}
    best = max(usable, key=lambda pair: (pair[1], -pair[0]))
    return {
        "multiple": best[0],
        "mean_net_pct": best[1],
        "reason": None,
        "candidates": [{"multiple": m, "mean_net_pct": v} for m, v in scored],
    }


def with_evidence(results: list[SessionResult]) -> list[SessionResult]:
    """The sessions that carry enough measurable legs to be split on.

    A session the capture reached but whose book was one-sided contributes no
    gradeable leg, and counting it toward the split floor lets an empty half be
    presented as the half a multiple was chosen on.
    """
    return [r for r in results if r.measured.legs >= MIN_SESSION_LEGS]


def cost_bases(results: list[SessionResult]) -> dict[str, int]:
    """How the round trip was established, over a set of sessions."""
    out: dict[str, int] = {}
    for result in results:
        for label, n in result.cost_bases.items():
            out[label] = out.get(label, 0) + n
    return out


def dominant_basis(tally: dict[str, int]) -> str | None:
    """The label most of the measured legs were costed under, or None."""
    if not tally:
        return None
    return max(sorted(tally), key=lambda label: tally[label])


def basis_across_cut(
    dev: list[SessionResult], holdout: list[SessionResult],
) -> dict:
    """Whether both halves of the cut had their round trip established alike.

    The ex-ante ratio divides by the leg's round-trip cost, so *how* that cost
    was arrived at is part of the quantity being thresholded. A store whose cost
    basis changed part-way through — a modelled spread before, a measured lot
    after — can put one basis on each side of a chronological cut, and then the
    difference between the halves is a difference in costing rather than in the
    multiple. This does not repair that: it makes it visible, and the verdict
    refuses to call such a comparison a test.
    """
    dev_tally, holdout_tally = cost_bases(dev), cost_bases(holdout)
    dev_top = dominant_basis(dev_tally)
    holdout_top = dominant_basis(holdout_tally)
    same = (dev_top is not None and dev_top == holdout_top
            and dev_top != UNKNOWN_BASIS)
    out = {
        "status": BASIS_CONSISTENT if same else BASIS_CHANGED,
        "dev": dict(sorted(dev_tally.items())),
        "holdout": dict(sorted(holdout_tally.items())),
        "dev_dominant": dev_top,
        "holdout_dominant": holdout_top,
    }
    if not same:
        out["because"] = BASIS_NOTE.format(dev=dev_top, holdout=holdout_top)
    return out


def split(results: list[SessionResult]) -> dict:
    """One chronological cut: choose on the early sessions, score on the late.

    Reports the chosen arm *and* the live multiple on the same holdout, because
    "better than nothing" is not the question — the question is whether it beats
    the multiple the engine already runs.
    """
    carrying = with_evidence(results)
    if len(carrying) < MIN_SESSIONS_FOR_SPLIT:
        return {
            "status": INSUFFICIENT,
            "sessions": len(carrying),
            "sessions_swept": len(results),
            "sessions_needed": MIN_SESSIONS_FOR_SPLIT,
            "sessions_carrying_evidence": [r.session for r in carrying],
            "because": (
                f"{len(carrying)} of {len(results)} swept session(s) carry at "
                f"least {MIN_SESSION_LEGS} legs whose economics could be "
                "measured, and a chronological holdout needs "
                f"{MIN_SESSIONS_FOR_SPLIT}: cutting on sessions that carry no "
                "gradeable leg leaves the evidence on one side of the cut"
            ),
        }
    cut = max(1, int(len(carrying) * DEV_SHARE))
    dev, holdout = carrying[:cut], carrying[cut:]
    picked = choose(dev)
    pool = pooled(holdout)
    rows = {
        m: pool.arms[m].admitted.stats() for m in THRESHOLDS
    }
    chosen = picked["multiple"]
    live = rows.get(LIVE_MULTIPLE)
    return {
        "status": "MEASURED",
        "sessions_swept": len(results),
        "sessions_carrying_evidence": len(carrying),
        "dev_sessions": [r.session for r in dev],
        "holdout_sessions": [r.session for r in holdout],
        "chosen_on_dev": picked,
        "holdout_all_arms": [{"multiple": m, **rows[m]} for m in THRESHOLDS],
        "holdout_chosen": rows.get(chosen) if chosen is not None else None,
        "holdout_live_multiple": live,
        "live_multiple": LIVE_MULTIPLE,
        "cost_basis_across_the_cut": basis_across_cut(dev, holdout),
    }


def walk_forward(results: list[SessionResult]) -> dict:
    """Re-choose on every prefix, score on the session that follows it."""
    carrying = with_evidence(results)
    if len(carrying) < MIN_SESSIONS_FOR_SPLIT:
        return {"status": INSUFFICIENT, "sessions": len(carrying),
                "sessions_swept": len(results)}
    steps: list[dict] = []
    for i in range(1, len(carrying)):
        picked = choose(carrying[:i])
        multiple = picked["multiple"]
        row = carrying[i]
        steps.append({
            "chose_on_sessions": [r.session for r in carrying[:i]],
            "tested_on": row.session,
            "multiple": multiple,
            "next_session_mean_net_pct": (
                row.arms[multiple].admitted.stats()["mean_net_pct"]
                if multiple is not None else None
            ),
            "live_multiple_mean_net_pct": (
                row.arms[LIVE_MULTIPLE].admitted.stats()["mean_net_pct"]
                if LIVE_MULTIPLE in row.arms else None
            ),
        })
    picks = [s["multiple"] for s in steps if s["multiple"] is not None]
    beat = [
        s for s in steps
        if s["next_session_mean_net_pct"] is not None
        and s["live_multiple_mean_net_pct"] is not None
    ]
    won = sum(1 for s in beat
              if s["next_session_mean_net_pct"] > s["live_multiple_mean_net_pct"])
    return {
        "status": "MEASURED",
        "steps": steps,
        "distinct_choices": sorted(set(picks)),
        "choice_stability_pct": (
            round(100.0 * picks.count(max(set(picks), key=picks.count))
                  / len(picks), 1) if picks else None
        ),
        "steps_where_choice_beat_live": won,
        "steps_comparable": len(beat),
    }


def verdict(one_split: dict, forward: dict) -> dict:
    """One label, taken from the holdout and never from the pooled figure.

    Three things can withhold the label, and each is reported as the reason
    rather than as a result. The holdout must exist; the sessions carrying it
    must reach the pre-committed :data:`MIN_SESSIONS` floor, which is declared
    and fingerprinted and was previously declared without being applied here, so
    a label was published on four sessions against a floor of fifty; and both
    halves of the cut must have been costed the same way, because the ratio
    divides by that cost.

    When a label is withheld the arithmetic is still returned, under
    ``provisional_label``, so the figure can be read as description without
    being read as a test.
    """
    if one_split.get("status") != "MEASURED":
        return {
            "verdict": INSUFFICIENT,
            "because": one_split.get("because", "no chronological holdout yet"),
        }
    chosen = one_split.get("holdout_chosen")
    live = one_split.get("holdout_live_multiple")
    if not chosen or chosen.get("mean_net_pct") is None:
        return {"verdict": INSUFFICIENT,
                "because": "the chosen arm has too few legs in the holdout"}
    mean = float(chosen["mean_net_pct"])
    labels = [POSITIVE_IN_HOLDOUT if mean > 0 else LOSS_REDUCTION_ONLY]
    if live and live.get("mean_net_pct") is not None:
        labels.append(BETTER_THAN_LIVE if mean > float(live["mean_net_pct"])
                      else NO_BETTER_THAN_LIVE)
    out = {
        "verdict": " / ".join(labels),
        "chosen_multiple": one_split["chosen_on_dev"]["multiple"],
        "holdout_mean_net_pct": mean,
        "live_holdout_mean_net_pct": (live or {}).get("mean_net_pct"),
        "walk_forward_choice_stability_pct": forward.get(
            "choice_stability_pct"),
        "sessions_carrying_evidence": one_split.get(
            "sessions_carrying_evidence"),
        "sessions_floor": MIN_SESSIONS,
        "because": (
            "the label comes from sessions the multiple was not chosen on; the "
            "pooled figure over all sessions is descriptive and is not a test"
        ),
    }
    withheld: list[str] = []
    carrying = one_split.get("sessions_carrying_evidence")
    if isinstance(carrying, int) and carrying < MIN_SESSIONS:
        withheld.append(
            f"{carrying} session(s) carry measurable legs against the "
            f"pre-committed floor of {MIN_SESSIONS}: four sessions can produce "
            "a holdout that is arithmetically clean and still be one regime",
        )
    basis = one_split.get("cost_basis_across_the_cut") or {}
    if basis.get("status") == BASIS_CHANGED:
        withheld.append(str(basis.get("because", BASIS_CHANGED)))
    if not withheld:
        return out
    return {
        "verdict": INSUFFICIENT,
        "provisional_label": out["verdict"],
        "chosen_multiple": out["chosen_multiple"],
        "holdout_mean_net_pct": mean,
        "live_holdout_mean_net_pct": out["live_holdout_mean_net_pct"],
        "walk_forward_choice_stability_pct": out[
            "walk_forward_choice_stability_pct"],
        "sessions_carrying_evidence": carrying,
        "sessions_floor": MIN_SESSIONS,
        "withheld_because": withheld,
        "because": (
            "the holdout arithmetic is reported as description only: "
            + "; ".join(withheld)
        ),
    }
