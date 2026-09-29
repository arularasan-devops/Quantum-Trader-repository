"""A+ grading A/B — is a negative historical prior a *validated* blocker?

RESEARCH ONLY. This module grades rows that were already recorded. It is never
imported by the signal path, it never writes a paper leg, and it changes no
threshold: ``aplus`` remains the single grader of record.

Two research questions, both raised by the grading audit.

**1. Should an unvalidated prior refuse an instrument?**
``aplus._label`` refuses any instrument whose ``PRIOR_DEV_R`` is negative, before
the book is looked at. Eight of eleven instruments are refused that way, one of
them (FINNIFTY) on -0.0010R. ``aplus.PRIOR_BASIS`` itself says the priors are
``UNDERLYING_ONLY_NOT_SIGNIFICANT`` — they did not clear the multiple-testing
bar. So the module documents the number as an ordering device while the code uses
it as a binary veto. This grades both policies over one pool:

* **v1** — negative prior refuses the instrument (today's behaviour).
* **v2** — a negative prior is binding only where the pool *validates* it; where
  it does not, the prior stays informational and the candidate is graded on its
  book, entry and room like any other.

v2 is deliberately not "admit all eight". Three outcomes, not two:

* the pool **supports** the negative prior — v2 keeps the veto;
* the pool **tested it and cannot support it** — v2 lifts the veto;
* the pool **never measured that instrument** — v2 leaves the veto exactly where
  it is. Lifting it there would be admission on the *absence* of evidence, which
  is the same error as the veto itself, pointed the other way.

Supported means out-of-sample, not in-sample: a mean that clears the bar on the
pool as a whole, holds its sign on a chronological holdout no development row
touched, and does not flip fold to fold. One pooled mean over five years is a
single in-sample number, and vetoing an instrument for five years on one
in-sample number is how the current rule came to be.

**2. Is "unmeasured" the same as "bad"?**
A+ requires zero unmeasured components, so a candidate that is excellent on
every measured axis is refused identically to a genuinely poor one while its
premium history warms up. That is indistinguishable on a tally and, live, at the
open it is the normal state. The two are separated here:

* ``A_PLUS_READY`` — every gate clear, every component measured.
* ``A_PLUS_PENDING_DATA`` — no gate fails; something is not measured yet.
* ``A_PLUS_REJECTED`` — at least one gate refuses it on its merits.

PENDING_DATA is a research state, not a promotion state: it is not A+, it never
enters the costed book, and it is never counted toward a promotion sample.
"""
from __future__ import annotations

import math

from app.research.phase15 import folds as p15folds
from app.research.phase17 import aplus as p17aplus
from app.research.phase17 import economics as p17econ
from app.research.phase17 import entry as p17entry
from app.research.phase17 import quality as p17quality
from app.research.phase17 import reach as p17reach
from app.research.phase17 import schema as p17schema

# Gate names. These mirror the branch order inside ``aplus._label`` exactly, and
# ``ORDER`` is what lets the audit prove the mirroring rather than assume it.
G_DATA = "DATA_QUALITY_UNUSABLE"
G_NO_ECON = "NO_ECONOMICS"
G_VEHICLE_RED = "VEHICLE_RED"
G_COST_UNKNOWN = "COST_UNKNOWN"
G_SPREAD = "SPREAD_OVER_RED"
G_ENTRY_CHASED = "ENTRY_CHASED"
G_ROOM = "T1_RANK_D_OR_E"
G_MARKET_PRIOR = "INSTRUMENT_PRIOR_NEGATIVE"
G_MISSING = "COMPONENT_UNMEASURED"
G_SCORE = "SCORE_BELOW_A_PLUS_FLOOR"

ORDER: tuple[str, ...] = (
    G_DATA, G_VEHICLE_RED, G_COST_UNKNOWN, G_NO_ECON, G_SPREAD, G_ENTRY_CHASED,
    G_ROOM, G_MARKET_PRIOR, G_MISSING, G_SCORE,
)

