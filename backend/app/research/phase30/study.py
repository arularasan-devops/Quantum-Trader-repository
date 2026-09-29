"""Phase 30 §13-§17 — bounded discovery, chronological validation, stress, FDR.

The search is deliberately small, because §13 caps it and because a large search
is what manufactures an edge that is not there:

1. **stage 1** — every frozen pattern, on its own side(s), at every stop band, in
   both confirmation arms, measured on the development window only;
2. **stage 2** — for the stage-1 survivors only, up to three predefined context
   conditions are added, staged singles → pairs → triples, still on development
   only.

Nothing is ever *selected* on validation or holdout data: those windows only
confirm or refuse what development proposed, and the holdout is read once, after
the candidate is frozen. Every hypothesis evaluated at either stage is counted,
and Benjamini-Hochberg runs on that honest denominator.

A candidate then has to survive, in this order: its own non-pattern control, the
validation window, the untouched holdout, a majority of walk-forward folds, 1.5x
and 2x costs with extra slippage, and the removal of its top 1% and top 5% of
winners. Most will not. ``REJECTED`` for everything is a valid outcome and is the
one the base rates predict.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np

from app.research.phase24 import metrics
from app.research.phase24.discover import benjamini_hochberg
from app.research.phase30 import (
    REJECTED,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    VALIDATED,
    context,
    patterns,
    pool,
)

# §8 — the frozen stop grid. Nothing outside this grid is searched.
STOP_GRID = (0.75, 1.0, 1.5, 2.0, 3.0)
# §8 — the existing target geometry, tested separately rather than swept.
TARGET_GRID = (1.0, 1.5, 2.5)
BASE_TARGET_R = 1.5

# §14 — chronological, by time, never by random split.
DEV_FRAC, VAL_FRAC = 0.60, 0.20
DEV, VAL, HOLDOUT = "development", "validation", "holdout"
WALK_FORWARD_FOLDS = 5

# §7/§17 gates, declared before any result.
MIN_DEV_TRADES = 100
MIN_TOTAL_TRADES = 150
MIN_EDGE_OVER_CONTROL_PCT = 2.0
MIN_DEV_AVG_NET_R = 0.0
FDR_ALPHA = 0.05
TOP_SINGLES = 6
TOP_PAIRS = 8
MAX_CONDITIONS = 3

# §15 — cost stress. A candidate that only works under optimistic execution is
# rejected, so the multipliers are applied to the whole modelled round trip.
COST_STRESS = (
    ("normal", {}),
    ("cost_1p5x", {"spread_multiplier": 1.5}),
    ("cost_2x", {"spread_multiplier": 2.0}),
    ("extra_slippage", {"slippage_points": None, "spread_multiplier": 1.25}),
)


def windows(ts: np.ndarray) -> dict[str, tuple[int, int]]:
    """60 / 20 / 20 chronological boundaries over the series' own time span."""
    t0, t1 = int(ts.min()), int(ts.max()) + 1
    span = t1 - t0
    dev_end = t0 + int(span * DEV_FRAC)
    val_end = t0 + int(span * (DEV_FRAC + VAL_FRAC))
    return {DEV: (t0, dev_end), VAL: (dev_end, val_end), HOLDOUT: (val_end, t1)}


def folds(ts: np.ndarray, count: int = WALK_FORWARD_FOLDS) -> list[tuple[int, int]]:
    """``count`` sequential, non-overlapping time blocks over the whole span."""
    t0, t1 = int(ts.min()), int(ts.max()) + 1
    edges = [t0 + int((t1 - t0) * k / count) for k in range(count + 1)]
    return [(edges[k], edges[k + 1]) for k in range(count)]


def _in(ts: np.ndarray, span: tuple[int, int]) -> np.ndarray:
    return (ts >= span[0]) & (ts < span[1])


