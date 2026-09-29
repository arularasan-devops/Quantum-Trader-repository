"""Phase 26 §4 — compare the exits, then test the events, then refuse.

Order matters and is enforced by the code path:

1. **the exit comparison comes first**, on every candidate with no condition
   applied. If no exit rule makes the pool less negative, no cohort found later
   can be credited to a clever entry — the geometry was the problem;
2. **the event cohorts are tested second**, and only under the exit variant that
   won on development sessions. Phase 25's one durable pocket was a *gap day*, so
   the vocabulary here is deliberately narrow: the market events that were
   already in the frozen Phase 24/25 vocabulary, nothing new invented after
   seeing a result;
3. **the chronological split is the same one Phase 25 used**, by session, and the
   holdout is read once. A variant or a cohort is never selected on validation or
   holdout data;
4. **the winner is stressed**, by re-resolving the same candidates under worse
   costs, worse slippage and a delayed exit.

The ceiling on any verdict is a research lead. The captured window is weeks long,
so no amount of internal agreement can promote anything, and the code cannot
emit a stronger label than ``RESEARCH_LEAD``.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import metrics
from app.research.phase24.discover import benjamini_hochberg
from app.research.phase25 import books, conditions, discover, underlying
from app.research.phase26 import REQUIRES_MORE_DATA, advisory, exits, paths

# Market events only, all of them already frozen in the Phase 24/25 vocabulary.
# The list is short on purpose: this phase is not another indicator search, and a
# narrow list keeps the multiple-testing denominator small enough to survive.
EVENT_CONDITIONS = (
    "gap_day_0p5pct",
    "flat_open_day",
    "opening_range_agrees",
    "prev_day_level_agrees",
    "volatility_expanding",
    "volatility_compressed",
    "expansion_leg_2atr",
    "continuation_confirmed",
    "pullback_20_40pct",
    "exhaustion_bar",
    "trend_5m_and_15m_agree",
    "vwap_side_agrees",
    "room_1p5atr_ahead",
    "time_open_0930_1030",
)

# Economics filters that may be combined with one event. Kept to the measured
# hurdle and spread brackets, which is the one finding that already reproduced.
ECONOMICS_CONDITIONS = ("hurdle_le_3pct", "spread_le_1pct")

MIN_DEV_TRADES = discover.MIN_DEV_TRADES
MIN_WINDOW_TRADES = discover.MIN_WINDOW_TRADES

COST_MULTIPLIERS = (1.25, 1.5)
SLIPPAGE_MULTIPLIERS = (2.0,)
EXIT_DELAY_STEPS = (1,)


def variant_rows(c: paths.Candidates, resolved: dict[str, object]) -> list[dict]:
    """Every exit variant on the same candidates, with its own split results."""
    win = discover.session_windows(c.session)
    rows: list[dict] = []
    for key, o in resolved.items():
        v = exits.VARIANTS_BY_KEY[key]
        res = o.resolved
        if not res.any():
            rows.append({
                "variant": key, "geometry": exits.geometry(v), "trades": 0,
                "status": REQUIRES_MORE_DATA,
            })
            continue
        hold = exits.hold_seconds(c, o)
        overall = metrics.summarise(o, res, discover.base_rate(o, res))
        row = {
            "variant": key,
            "geometry": exits.geometry(v),
            "overall": overall,
            "trades": overall["trades"],
            "avg_net_r": overall["avg_net_r"],
            "median_net_r": overall["median_net_r"],
            "profit_factor": overall["profit_factor"],
            "profit_exit_before_sl_pct": overall["t1_before_sl_pct"],
            "positive_net_pct": overall["positive_net_pct"],
            "max_drawdown_r": overall["max_drawdown_r"],
            "total_net_rupees": round(float(o.net_rupees[res].sum()), 2),
            "avg_net_rupees": round(float(o.net_rupees[res].mean()), 2),
            "outcome_mix": _outcome_mix(o, res),
            # A variant with no fixed target ends most trades at the horizon by
            # construction, so the horizon count alone cannot be compared with
            # Phase 25's flat timeout. How many of those left in profit can be.
            "horizon_exits": int((res & (o.outcome == exits.TIMEOUT)).sum()),
            "horizon_exits_in_profit": int(
                (res & (o.outcome == exits.TIMEOUT) & (o.net_r > 0)).sum()
            ),
            "avg_hold_sec": round(float(hold[res].mean()), 1),
            "median_hold_sec": round(float(np.median(hold[res])), 1),
            "avg_cost_as_fraction_of_risk": round(
                float(np.median(o.cost_points[res]) / np.median(o.risk[res])), 4
            ),
            "exit_orders_charged": exits.geometry(v)["exit_orders"],
        }
        for name, mask in (
            (discover.DEV, win[discover.DEV]),
            (discover.VAL, win[discover.VAL]),
            (discover.HOLDOUT, win[discover.HOLDOUT]),
        ):
            m = mask & res
            row[name] = metrics.summarise(o, m, discover.base_rate(o, m))
        rows.append(row)
    return rows


def _outcome_mix(o, res: np.ndarray) -> dict:
    """How the resolved trades actually ended, which is the Phase 25 finding."""
    labels, counts = np.unique(o.outcome[res], return_counts=True)
    n = max(1, int(res.sum()))
    return {
        str(k): {"trades": int(v), "pct": round(100.0 * int(v) / n, 2)}
        for k, v in sorted(zip(labels, counts), key=lambda kv: -kv[1])
    }


def best_variant(rows: list[dict]) -> str:
    """The variant with the best **development** average net R.

    Selected on development sessions only. Validation and the holdout are never
    used to pick a variant, or the split would stop meaning anything.
    """
    scored = [
        r for r in rows
        if (r.get(discover.DEV) or {}).get("trades", 0) >= MIN_WINDOW_TRADES
    ]
    if not scored:
        return exits.BASELINE_KEY
    return max(scored, key=lambda r: r[discover.DEV]["avg_net_r"])["variant"]


class EventSearch:
    """Event cohorts for one instrument under one exit variant."""

    def __init__(self, c: paths.Candidates, o, variant_key: str) -> None:
        self.c = c
        self.o = o
        self.variant = variant_key
        self.masks = conditions.masks(c.feat, c.side)
        self.res = o.resolved
        win = discover.session_windows(c.session)
        self.dev = win[discover.DEV] & self.res
        self.val = win[discover.VAL] & self.res
        self.hold = win[discover.HOLDOUT] & self.res
        self.tests = 0

    def _cohort(self, names: tuple[str, ...], otype: str) -> np.ndarray:
        m = self.c.option_type == otype
        for n in names:
            m = m & self.masks[n]
        return m

    def _eval(self, cohort: np.ndarray, window: np.ndarray) -> dict:
        return metrics.summarise(
            self.o, cohort & window, discover.base_rate(self.o, window)
        )

    def run(self) -> list[dict]:
        """Test each event, and each event with one economics filter."""
        combos: list[tuple[str, ...]] = [(e,) for e in EVENT_CONDITIONS]
        combos += [
            (e, f) for e in EVENT_CONDITIONS for f in ECONOMICS_CONDITIONS
        ]
        out: list[dict] = []
        for otype in ("CE", "PE"):
            for names in combos:
                if any(n not in self.masks for n in names):
                    continue
                self.tests += 1
                cohort = self._cohort(names, otype)
                dev = self._eval(cohort, self.dev)
                if dev.get("trades", 0) < MIN_DEV_TRADES:
                    continue
                if dev["avg_net_r"] <= 0:
                    continue
                val = self._eval(cohort, self.val)
                hold = self._eval(cohort, self.hold)
                folds = [
                    self._eval(cohort, f & self.res)
                    for f in discover.walk_folds(self.c.session)
                ]
                out.append({
                    "instrument": self.c.instrument,
                    "variant": self.variant,
                    "option_type": otype,
                    "conditions": list(names),
                    "development": dev,
                    "validation": val,
                    "holdout": hold,
                    "session_folds": folds,
                    "folds_measurable": sum(
                        1 for f in folds
                        if f.get("trades", 0) >= MIN_WINDOW_TRADES
                    ),
                    "folds_positive": sum(
                        1 for f in folds
                        if f.get("trades", 0) >= MIN_WINDOW_TRADES
                        and f.get("avg_net_r", 0) > 0
                    ),
                    "p_value": dev.get("p_value_vs_base_rate"),
                    "selectivity": self._selectivity(cohort),
                })
        return out

    def _selectivity(self, cohort: np.ndarray) -> dict:
        m = cohort & self.res
        n = int(m.sum())
        total = max(1, int(self.res.sum()))
        sessions = int(np.unique(self.c.session[self.res]).size) or 1
        return {
            "trades": n,
            "sessions": sessions,
            "trades_per_session": round(n / sessions, 3),
            "pct_of_candidates": round(100.0 * n / total, 3),
            "no_trade_pct": round(100.0 - 100.0 * n / total, 3),
        }


def robustness(c: paths.Candidates, variant_key: str,
               cohort_names: tuple[str, ...] | None = None,
               option_type: str | None = None) -> list[dict]:
    """Re-resolve the same candidates under worse execution assumptions."""
    v = exits.VARIANTS_BY_KEY[variant_key]
    grid: list[dict] = [{"cost_multiplier": m} for m in COST_MULTIPLIERS]
    grid += [
        {"slip_pct": exits.slippage_pct() * 100.0 * m} for m in SLIPPAGE_MULTIPLIERS
    ]
    grid += [{"exit_delay_steps": d} for d in EXIT_DELAY_STEPS]
    masks = conditions.masks(c.feat, c.side) if cohort_names else {}
    rows: list[dict] = []
    for kw in grid:
        o = exits.resolve(c, v, **kw)
        m = o.resolved
        if cohort_names:
            sel = c.option_type == option_type
            for name in cohort_names:
                sel = sel & masks[name]
            m = m & sel
        stats = metrics.summarise(o, m, discover.base_rate(o, o.resolved))
        rows.append({
            "variant": ", ".join(f"{k}={v_}" for k, v_ in kw.items()),
            "trades": stats.get("trades", 0),
            "avg_net_r": stats.get("avg_net_r"),
            "profit_factor": stats.get("profit_factor"),
            "profit_exit_before_sl_pct": stats.get("t1_before_sl_pct"),
            "survives": bool((stats.get("avg_net_r") or -1) > 0),
        })
    return rows


def run_instrument(instrument: str, *, quick: bool = False,
                   db_path: str | None = None) -> dict:
    """The whole Phase 26 study for one instrument, or why it could not run."""
    inst = (instrument or "").upper()
    chain = books.load_chain(inst, db_path=db_path)
    elig = books.eligibility(chain)
    if not elig["eligible"]:
        return {"instrument": inst, "coverage": elig, "status": REQUIRES_MORE_DATA,
                "variants": [], "events": []}
    series = underlying.load_series(inst, db_path=db_path)
    if series is None or len(series) <= paths.WARMUP_BARS:
        elig = dict(elig)
        elig["reasons"] = list(elig["reasons"]) + [
            "no captured 1-minute candles long enough to form market context, so "
            "the option books cannot be given a causal entry reason"
        ]
        elig["status"] = REQUIRES_MORE_DATA
        return {"instrument": inst, "coverage": elig, "status": REQUIRES_MORE_DATA,
                "variants": [], "events": []}

    c = paths.build(inst, chain=chain, series=series)
    if c is None:
        elig = dict(elig)
        elig["reasons"] = list(elig["reasons"]) + [
            "no snapshot produced a resolvable candidate: the same contract was "
            "not quoted again inside its own session"
        ]
        return {"instrument": inst, "coverage": elig, "status": REQUIRES_MORE_DATA,
                "variants": [], "events": []}

    keys = (
        (exits.BASELINE_KEY, "V2_SCALE_HALF_0R5_BE_1H")
        if quick else tuple(v.key for v in exits.VARIANTS)
    )
    resolved = {k: exits.resolve(c, exits.VARIANTS_BY_KEY[k]) for k in keys}
    rows = variant_rows(c, resolved)
    winner = best_variant(rows)

    search = EventSearch(c, resolved[winner], winner)
    events = search.run()
    if not quick:
        for e in events:
            e["robustness"] = robustness(
                c, winner, tuple(e["conditions"]), e["option_type"]
            )
    adv = advisory.row(c, resolved[exits.BASELINE_KEY])

    return {
        "instrument": inst,
        "coverage": {**elig, **(c.coverage or {})},
        "sessions": c.sessions,
        "candidates": len(c),
        "variants": rows,
        "baseline_variant": exits.BASELINE_KEY,
        "best_variant_on_development": winner,
        "variant_uplift_vs_baseline_r": _uplift(rows, winner),
        "events": events,
        "event_hypotheses_evaluated": search.tests,
        "advisory": adv,
        "robustness_of_best_variant": (
            [] if quick else robustness(c, winner)
        ),
        "status": "STUDIED",
    }


def _uplift(rows: list[dict], winner: str) -> dict | None:
    """The winner's improvement over the baseline, in each window."""
    base = next((r for r in rows if r["variant"] == exits.BASELINE_KEY), None)
    best = next((r for r in rows if r["variant"] == winner), None)
    if not base or not best or base.get("trades", 0) == 0:
        return None
    out = {"variant": winner, "baseline": exits.BASELINE_KEY}
    for w in ("overall", discover.DEV, discover.VAL, discover.HOLDOUT):
        a = (base.get(w) or {}).get("avg_net_r")
        b = (best.get(w) or {}).get("avg_net_r")
        out[w] = None if a is None or b is None else round(b - a, 4)
    out["both_still_negative"] = bool(
        (base.get("avg_net_r") or 0) < 0 and (best.get("avg_net_r") or 0) < 0
    )
    return out


def fdr(events: list[dict]) -> list[dict]:
    """Benjamini-Hochberg over every event hypothesis that reached the gate."""
    ps = [e.get("p_value") for e in events]
    keep = benjamini_hochberg([p for p in ps if p is not None])
    it = iter(keep)
    for e in events:
        e["fdr_survivor"] = bool(next(it)) if e.get("p_value") is not None else False
    return events