# Which label ``_label`` writes when each gate is the first to fail. G_MISSING
# and G_SCORE map to no single label — below the floor the engine writes WATCH,
# REJECT_ROOM or REJECT_DATA depending on the score — so they are absent here and
# treated as unmapped rather than as a disagreement.
GATE_LABEL: dict[str, str] = {
    G_DATA: p17aplus.REJECT_DATA,
    G_NO_ECON: p17aplus.REJECT_DATA,
    G_COST_UNKNOWN: p17aplus.REJECT_DATA,
    G_VEHICLE_RED: p17aplus.REJECT_VEHICLE,
    G_SPREAD: p17aplus.REJECT_SPREAD,
    G_ENTRY_CHASED: p17aplus.REJECT_ENTRY,
    G_ROOM: p17aplus.REJECT_ROOM,
    G_MARKET_PRIOR: p17aplus.REJECT_MARKET,
}

# The gates that judge the candidate. G_MISSING is excluded on purpose: it says
# "not known yet", which is what PENDING_DATA exists to express.
MERIT_GATES: frozenset[str] = frozenset(ORDER) - {G_MISSING}

V1 = "V1_PRIOR_BINDING"
V2 = "V2_PRIOR_INFORMATIONAL_UNLESS_VALIDATED"
VERSIONS: tuple[str, str] = (V1, V2)

READY = "A_PLUS_READY"
PENDING_DATA = "A_PLUS_PENDING_DATA"
REJECTED = "A_PLUS_REJECTED"
STATES: tuple[str, ...] = (READY, PENDING_DATA, REJECTED)
# How an opportunity is summarised from its ticks: by its best state.
RANK: dict[str, int] = {READY: 3, PENDING_DATA: 2, REJECTED: 1}

# The six verdicts, defined next to the prior they describe so grader and A/B
# cannot drift apart. "NOT_VALIDATED" is gone on purpose: it merged a mean the
# pool cannot separate from zero with one that is plainly negative and merely
# missed a deliberately strict bar, and reading the second as the first is how an
# instrument with a 0.93 PF holdout would get re-admitted as "fine".
VALIDATED_NEGATIVE = p17aplus.VALIDATED_NEGATIVE
NEGATIVE_BUT_UNDER_BAR = p17aplus.NEGATIVE_BUT_UNDER_BAR
INDISTINGUISHABLE_FROM_ZERO = p17aplus.INDISTINGUISHABLE_FROM_ZERO
POSITIVE_BUT_UNDER_BAR = p17aplus.POSITIVE_BUT_UNDER_BAR
VALIDATED_POSITIVE = p17aplus.VALIDATED_POSITIVE
INSUFFICIENT_DATA = p17aplus.INSUFFICIENT_DATA
VERDICTS: tuple[str, ...] = p17aplus.PRIOR_LABELS
# Which verdicts lift a refusal, derived from the grader's own veto policy rather
# than restated here, so the A/B cannot answer a different question than the one
# the grader acts on. INSUFFICIENT_DATA is excluded from both sides: an instrument
# the pool never measured keeps whatever production has, since absence of evidence
# is not evidence in either direction.
VETO_LIFTED_ON: frozenset[str] = frozenset(
    v for v in VERDICTS
    if v not in p17aplus.VETO_LABELS and v != INSUFFICIENT_DATA
)

# The multiple-testing bar the priors were measured against, kept identical to
# the one Phase 15B applied: 218 comparisons, so a nominal 5% two-sided test needs
# roughly 3.68 sigma before one instrument's mean may be called negative.
COMPARISONS = 218
SIGMA_BAR = 3.68
NOMINAL_SIGMA = 1.96
# Below this many outcomes the sign of a mean R is not worth a verdict either way.
MIN_VALIDATION_ROWS = 30
# Out-of-sample bars. The holdout gets the same floor as the pool — a holdout of
# nine rows cannot confirm anything, and reading one as confirmation is how an
# in-sample number acquires an out-of-sample certificate it did not earn. Folds
# are allowed to be smaller, and a fold under the floor is counted as unmeasured
# rather than as agreeing with its neighbours.
MIN_HOLDOUT_ROWS = 30
MIN_FOLD_ROWS = 20
FOLDS = 5

# Every instrument whose prior is negative at all — the v1 policy, kept explicit
# so the A/B can still replay it now that the grader itself no longer refuses on
# the sign alone.
ALL_NEGATIVE_PRIORS: frozenset[str] = frozenset(
    name for name, val in p17aplus.PRIOR_DEV_R.items() if float(val) < 0
)


