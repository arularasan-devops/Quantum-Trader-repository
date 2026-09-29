"""Phase 51 §9/§11/§14 — the search, its chronological gates, and its verdicts.

The order of operations is the whole design, because it is what makes the
holdout meaningful:

1. every generated rule is measured on **DISCOVERY** only, and every one of them
   is counted, including the ones that die immediately. That count is the FDR
   denominator;
2. Benjamini-Hochberg over that whole count picks the rules whose discovery
   result is not explicable by the number of things tried;
3. those rules are **frozen** — rule id, side, parameters, exit geometry — and
   measured on **VALIDATION**. Nothing is re-tuned;
4. only what survives validation touches **UNTOUCHED_HOLDOUT**, once.

A rule that is positive in discovery and dies in validation is not a failure of
the engine; it is the engine working, and it is labelled ``OVERFIT_RISK`` rather
than dropped, so the report shows how many candidates died at which gate. That
count is the only honest way to read a zero.

``DISCOVERY_LEADS`` and ``ROBUST_CANDIDATES`` are deliberately different
objects: a lead is an interesting historical relationship and nothing more, and
the distinction exists so a lead can never be quoted as a validated result.
"""
from __future__ import annotations

import hashlib
import time

import numpy as np

from app.research.phase51 import (
    DISCOVERY,
    HISTORICAL_LEAD,
    OVERFIT_RISK,
    PROMISING_NEEDS_DATA,
    REJECTED,
    ROBUST_CANDIDATE,
    UNTOUCHED_HOLDOUT,
    VALIDATION,
    preregistration_fingerprint,
)
from app.research.phase51 import families, geometry, simulate, stats, universe

# Chronological split, by session, declared before anything is measured.
DISCOVERY_FRACTION = 0.60
VALIDATION_FRACTION = 0.20
# The remainder is the untouched holdout. It is read once, for candidates that
# are already frozen, and never for generation or tuning.

SECONDS_PER_YEAR = 365.25 * 86_400


def candidate_id(instrument: str, rule_id: str) -> str:
    return hashlib.sha256(f"{instrument}|{rule_id}".encode()).hexdigest()[:16]


def partitions(sessions: np.ndarray) -> dict[str, np.ndarray]:
    """Three contiguous spans of sessions, in time order, never shuffled.

    Split on distinct sessions rather than on bars so a session is never cut in
    half across two partitions, which would let a trade decided in discovery be
    resolved by validation's bars.
    """
    uniq = np.unique(sessions)
    n = uniq.size
    d_end = int(n * DISCOVERY_FRACTION)
    v_end = int(n * (DISCOVERY_FRACTION + VALIDATION_FRACTION))
    return {
        DISCOVERY: np.isin(sessions, uniq[:d_end]),
        VALIDATION: np.isin(sessions, uniq[d_end:v_end]),
        UNTOUCHED_HOLDOUT: np.isin(sessions, uniq[v_end:]),
    }