def control_rate(p: pool.Pool, mask: np.ndarray) -> tuple[float, float]:
    """The non-pattern control's T1 rate and net expectancy inside ``mask``.

    This is the coin a pattern has to beat. Beating zero is not enough: a pool
    with a 40% base T1 rate makes a 41% pattern look like a discovery when it is
    the pool.
    """
    m = p.pat[pool.NON_PATTERN] & p.out.resolved & mask
    if not m.any():
        return 0.0, 0.0
    return float(p.out.t1_before_sl[m].mean()), float(p.out.net_r[m].mean())


class Candidate:
    """One frozen hypothesis: pattern, side, stop band, arm, context conditions."""

    __slots__ = (
        "instrument", "pattern", "side", "stop_atr", "confirmed", "conditions",
        "dev", "val", "holdout", "all", "walk_forward", "stress", "outlier",
        "control_dev", "p_value", "holdout_p_value", "fdr_survivor", "status",
        "reasons", "trades_per_day", "trades_per_week",
    )

    def key(self) -> str:
        arm = "CONFIRMED" if self.confirmed else "PATTERN_ONLY"
        side = {1: "LONG", -1: "SHORT", 0: "BOTH"}[self.side]
        cond = "+".join(self.conditions) if self.conditions else "none"
        return (
            f"{self.instrument}|{self.pattern}|{side}|stop{self.stop_atr}"
            f"|{arm}|{cond}"
        )

    def as_dict(self) -> dict:
        return {
            "key": self.key(),
            "instrument": self.instrument,
            "pattern": self.pattern,
            "side": {1: "LONG", -1: "SHORT", 0: "BOTH"}[self.side],
            "stop_atr": self.stop_atr,
            "confirmation": bool(self.confirmed),
            "conditions": list(self.conditions),
            "control_dev": self.control_dev,
            "development": self.dev,
            "validation": self.val,
            "holdout": self.holdout,
            "full_sample": self.all,
            "walk_forward": self.walk_forward,
            "cost_stress": self.stress,
            "outlier": self.outlier,
            "p_value_development": self.p_value,
            "p_value_holdout": self.holdout_p_value,
            "fdr_survivor": self.fdr_survivor,
            "trades_per_day": self.trades_per_day,
            "trades_per_week": self.trades_per_week,
            "status": self.status,
            "reasons": self.reasons,
        }


class Arm:
    """One (instrument, stop band, confirmation arm) resolved pool with its masks."""

    __slots__ = ("p", "masks", "windows", "folds", "side_rows")

    def __init__(self, p: pool.Pool) -> None:
        self.p = p
        self.masks = context.condition_masks(p.feat, p.side)
        self.windows = windows(p.ts)
        self.folds = folds(p.ts)


def build_arms(prep: pool.Prepared) -> dict[tuple[float, bool], Arm]:
    """Every stop band x confirmation arm for one instrument, resolved once."""
    out: dict[tuple[float, bool], Arm] = {}
    for stop in STOP_GRID:
        for confirmed in (False, True):
            p = pool.build(prep, stop_atr=stop, confirmed_arm=confirmed)
            if p is None:
                continue
            out[(stop, confirmed)] = Arm(p)
    return out


def _cohort(arm: Arm, pattern: str, conditions: tuple[str, ...]) -> np.ndarray:
    m = arm.p.pat[pattern] & arm.p.out.resolved
    for name in conditions:
        m = m & arm.masks[name]
    return m


def _summary(arm: Arm, mask: np.ndarray, base_rate: float) -> dict:
    s = metrics.summarise(arm.p.out, mask, base_rate)
    s.update(gross(arm.p, mask))
    return s


def gross(p: pool.Pool, mask: np.ndarray) -> dict:
    """Pre-cost R, so "no effect" and "cost ate the effect" stay distinguishable.

    Net expectancy alone cannot tell those two apart, and they have opposite
    implications: the first says the structure carries no information, the second
    says it does but the geometry is too small to pay for itself. Both are
    reported for every cohort.
    """
    o = p.out
    m = mask & o.resolved
    if not m.any():
        return {"avg_gross_r": None, "cost_over_risk": None}
    risk = o.risk[m]
    return {
        "avg_gross_r": round(float(np.mean(o.gross_points[m] / risk)), 4),
        "cost_over_risk": round(float(np.median(o.cost_points[m] / risk)), 4),
    }