def gates_failed(row: dict, *, binding_negative: frozenset[str] | None = None
                 ) -> list[str]:
    """Every gate this recorded tick fails, independent of the check order.

    The audit's core move: ``_label`` stops at the first failure, so a row
    labelled REJECT_MARKET may also have been unfillable and a row labelled
    REJECT_DATA may have been fine on everything else. Only evaluating all ten
    separates "binding constraint" from "first in the list".

    ``binding_negative`` selects the policy. ``None`` follows the grader of
    record, so an audit of recorded rows reproduces the labels they were written
    with. A set overrides it: ``ALL_NEGATIVE_PRIORS`` replays v1, any smaller set
    replays a candidate v2 policy.
    """
    out: list[str] = []
    econ = row.get("economics") if isinstance(row.get("economics"), dict) else None
    ent = row.get("entry") if isinstance(row.get("entry"), dict) else None
    rch = row.get("reach") if isinstance(row.get("reach"), dict) else None
    ap = row.get("aplus") if isinstance(row.get("aplus"), dict) else {}

    if not p17quality.usable(str(row.get("data_quality") or p17quality.MISSING)):
        out.append(G_DATA)
    if econ is None:
        out.append(G_NO_ECON)
    else:
        if econ.get("vehicle_class") == p17econ.RED:
            out.append(G_VEHICLE_RED)
        if econ.get("cost_status") != p17schema.COST_MEASURED:
            out.append(G_COST_UNKNOWN)
        spct = econ.get("spread_pct")
        if isinstance(spct, (int, float)) and float(spct) > p17econ.RED_SPREAD_PCT:
            out.append(G_SPREAD)
    if ent and ent.get("entry_quality") in (
        p17entry.CHASED, p17entry.SEVERELY_CHASED
    ):
        out.append(G_ENTRY_CHASED)
    if rch and rch.get("t1_rank") in (p17reach.RANK_D, p17reach.RANK_E):
        out.append(G_ROOM)
    inst = str(row.get("instrument") or "").upper()
    if binding_negative is None:
        vetoed = p17aplus.prior_vetoes(inst)[0]
    else:
        prior = p17aplus.PRIOR_DEV_R.get(inst)
        vetoed = (prior is not None and float(prior) < 0
                  and inst in binding_negative)
    if vetoed:
        out.append(G_MARKET_PRIOR)
    if ap.get("missing_components"):
        out.append(G_MISSING)
    score = ap.get("a_plus_score")
    if not isinstance(score, (int, float)) or float(score) < p17aplus.A_PLUS_MIN_SCORE:
        out.append(G_SCORE)
    return out


def first_gate(failed: list[str]) -> str | None:
    for g in ORDER:
        if g in failed:
            return g
    return None


def state_of(failed: list[str]) -> str:
    """READY / PENDING_DATA / REJECTED for one already-evaluated tick.

    A row that fails only because a component is unmeasured is PENDING_DATA even
    though its score is under the floor: an unmeasured component contributes 0 to
    the weighted score, so G_SCORE there is a consequence of the same missing
    input and not a second, independent verdict.
    """
    merits = [g for g in failed if g in MERIT_GATES and g != G_SCORE]
    if merits:
        return REJECTED
    if G_MISSING in failed:
        return PENDING_DATA
    return REJECTED if G_SCORE in failed else READY