def _years_of(ts: np.ndarray) -> np.ndarray:
    return (ts // SECONDS_PER_YEAR).astype(np.int64)


def evaluate(
    instrument: str,
    f: dict[str, np.ndarray],
    mask: np.ndarray,
    side: int,
    partition_mask: np.ndarray,
    *,
    stop_atr: float = simulate.ENTRY_STOP_ATR,
    target_r: float = simulate.ENTRY_TARGET_R,
    horizon: int = simulate.HORIZON_BARS,
    with_detail: bool = False,
) -> dict:
    """Resolve one frozen rule inside one partition and summarise it.

    The partition is applied to the *decision* bar. A trade decided on the last
    bars of a partition may resolve on the next partition's bars, which is the
    honest reading — the alternative is to truncate the trade and report an exit
    the market never gave.
    """
    idx = simulate.non_overlapping(mask & partition_mask)
    if idx.size == 0:
        return {"summary": {"trade_count": 0, "session_count": 0,
                            "net_expectancy_r": None, "measured": False},
                "years": {}, "regimes": {}, "cost_stress": {}}
    o = simulate.resolve(instrument, f, idx, side, stop_atr=stop_atr,
                         target_r=target_r, horizon=horizon)
    keep = simulate.tradable(o)
    if not keep.any():
        return {"summary": {"trade_count": 0, "session_count": 0,
                            "net_expectancy_r": None, "measured": False},
                "years": {}, "regimes": {}, "cost_stress": {}}
    kept_idx = o.idx[keep]
    sessions = f["session"][kept_idx].astype(np.int64)
    years = _years_of(f["ts"][kept_idx].astype(np.int64))
    regimes = stats.regime_labels(f["volatility_ratio"][kept_idx])
    net_r = o.net_r[keep]
    summary = stats.describe(
        net_r, o.net_points[keep], sessions, years,
        o.mfe_r[keep], o.mae_r[keep], o.cost_points[keep],
    )
    out = {
        "summary": summary,
        "years": stats.per_year(net_r, years),
        "regimes": stats.per_regime(net_r, regimes),
        "cost_stress": {},
    }
    if with_detail:
        # Cost stress re-resolves the same trades with the cost model's charge
        # multiplied. A rule that needs today's exact fee schedule to be
        # positive is a cost assumption, not a market relationship.
        for mult in stats.cost_stress_multiples():
            so = simulate.resolve(instrument, f, idx, side, stop_atr=stop_atr,
                                  target_r=target_r, horizon=horizon,
                                  spread_multiplier=float(mult))
            sk = simulate.tradable(so)
            out["cost_stress"][f"{mult:g}"] = {
                "net_expectancy_r": (float(so.net_r[sk].mean()) if sk.any()
                                     else None),
                "trade_count": int(sk.sum()),
            }
        # Parameter sensitivity: the neighbours of the frozen geometry. A result
        # that exists only at one stop and one target is a fitted point.
        out["parameter_sensitivity"] = {}
        for label, s_atr, t_r in (("stop_0.75", 0.75, target_r),
                                  ("stop_1.25", 1.25, target_r),
                                  ("target_1.0R", stop_atr, 1.0),
                                  ("target_2.0R", stop_atr, 2.0)):
            po = simulate.resolve(instrument, f, idx, side, stop_atr=s_atr,
                                  target_r=t_r, horizon=horizon)
            pk = simulate.tradable(po)
            out["parameter_sensitivity"][label] = (
                float(po.net_r[pk].mean()) if pk.any() else None
            )
        # Entry-timing sensitivity: one bar later. A rule that only pays at the
        # instant it fires is a rule nobody can trade.
        do = simulate.resolve(instrument, f, idx, side, stop_atr=stop_atr,
                              target_r=target_r, horizon=horizon,
                              entry_delay_bars=simulate.ENTRY_DELAY_BARS + 1)
        dk = simulate.tradable(do)
        out["entry_delay_sensitivity"] = {
            "one_bar_later_net_expectancy_r": (float(do.net_r[dk].mean())
                                               if dk.any() else None),
        }
        out["per_session_net_r"] = {
            "sessions": int(np.unique(sessions).size),
            "net_r_per_session": float(net_r.sum() / max(
                1, int(np.unique(sessions).size))),
        }
    return out


def _positive(result: dict) -> bool:
    exp = result["summary"].get("net_expectancy_r")
    return bool(result["summary"].get("measured")) and exp is not None and exp > 0


def _final_status(discovery: dict, fdr_pass: bool, validation: dict | None,
                  holdout: dict | None, stability: str) -> str:
    """One status per candidate, from the gates it actually cleared.

    Deliberately ordered so that "not measured" can never be reported as
    "rejected": a rule with eleven trades has not failed, it has not been tested.
    """
    if not discovery["summary"].get("measured"):
        return PROMISING_NEEDS_DATA
    if not _positive(discovery):
        return REJECTED
    if not fdr_pass:
        # Positive, but not beyond what the number of hypotheses tried explains.
        return OVERFIT_RISK
    if validation is None or not validation["summary"].get("measured"):
        return PROMISING_NEEDS_DATA
    if not _positive(validation):
        return OVERFIT_RISK
    if holdout is None or not holdout["summary"].get("measured"):
        return PROMISING_NEEDS_DATA
    if not _positive(holdout):
        return HISTORICAL_LEAD
    if stability != stats.STABILITY_PASS:
        return (PROMISING_NEEDS_DATA if stability == stats.STABILITY_UNMEASURED
                else HISTORICAL_LEAD)
    return ROBUST_CANDIDATE


def run_instrument(instrument: str, *, progress_every: int = 0) -> dict:
    """The full staged search for one instrument."""
    s = universe.load(instrument)
    if s is None:
        return {"instrument": instrument, "eligible": False,
                "reason": universe.describe(instrument).get("exclusion_reason")}
    f = geometry.build(s)
    sessions = f["session"].astype(np.int64)
    parts = partitions(sessions)
    started = time.time()

    tested = 0
    leads: list[dict] = []
    for rule in families.all_rules(f):
        tested += 1
        if progress_every and tested % progress_every == 0:
            print(f"    {instrument}: {tested} hypotheses tested, "
                  f"{len(leads)} discovery leads, "
                  f"{time.time() - started:.0f}s", flush=True)
        d = evaluate(instrument, f, rule.mask, rule.side, parts[DISCOVERY])
        summary = d["summary"]
        if not summary.get("measured"):
            continue
        # A lead needs a positive discovery expectancy and enough trades to have
        # measured anything. Everything else is counted and left behind.
        if not _positive(d) or summary["trade_count"] < 30:
            continue
        leads.append({
            "candidate_id": candidate_id(instrument, rule.rule_id),
            "rule_id": rule.rule_id,
            "mechanism_family": rule.family,
            "member": rule.member,
            "human_readable_rule": rule.text,
            "instrument": instrument,
            "side": "LONG" if rule.side > 0 else "SHORT",
            "params": rule.params,
            "discovery": d,
            "p_value": summary["p_value_one_sided"],
            "mask": rule.mask,
        })

    # §8 — BH over every hypothesis generated, not over the leads.
    flags = stats.benjamini_hochberg(
        [lead["p_value"] for lead in leads], tests=tested,
    )
    rows: list[dict] = []
    validation_leads = 0
    holdout_positive = 0
    for lead, fdr_pass in zip(leads, flags):
        mask = lead.pop("mask")
        side = 1 if lead["side"] == "LONG" else -1
        validation = holdout = None
        stability = stats.STABILITY_UNMEASURED
        if fdr_pass:
            # The expensive diagnostics — cost stress, parameter and entry-delay
            # sensitivity — run only for rules that got this far. Run for every
            # generated hypothesis they would multiply the search several times
            # over to produce numbers for candidates that are already dead.
            lead["discovery"] = evaluate(instrument, f, mask, side,
                                         parts[DISCOVERY], with_detail=True)
            # Frozen from here: the same rule id, the same side, the same
            # geometry. The only thing that changes is the calendar.
            validation = evaluate(instrument, f, mask, side, parts[VALIDATION],
                                  with_detail=True)
            if _positive(validation):
                validation_leads += 1
                holdout = evaluate(instrument, f, mask, side,
                                   parts[UNTOUCHED_HOLDOUT], with_detail=True)
                if _positive(holdout):
                    holdout_positive += 1
            stability = stats.stability_status(
                lead["discovery"]["summary"],
                lead["discovery"]["years"],
                lead["discovery"]["cost_stress"],
            )
        status = _final_status(lead["discovery"], fdr_pass, validation,
                               holdout, stability)
        rows.append({
            **{k: v for k, v in lead.items() if k != "p_value"},
            "p_value_one_sided": lead["p_value"],
            "fdr_pass": bool(fdr_pass),
            "validation": validation,
            "untouched_holdout": holdout,
            "stability_status": stability,
            "overfit_status": (OVERFIT_RISK if status == OVERFIT_RISK
                               else "NOT_INDICATED"),
            "final_status": status,
        })

    return {
        "instrument": instrument,
        "eligible": True,
        "bars": int(s.ts.size),
        "sessions": int(np.unique(sessions).size),
        "partition_sessions": {
            k: int(np.unique(sessions[v]).size) for k, v in parts.items()
        },
        "total_hypotheses_tested": tested,
        "total_discovery_leads": len(leads),
        "total_validation_leads": validation_leads,
        "total_untouched_holdout_positive": holdout_positive,
        "total_robust_candidates": sum(
            1 for r in rows if r["final_status"] == ROBUST_CANDIDATE),
        "rows": rows,
        "elapsed_seconds": round(time.time() - started, 1),
    }


FDR_CLEARED = "FDR_CLEARED_ENTRIES"
LEAD_CONDITIONED = "LEAD_CONDITIONED_ENTRIES"

LEAD_CONDITIONED_CAVEAT = (
    "THESE_EXITS_WERE_SEARCHED_OVER_ENTRIES_THAT_DID_NOT_CLEAR_THE_ENTRY_"
    "SEARCHES_FALSE_DISCOVERY_CORRECTION_SO_EVERY_NUMBER_IS_CONDITIONED_ON_A_"
    "SET_THAT_IS_ITSELF_INDISTINGUISHABLE_FROM_A_DATA_MINED_FLUKE_IT_ANSWERS_"
    "WHETHER_THE_EXIT_IS_THE_BINDING_CONSTRAINT_AND_IT_CANNOT_PROMOTE_AN_ENTRY_"
    "CANNOT_CHANGE_AN_OVERFIT_RISK_LABEL_AND_IS_NOT_EVIDENCE_OF_AN_EDGE"
)


def profit_capture_search(
    instrument: str,
    rows: list[dict],
    *,
    lead_conditioned: bool = False,
) -> dict:
    """§13 on a frozen entry set, as its own counted search.

    By default runs only on rules that cleared discovery FDR, uses their frozen
    entry bars, and varies nothing but the exit. Its trials are counted in their
    own denominator: an exit grid searched over surviving entries is a second
    multiple-testing problem, and pooling it with the entry count would
    understate both.

    ``lead_conditioned`` widens the entry set to every discovery lead, including
    the ones FDR refused. It exists because a search that clears nothing leaves
    §13 unanswerable, and the question of whether a flat result is a dead entry
    or a badly chosen exit is worth measuring. It is a strictly weaker object:
    the entry labels are untouched by whatever the exits show, and the caveat
    travels in the payload rather than in a covering note.
    """
    s = universe.load(instrument)
    if s is None:
        return {"instrument": instrument, "measured": False,
                "reason": "instrument not eligible"}
    f = geometry.build(s)
    parts = partitions(f["session"].astype(np.int64))
    survivors = {r["rule_id"]: r for r in rows
                 if lead_conditioned or r["fdr_pass"]}
    # One pass over the generator to recover the survivors' masks, rather than
    # one pass per survivor.
    wanted = {rule.rule_id: rule for rule in families.all_rules(f)
              if rule.rule_id in survivors}
    trials = 0
    out_rows: list[dict] = []
    for rule_id, r in survivors.items():
        rule = wanted.get(rule_id)
        if rule is None:
            continue
        side = 1 if r["side"] == "LONG" else -1
        for name, stop_atr, target_r, horizon in simulate.EXIT_VARIANTS:
            trials += 1
            d = evaluate(instrument, f, rule.mask, side, parts[DISCOVERY],
                         stop_atr=stop_atr, target_r=target_r, horizon=horizon)
            v = evaluate(instrument, f, rule.mask, side, parts[VALIDATION],
                         stop_atr=stop_atr, target_r=target_r, horizon=horizon)
            out_rows.append({
                "candidate_id": r["candidate_id"],
                "rule_id": rule_id,
                "exit_rule": name,
                "discovery_net_expectancy_r":
                    d["summary"].get("net_expectancy_r"),
                "validation_net_expectancy_r":
                    v["summary"].get("net_expectancy_r"),
                "discovery_trades": d["summary"].get("trade_count"),
                "validation_trades": v["summary"].get("trade_count"),
                "discovery_p_value": d["summary"].get("p_value_one_sided"),
            })
    # The exit grid is its own multiple-testing problem, corrected against its
    # own trial count. Pooling it with the entry search would understate both.
    fdr = stats.benjamini_hochberg(
        [float(row["discovery_p_value"] or 1.0) for row in out_rows],
        tests=trials,
    )
    for row, ok in zip(out_rows, fdr):
        row["fdr_pass_within_exit_search"] = bool(ok)
    return {
        "instrument": instrument,
        "measured": bool(out_rows),
        "entry_selection": LEAD_CONDITIONED if lead_conditioned else FDR_CLEARED,
        "lead_conditioned": bool(lead_conditioned),
        "conditioning_caveat": (
            LEAD_CONDITIONED_CAVEAT if lead_conditioned else
            "EVERY_ENTRY_HERE_CLEARED_THE_ENTRY_SEARCHES_FALSE_DISCOVERY_"
            "CORRECTION_BEFORE_ANY_EXIT_WAS_VARIED"
        ),
        "entries_considered": len(survivors),
        "exit_variants": len(simulate.EXIT_VARIANTS),
        "total_exit_hypotheses_tested": trials,
        "rows": out_rows,
        "refused_path_dependent_exits": simulate.PATH_DEPENDENT_EXITS_REFUSED,
        "edge_separation": (
            "THIS_IS_A_PROFIT_CAPTURE_EDGE_SEARCH_OVER_FROZEN_ENTRIES_ITS_"
            "TRIALS_ARE_COUNTED_IN_THEIR_OWN_DENOMINATOR_AND_NO_RESULT_HERE_"
            "PROMOTES_OR_CHANGES_THE_ENTRY_EDGE_STATUS_OF_ANY_CANDIDATE"
        ),
    }


def run(instruments: list[str] | None = None, *, progress_every: int = 0) -> dict:
    """The authoritative search over every eligible instrument."""
    survey = universe.survey()
    names = instruments or [d["instrument"] for d in survey["eligible"]]
    per_instrument = [run_instrument(n, progress_every=progress_every)
                      for n in names]
    totals = {
        "TOTAL_HYPOTHESES_TESTED": sum(
            r.get("total_hypotheses_tested", 0) for r in per_instrument),
        "TOTAL_DISCOVERY_LEADS": sum(
            r.get("total_discovery_leads", 0) for r in per_instrument),
        "TOTAL_VALIDATION_LEADS": sum(
            r.get("total_validation_leads", 0) for r in per_instrument),
        "TOTAL_UNTOUCHED_HOLDOUT_POSITIVE": sum(
            r.get("total_untouched_holdout_positive", 0) for r in per_instrument),
        "TOTAL_ROBUST_CANDIDATES": sum(
            r.get("total_robust_candidates", 0) for r in per_instrument),
    }
    return {
        "version": preregistration_fingerprint(),
        "preregistration_fingerprint": preregistration_fingerprint(),
        "universe": survey,
        "instruments": per_instrument,
        "totals": totals,
        "unmeasurable_families": families.UNMEASURABLE_FAMILIES,
        "p_value_caveat": stats.P_VALUE_IS_ORDERING_ONLY,
    }