def stage1(
    arms: dict[tuple[float, bool], Arm]
) -> tuple[list[dict], int, list[dict]]:
    """Every pattern at every band in both arms, on development only.

    Returns the survivors, the hypothesis count, and *every* row tested. The third
    return value exists so that a run in which nothing clears the gates still
    reports a graded table with reasons instead of an empty one — "nothing passed"
    and "nothing was tried" must not look alike.
    """
    survivors: list[dict] = []
    tested: list[dict] = []
    counted = 0
    for (stop, confirmed), arm in arms.items():
        dev = _in(arm.p.ts, arm.windows[DEV])
        base_t1, base_net = control_rate(arm.p, dev)
        for name in patterns.NAMES:
            mask = _cohort(arm, name, ()) & dev
            counted += 1
            s = _summary(arm, mask, base_t1)
            if s.get("trades", 0) < MIN_DEV_TRADES:
                continue
            edge = s["t1_before_sl_pct"] - 100.0 * base_t1
            row = {
                "stop_atr": stop,
                "confirmed": confirmed,
                "pattern": name,
                "dev": s,
                "edge_over_control_pct": round(edge, 2),
                "control_t1_pct": round(100.0 * base_t1, 2),
                "control_net_r": round(base_net, 4),
            }
            tested.append(row)
            if edge < MIN_EDGE_OVER_CONTROL_PCT:
                continue
            if s["avg_net_r"] <= MIN_DEV_AVG_NET_R:
                continue
            survivors.append(row)
    return survivors, counted, tested


def stage2(
    arms: dict[tuple[float, bool], Arm], seeds: list[dict]
) -> tuple[list[dict], int]:
    """Add up to three context conditions to stage-1 survivors, development only."""
    out: list[dict] = []
    counted = 0
    for seed in seeds:
        arm = arms[(seed["stop_atr"], seed["confirmed"])]
        dev = _in(arm.p.ts, arm.windows[DEV])
        base_t1, _ = control_rate(arm.p, dev)
        base_edge = seed["edge_over_control_pct"]

        singles: list[tuple[float, str, dict]] = []
        for name in arm.masks:
            mask = _cohort(arm, seed["pattern"], (name,)) & dev
            counted += 1
            s = _summary(arm, mask, base_t1)
            if s.get("trades", 0) < MIN_DEV_TRADES or s["avg_net_r"] <= 0:
                continue
            edge = s["t1_before_sl_pct"] - 100.0 * base_t1
            if edge <= base_edge:
                continue
            singles.append((s["avg_net_r"], name, s))
        singles.sort(key=lambda r: -r[0])
        keep = [name for _, name, _ in singles[:TOP_SINGLES]]
        for score, name, s in singles[:TOP_SINGLES]:
            out.append({**seed, "conditions": (name,), "dev": s})

        pairs: list[tuple[float, tuple[str, ...], dict]] = []
        for combo in combinations(keep, 2):
            mask = _cohort(arm, seed["pattern"], combo) & dev
            counted += 1
            s = _summary(arm, mask, base_t1)
            if s.get("trades", 0) < MIN_DEV_TRADES or s["avg_net_r"] <= 0:
                continue
            pairs.append((s["avg_net_r"], combo, s))
        pairs.sort(key=lambda r: -r[0])
        for score, combo, s in pairs[:TOP_PAIRS]:
            out.append({**seed, "conditions": combo, "dev": s})

        if MAX_CONDITIONS >= 3:
            for _, combo, _ in pairs[:TOP_PAIRS]:
                for name in keep:
                    if name in combo:
                        continue
                    trio = tuple(sorted(combo + (name,)))
                    mask = _cohort(arm, seed["pattern"], trio) & dev
                    counted += 1
                    s = _summary(arm, mask, base_t1)
                    if s.get("trades", 0) < MIN_DEV_TRADES or s["avg_net_r"] <= 0:
                        continue
                    out.append({**seed, "conditions": trio, "dev": s})
    return out, counted