def _mean_sd(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(var)


def validate_prior(name: str, r_values: list[float],
                   target_hits: int | None = None) -> dict:
    """Does the pool actually support this instrument's prior being negative?

    The prior is a mean underlying R. Whether it may veto an instrument is a
    question about the mean's standard error and the number of comparisons the
    search made, not about its sign — which is precisely what the current gate
    reads. No verdict here claims an instrument is good: the strongest thing the
    pool can say in an instrument's favour is that its mean is indistinguishable
    from zero, which makes a *veto* unsupported and nothing more.
    """
    prior = p17aplus.PRIOR_DEV_R.get(name.upper())
    n = len(r_values)
    mean, sd = _mean_sd(r_values)
    se = sd / math.sqrt(n) if n > 1 and sd > 0 else None
    sigma = abs(mean) / se if se else None
    wins = sum(1 for v in r_values if v > 0)
    gross_win = sum(v for v in r_values if v > 0)
    gross_loss = -sum(v for v in r_values if v < 0)
    if n < MIN_VALIDATION_ROWS or sigma is None:
        verdict = INSUFFICIENT_DATA
    elif sigma >= SIGMA_BAR:
        verdict = VALIDATED_NEGATIVE if mean < 0 else VALIDATED_POSITIVE
    elif sigma >= NOMINAL_SIGMA:
        # Under the multiple-testing bar but not inside the noise either: the sign
        # is readable at the nominal level, so it is reported as a direction that
        # missed the bar rather than as "no finding".
        verdict = NEGATIVE_BUT_UNDER_BAR if mean < 0 else POSITIVE_BUT_UNDER_BAR
    else:
        verdict = INDISTINGUISHABLE_FROM_ZERO
    return {
        "instrument": name.upper(),
        "prior_dev_r": prior,
        "pool_rows": n,
        "pool_mean_r": round(mean, 4),
        "pool_sd_r": round(sd, 4),
        "sigma": round(sigma, 2) if sigma is not None else None,
        "sigma_bar": SIGMA_BAR,
        "comparisons": COMPARISONS,
        "clears_nominal_sigma": bool(sigma is not None and sigma >= NOMINAL_SIGMA),
        "win_rate_pct": round(100.0 * wins / n, 2) if n else None,
        "t1_before_sl_pct": (
            round(100.0 * target_hits / n, 2)
            if target_hits is not None and n else None
        ),
        "profit_factor": (
            round(gross_win / gross_loss, 2) if gross_loss > 0 else None
        ),
        "verdict": verdict,
        "negative_veto_supported": verdict == VALIDATED_NEGATIVE,
        # Whether the refusal survives is a wider question than whether the mean is
        # validated: a negative that only missed the bar keeps it too.
        "veto_retained_under_v2": _retains_veto(prior, verdict),
        "basis": "UNDERLYING_ONLY",
    }


def _rows_r(rows: list[dict]) -> tuple[list[float], int]:
    vals: list[float] = []
    hits = 0
    for r in rows:
        val = r.get("r")
        if not isinstance(val, (int, float)):
            continue
        vals.append(float(val))
        if str(r.get("exit_reason") or "").upper() == "TARGET":
            hits += 1
    return vals, hits


def _pf_and_drawdown(vals: list[float]) -> tuple[float | None, float | None]:
    """Profit factor and worst peak-to-trough drawdown, in R, over the sequence.

    Order matters for the drawdown, so the rows must already be chronological.
    """
    if not vals:
        return None, None
    win = sum(v for v in vals if v > 0)
    loss = -sum(v for v in vals if v < 0)
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for v in vals:
        equity += v
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return (round(win / loss, 2) if loss > 0 else None), round(worst, 4)


def split_chronological(rows: list[dict], *, dev_share: float = 0.5,
                        validation_share: float = 0.2) -> dict:
    """Development / validation / holdout, cut on session boundaries.

    Two candidates from the same morning are not independent observations, so a
    cut that lands mid-session lets development see the afternoon of a session the
    holdout is then graded on. The holdout is the last block of sessions and is
    read once, at the end — it is not a third place to search.
    """
    sessions = sorted({str(r.get("session")) for r in rows if r.get("session")})
    n = len(sessions)
    if n < 3:
        return {"development": list(rows), "validation": [], "holdout": [],
                "sessions": n, "note": ("the pool spans fewer than three "
                                        "sessions, so it cannot be cut")}
    d_cut = max(1, int(n * dev_share))
    v_cut = max(d_cut + 1, min(n - 1, int(n * (dev_share + validation_share))))
    dev_names = set(sessions[:d_cut])
    val_names = set(sessions[d_cut:v_cut])
    out: dict = {"development": [], "validation": [], "holdout": []}
    for r in rows:
        s = str(r.get("session"))
        key = ("development" if s in dev_names
               else "validation" if s in val_names else "holdout")
        out[key].append(r)
    out["sessions"] = n
    out["session_spans"] = {
        "development": [sessions[0], sessions[d_cut - 1]],
        "validation": [sessions[d_cut], sessions[v_cut - 1]],
        "holdout": [sessions[v_cut], sessions[-1]],
    }
    out["note"] = ("cut on session boundaries, oldest first; no session "
                   "contributes rows to two periods")
    return out


def walk_forward(rows: list[dict], *, folds: int = FOLDS) -> dict:
    """Does the sign hold fold to fold, or is it one period carrying the mean?

    A prior that is negative overall because a single stretch of the history was
    negative is not a property of the instrument. Folds too small to read are
    reported as unmeasured rather than counted as agreeing.
    """
    slices = p15folds.split_folds(rows, folds) if len(rows) >= folds else []
    out = []
    for i, part in enumerate(slices, start=1):
        vals, _ = _rows_r(part)
        mean, _sd = _mean_sd(vals)
        measurable = len(vals) >= MIN_FOLD_ROWS
        out.append({
            "fold": i,
            "rows": len(vals),
            "mean_r": round(mean, 4) if vals else None,
            "sign": None if not measurable else ("NEG" if mean < 0 else "POS"),
            "measurable": measurable,
        })
    measured = [f for f in out if f["measurable"]]
    negatives = [f for f in measured if f["sign"] == "NEG"]
    if len(measured) < 2:
        verdict = INSUFFICIENT_DATA
    elif len(negatives) == len(measured):
        verdict = "NEGATIVE_IN_EVERY_MEASURED_FOLD"
    elif negatives:
        verdict = "SIGN_FLIPS_ACROSS_FOLDS"
    else:
        verdict = "NEVER_NEGATIVE_IN_A_MEASURED_FOLD"
    return {
        "folds": out,
        "folds_requested": folds,
        "folds_measured": len(measured),
        "folds_negative": len(negatives),
        "stable_negative": verdict == "NEGATIVE_IN_EVERY_MEASURED_FOLD",
        # The positive case is the one a research universe is tempted to wave
        # through, so it is held to the same fold agreement as a veto.
        "stable_positive": verdict == "NEVER_NEGATIVE_IN_A_MEASURED_FOLD",
        "verdict": verdict,
    }


def validate_instrument(name: str, rows: list[dict], *,
                        folds: int = FOLDS) -> dict:
    """One instrument's prior, tested in-sample, out-of-sample and fold by fold.

    A verdict survives only if all three agree: the pooled mean clears the
    multiple-testing bar, the holdout keeps the sign on rows no development period
    touched, and no measured fold flips. Any one of those failing leaves the pool
    unable to support the finding — which is a statement about the evidence, never
    a claim in the other direction.

    The rule is symmetric. A validated negative is a five-year refusal; a validated
    positive is a claim that a market has direction, and Phase 20 ranks a research
    universe on these labels, so a positive carried by one stretch of the history
    must be downgraded exactly as a negative is.
    """
    inst = name.upper()
    # Chronological before anything else: the period cut and the drawdown both
    # depend on order, and a pool file is not guaranteed to be sorted.
    rows = sorted(rows, key=lambda r: (str(r.get("session") or ""),
                                       str(r.get("ts") or "")))
    vals, hits = _rows_r(rows)
    base = validate_prior(inst, vals, hits)
    periods = split_chronological(rows)
    per_period: dict[str, dict] = {}
    for label in ("development", "validation", "holdout"):
        p_vals, p_hits = _rows_r(periods.get(label) or [])
        p_mean, _sd = _mean_sd(p_vals)
        p_pf, p_dd = _pf_and_drawdown(p_vals)
        per_period[label] = {
            "rows": len(p_vals),
            "mean_r": round(p_mean, 4) if p_vals else None,
            "t1_before_sl_pct": (round(100.0 * p_hits / len(p_vals), 2)
                                 if p_vals else None),
            "profit_factor": p_pf,
            "max_drawdown_r": p_dd,
            "measurable": len(p_vals) >= MIN_HOLDOUT_ROWS,
        }
    wf = walk_forward(rows, folds=folds)
    hold = per_period["holdout"]
    holdout_negative = bool(hold["measurable"] and (hold["mean_r"] or 0) < 0)
    holdout_positive = bool(hold["measurable"] and (hold["mean_r"] or 0) > 0)
    verdict = base["verdict"]
    downgrade = None
    # A pooled mean that clears the bar but fails out of sample is downgraded to
    # what the evidence still shows rather than to a blanket "unvalidated": a
    # holdout that keeps the sign leaves the instrument in the same directional
    # bucket, and only a holdout that does not keep it reads as noise.
    #
    # Both directions go through this, symmetrically. An unchecked positive is the
    # more expensive mistake of the two — it is the one that would let a research
    # universe call an instrument profitable on a pooled mean that one period
    # carried.
    if verdict in (VALIDATED_NEGATIVE, VALIDATED_POSITIVE):
        negative = verdict == VALIDATED_NEGATIVE
        keeps_sign = holdout_negative if negative else holdout_positive
        stable_folds = wf["stable_negative"] if negative else wf["stable_positive"]
        under_bar_label = NEGATIVE_BUT_UNDER_BAR if negative \
            else POSITIVE_BUT_UNDER_BAR
        under_bar = under_bar_label if keeps_sign else INDISTINGUISHABLE_FROM_ZERO
        if not hold["measurable"]:
            verdict, downgrade = INSUFFICIENT_DATA, "HOLDOUT_TOO_SMALL_TO_READ"
        elif not keeps_sign:
            verdict, downgrade = under_bar, "HOLDOUT_DOES_NOT_KEEP_THE_SIGN"
        elif wf["verdict"] == INSUFFICIENT_DATA:
            verdict, downgrade = INSUFFICIENT_DATA, "TOO_FEW_MEASURABLE_FOLDS"
        elif not stable_folds:
            verdict, downgrade = under_bar, "SIGN_FLIPS_ACROSS_FOLDS"
    out = dict(base)
    out.update({
        "periods": per_period,
        "period_spans": periods.get("session_spans"),
        "sessions": periods.get("sessions", 0),
        "walk_forward": wf,
        "holdout_keeps_the_sign": holdout_negative,
        "holdout_keeps_a_positive_sign": holdout_positive,
        "pooled_verdict": base["verdict"],
        "verdict": verdict,
        "downgraded_because": downgrade,
        "negative_veto_supported": verdict == VALIDATED_NEGATIVE,
        "veto_retained_under_v2": _retains_veto(base["prior_dev_r"], verdict),
    })
    return out


def _retains_veto(prior, verdict: str) -> bool:
    """v2 lifts a veto only on evidence that it is unsupported.

    INSUFFICIENT_DATA keeps it: an instrument the pool never measured is left
    exactly as production has it today, because "we did not look" is not a
    finding, in either direction.
    """
    if not isinstance(prior, (int, float)) or float(prior) >= 0:
        return False
    return verdict not in VETO_LIFTED_ON


def validate_pool(rows: list[dict], *, folds: int = FOLDS) -> list[dict]:
    """Per-instrument prior validation over a saved candidate pool.

    The pool is underlying-only, which is the same basis the priors were derived
    on, so this tests the prior on its own terms. It says nothing about option
    economics; a re-admitted instrument still has to clear vehicle, spread, entry
    and room like everything else.
    """
    by_inst: dict[str, list[dict]] = {}
    for r in rows:
        inst = str(r.get("instrument") or "").upper()
        if inst:
            by_inst.setdefault(inst, []).append(r)
    names = sorted(set(by_inst) | set(p17aplus.PRIOR_DEV_R))
    return [validate_instrument(n, by_inst.get(n, []), folds=folds)
            for n in names]


def binding_negatives(validations: list[dict]) -> frozenset[str]:
    """The instruments whose negative prior still vetoes them under v2."""
    return frozenset(
        str(v["instrument"]) for v in validations
        if v.get("veto_retained_under_v2",
                 v.get("negative_veto_supported", False))
    )


def relaxed_by_v2(validations: list[dict]) -> dict[str, list[str]]:
    """Negative-prior instruments grouped by what the pool could say about them.

    The four groups are four different findings and must not be merged. Two of
    them lift the refusal, and they are still reported apart: an instrument the
    pool cannot separate from zero and one that is plainly negative but missed the
    strict bar are admitted to research on the same rule and belong at opposite
    ends of the ranking. The other two leave the refusal where it is — once on
    evidence that the negative is real, once on the absence of evidence entirely.
    """
    lifted_neutral: list[str] = []
    lifted_still_negative: list[str] = []
    kept_validated: list[str] = []
    kept_unmeasured: list[str] = []
    for v in validations:
        prior = v.get("prior_dev_r")
        name = str(v["instrument"])
        if not isinstance(prior, (int, float)) or float(prior) >= 0:
            continue
        verdict = v.get("verdict")
        if verdict == VALIDATED_NEGATIVE:
            kept_validated.append(name)
        elif verdict == NEGATIVE_BUT_UNDER_BAR:
            lifted_still_negative.append(name)
        elif verdict in VETO_LIFTED_ON:
            lifted_neutral.append(name)
        else:
            kept_unmeasured.append(name)
    return {
        "tested_and_indistinguishable_from_zero": sorted(lifted_neutral),
        "lifted_but_still_negative_under_bar": sorted(lifted_still_negative),
        "kept_validated_negative": sorted(kept_validated),
        "never_measured_in_this_pool": sorted(kept_unmeasured),
    }


def grade(rows: list[dict], *, binding_negative: frozenset[str] | None,
          version: str, key) -> dict:
    """Grade recorded ticks under one policy version.

    ``key`` groups ticks into opportunities — the same session x instrument x
    symbol x direction key the funnel and the audit use, passed in so there is
    one definition of "opportunity" in the codebase rather than three.
    """
    states: dict[str, int] = {s: 0 for s in STATES}
    opp_state: dict[str, str] = {}
    per_inst: dict[str, dict[str, int]] = {}
    for row in rows:
        failed = gates_failed(row, binding_negative=binding_negative)
        st = state_of(failed)
        states[st] += 1
        inst = str(row.get("instrument") or "?").upper()
        bucket = per_inst.setdefault(inst, {s: 0 for s in STATES})
        bucket[st] += 1
        k = key(row)
        # An opportunity is described by its best tick: one A+ instant is what
        # the paper book would have acted on.
        prev = opp_state.get(k)
        if prev is None or RANK[st] > RANK[prev]:
            opp_state[k] = st
    opp_counts = {s: sum(1 for v in opp_state.values() if v == s) for s in STATES}
    return {
        "version": version,
        "prior_binding_on": (sorted(binding_negative)
                             if binding_negative is not None
                             else "GRADER_OF_RECORD"),
        "ticks": len(rows),
        "tick_states": states,
        "opportunities": len(opp_state),
        "opportunity_states": opp_counts,
        "per_instrument_ticks": dict(sorted(per_inst.items())),
        "promotable_states": [READY],
        "research_only": True,
    }


def compare(rows: list[dict], validations: list[dict], *, key,
            sessions: int = 0) -> dict:
    """v1 vs v2 over the same recorded ticks.

    Only eligibility is compared. Outcomes — T1-before-SL, net R, PF on the
    option vehicle — cannot be replayed for a candidate that was never entered:
    no leg exists, so no exit price exists. Those columns therefore come from
    resolved legs only and are reported as unmeasured when there are none, rather
    than being modelled into existence.
    """
    binding = binding_negatives(validations)
    groups = relaxed_by_v2(validations)
    v1 = grade(rows, binding_negative=ALL_NEGATIVE_PRIORS, version=V1, key=key)
    v2 = grade(rows, binding_negative=binding, version=V2, key=key)
    per_day = {}
    for name, res in ((V1, v1), (V2, v2)):
        per_day[name] = (
            round(res["opportunity_states"][READY] / sessions, 2)
            if sessions else None
        )
    return {
        "v1": v1,
        "v2": v2,
        "sessions": sessions,
        "a_plus_per_session": per_day,
        "prior_validation": validations,
        "v2_prior_groups": groups,
        "v2_stops_vetoing": groups["tested_and_indistinguishable_from_zero"],
        "v2_keeps_vetoing_validated": groups["kept_validated_negative"],
        "v2_stops_vetoing_but_still_negative": groups[
            "lifted_but_still_negative_under_bar"],
        "v2_keeps_vetoing_unmeasured": groups["never_measured_in_this_pool"],
        "v2_keeps_vetoing": sorted(binding),
        "delta_a_plus_opportunities": (
            v2["opportunity_states"][READY] - v1["opportunity_states"][READY]
        ),
        "delta_pending_data": (
            v2["opportunity_states"][PENDING_DATA]
            - v1["opportunity_states"][PENDING_DATA]
        ),
        "outcome_basis": "RESOLVED_LEGS_ONLY",
        "production_grader_unchanged": True,
        "research_only": True,
    }