def _walk_forward(arm: Arm, mask: np.ndarray, base_t1: float) -> dict:
    rows = []
    for lo, hi in arm.folds:
        m = mask & _in(arm.p.ts, (lo, hi))
        s = _summary(arm, m, base_t1)
        rows.append({
            "from_ts": lo,
            "to_ts": hi,
            "trades": s.get("trades", 0),
            "t1_before_sl_pct": s.get("t1_before_sl_pct"),
            "avg_net_r": s.get("avg_net_r"),
        })
    scored = [r for r in rows if r["trades"] >= 20]
    positive = sum(1 for r in scored if (r["avg_net_r"] or 0.0) > 0)
    return {
        "folds": rows,
        "folds_scored": len(scored),
        "folds_positive": positive,
        "majority_positive": bool(scored and positive * 2 > len(scored)),
    }


def _stress(
    prep: pool.Prepared, cand: dict, arm: Arm, mask_conditions: tuple[str, ...]
) -> dict:
    """Re-resolve the same candidates under worse execution assumptions."""
    out: dict[str, dict] = {}
    for name, kw in COST_STRESS:
        if name == "normal":
            p = arm.p
            masks = arm.masks
        else:
            p = pool.build(
                prep,
                stop_atr=cand["stop_atr"],
                confirmed_arm=cand["confirmed"],
                **kw,
            )
            if p is None:
                out[name] = {"trades": 0, "status": "EMPTY"}
                continue
            masks = context.condition_masks(p.feat, p.side)
        m = p.pat[cand["pattern"]] & p.out.resolved
        for cname in mask_conditions:
            m = m & masks[cname]
        base_t1, _ = control_rate(p, np.ones(len(p), dtype=bool))
        s = metrics.summarise(p.out, m, base_t1)
        out[name] = {
            "trades": s.get("trades", 0),
            "t1_before_sl_pct": s.get("t1_before_sl_pct"),
            "avg_net_r": s.get("avg_net_r"),
            "profit_factor": s.get("profit_factor"),
        }
    normal = out.get("normal", {})
    worst = [
        v.get("avg_net_r")
        for k, v in out.items()
        if k != "normal" and v.get("avg_net_r") is not None
    ]
    out["survives_cost_stress"] = bool(
        normal.get("avg_net_r", 0) and worst and all(v > 0 for v in worst)
    )
    return out


def _target_geometry(arm: Arm, mask: np.ndarray) -> dict:
    """§8 — T1 distance, stop distance, reward/risk and cost/risk for the cohort."""
    o = arm.p.out
    m = mask & o.resolved
    if not m.any():
        return {}
    risk = o.risk[m]
    return {
        "stop_distance_points": round(float(np.median(risk)), 2),
        "t1_distance_points": round(float(np.median(np.abs(o.t1[m] - o.entry[m]))), 2),
        "reward_over_risk": BASE_TARGET_R,
        "cost_over_risk": round(float(np.median(o.cost_points[m] / risk)), 4),
        "expected_move_over_risk": round(float(np.median(o.mfe_r[m])), 3),
        "pays_its_cost": bool(
            float(np.median(o.cost_points[m] / risk)) < BASE_TARGET_R / 3.0
        ),
    }


def evaluate(prep: pool.Prepared, arms: dict, cand: dict) -> Candidate:
    """Full §14-§16 evaluation of one frozen candidate."""
    arm = arms[(cand["stop_atr"], cand["confirmed"])]
    conds = tuple(cand.get("conditions") or ())
    mask = _cohort(arm, cand["pattern"], conds)
    base_t1, base_net = control_rate(arm.p, np.ones(len(arm.p), dtype=bool))

    c = Candidate()
    c.instrument = arm.p.instrument
    c.pattern = cand["pattern"]
    c.side = patterns.SIDES[cand["pattern"]]
    c.stop_atr = cand["stop_atr"]
    c.confirmed = cand["confirmed"]
    c.conditions = conds
    c.control_dev = {
        "control_t1_before_sl_pct": round(100.0 * base_t1, 2),
        "control_avg_net_r": round(base_net, 4),
    }
    c.dev = _summary(arm, mask & _in(arm.p.ts, arm.windows[DEV]), base_t1)
    c.val = _summary(arm, mask & _in(arm.p.ts, arm.windows[VAL]), base_t1)
    c.holdout = _summary(arm, mask & _in(arm.p.ts, arm.windows[HOLDOUT]), base_t1)
    c.all = _summary(arm, mask, base_t1)
    c.all["target_geometry"] = _target_geometry(arm, mask)
    c.walk_forward = _walk_forward(arm, mask, base_t1)
    c.stress = _stress(prep, cand, arm, conds)
    c.outlier = {
        "total_net_r": c.all.get("total_net_r"),
        "total_net_r_excl_top1pct": c.all.get("total_net_r_excl_top1pct"),
        "total_net_r_excl_top5pct": c.all.get("total_net_r_excl_top5pct"),
        "avg_net_r_excl_top5pct": c.all.get("avg_net_r_excl_top5pct"),
        "top1_contribution_pct": c.all.get("outlier_top1_contribution_pct"),
        "outlier_dependent": bool(
            (c.all.get("total_net_r_excl_top5pct") or 0.0) <= 0.0
            < (c.all.get("total_net_r") or 0.0)
        ),
    }
    # FDR corrects the *search*, and the search happened on development only, so
    # the p-value fed to Benjamini-Hochberg is development's. The holdout's own
    # p-value is recorded beside it and read once, after the candidate is frozen —
    # using it to select would spend the holdout on discovery.
    c.p_value = c.dev.get("p_value_vs_base_rate")
    c.holdout_p_value = c.holdout.get("p_value_vs_base_rate")
    c.fdr_survivor = False
    sessions = max(1, int(np.unique(arm.p.session[mask]).size))
    trades = int(c.all.get("trades") or 0)
    c.trades_per_day = round(trades / sessions, 3)
    c.trades_per_week = round(5.0 * trades / sessions, 2)
    c.status = REQUIRES_MORE_DATA
    c.reasons = []
    return c


def grade(c: Candidate, *, hypotheses: int) -> Candidate:
    """§20 — exactly one status per candidate, with the reasons that decided it."""
    reasons: list[str] = []
    dev_ok = (c.dev.get("avg_net_r") or 0.0) > 0
    val_ok = (c.val.get("avg_net_r") or 0.0) > 0
    hold_ok = (c.holdout.get("avg_net_r") or 0.0) > 0
    enough = (c.all.get("trades") or 0) >= MIN_TOTAL_TRADES
    wf_ok = bool(c.walk_forward.get("majority_positive"))
    cost_ok = bool(c.stress.get("survives_cost_stress"))
    outlier_ok = not c.outlier.get("outlier_dependent")
    control_ok = (
        (c.all.get("t1_before_sl_pct") or 0.0)
        - (c.control_dev.get("control_t1_before_sl_pct") or 0.0)
    ) >= MIN_EDGE_OVER_CONTROL_PCT
    dd_ok = (c.all.get("max_drawdown_r") or 0.0) <= max(
        10.0, 0.5 * abs(c.all.get("total_net_r") or 0.0)
    )

    if not enough:
        reasons.append(f"sample below {MIN_TOTAL_TRADES} resolved trades")
    if not dev_ok:
        reasons.append("development net expectancy not positive")
    if not val_ok:
        reasons.append("validation net expectancy not positive")
    if not hold_ok:
        reasons.append("untouched holdout net expectancy not positive")
    if not wf_ok:
        reasons.append("no positive majority across walk-forward folds")
    if not cost_ok:
        reasons.append("does not survive 1.5x/2x cost and extra slippage")
    if not outlier_ok:
        reasons.append("profit depends on the top 5% of winners")
    if not control_ok:
        reasons.append(
            f"edge over its own non-pattern control below "
            f"{MIN_EDGE_OVER_CONTROL_PCT}pp"
        )
    if not dd_ok:
        reasons.append("drawdown large relative to total return")
    if not c.fdr_survivor:
        reasons.append(
            f"fails Benjamini-Hochberg at alpha={FDR_ALPHA} over {hypotheses} "
            "counted hypotheses"
        )

    if not reasons:
        c.status = VALIDATED
    elif dev_ok and val_ok and hold_ok and enough:
        c.status = RESEARCH_LEAD
    elif not enough:
        c.status = REQUIRES_MORE_DATA
    else:
        c.status = REJECTED
    c.reasons = reasons
    return c


def apply_fdr(cands: list[Candidate], hypotheses: int) -> int:
    """BH over the honest denominator: every hypothesis counted, not just survivors.

    Padding to ``hypotheses`` is the point — correcting over the shortlist would
    be correcting for the search that was reported rather than the search that was
    run.
    """
    ps = [c.p_value if c.p_value is not None else 1.0 for c in cands]
    pad = max(0, hypotheses - len(ps))
    keep = benjamini_hochberg(ps + [1.0] * pad, FDR_ALPHA)[: len(ps)]
    for c, k in zip(cands, keep):
        c.fdr_survivor = bool(k)
    return hypotheses


def pattern_vs_control(arms: dict[tuple[float, bool], Arm], stop_atr: float) -> list[dict]:
    """§12 — every pattern against the non-pattern control at one stop band."""
    arm = arms.get((stop_atr, False))
    if arm is None:
        return []
    base_t1, base_net = control_rate(arm.p, np.ones(len(arm.p), dtype=bool))
    ctrl = _summary(
        arm, arm.p.pat[pool.NON_PATTERN] & arm.p.out.resolved, base_t1
    )
    rows = []
    for name in patterns.NAMES:
        mask = _cohort(arm, name, ())
        s = _summary(arm, mask, base_t1)
        if not s.get("trades"):
            continue
        conf = _cohort(arms[(stop_atr, True)], name, ()) if (stop_atr, True) in arms else None
        conf_s = (
            _summary(arms[(stop_atr, True)], conf, base_t1) if conf is not None else {}
        )
        rows.append({
            "pattern": name,
            "side": {1: "LONG", -1: "SHORT", 0: "BOTH"}[patterns.SIDES[name]],
            "trades": s["trades"],
            "t1_before_sl_pct": s["t1_before_sl_pct"],
            "t1_ci95": s["t1_ci95"],
            "avg_net_r": s["avg_net_r"],
            "avg_gross_r": s["avg_gross_r"],
            "cost_over_risk": s["cost_over_risk"],
            "profit_factor": s["profit_factor"],
            "max_drawdown_r": s["max_drawdown_r"],
            "control_t1_before_sl_pct": round(100.0 * base_t1, 2),
            "control_avg_net_r": round(base_net, 4),
            # The control is a systematic every-Nth-bar sample of the
            # non-pattern bars, not a random one and not all of them. Its size
            # and interval travel with every comparison, because an edge over a
            # control is only as precise as the control.
            "control_trades": ctrl.get("trades", 0),
            "control_t1_ci95": ctrl.get("t1_ci95"),
            "control_sampling": (
                f"every {pool.CONTROL_STRIDE}th eligible non-pattern bar, "
                "both sides"
            ),
            "edge_over_control_pct": round(s["t1_before_sl_pct"] - 100.0 * base_t1, 2),
            "confirmed_trades": conf_s.get("trades", 0),
            "confirmed_t1_before_sl_pct": conf_s.get("t1_before_sl_pct"),
            "confirmed_avg_net_r": conf_s.get("avg_net_r"),
            "confirmation_helps": bool(
                conf_s.get("avg_net_r") is not None
                and conf_s["avg_net_r"] > s["avg_net_r"]
            ),
        })
    rows.sort(key=lambda r: -(r["avg_net_r"] or 0.0))
    return rows
