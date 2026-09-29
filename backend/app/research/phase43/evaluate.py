"""Phase 43 §3 — measure each declared candidate, then label it.

The order in this module is the whole point and is enforced by the code path:

1. sessions are cut chronologically into development / validation / holdout,
   by whole sessions, before any candidate is scored;
2. every candidate is scored on all three windows in one pass. Nothing is
   *selected* on validation or holdout — the only selection this phase makes is
   family D's choice of which family-A arm to split, and that is made on the
   development window alone;
3. the cost stress, the outlier check and the FDR correction are applied to the
   holdout result, because a candidate that needs the full sample to look
   positive has not survived anything.

Everything is measured in rupees per lot (per leg notional for pairs) alongside
R, so "NET" in the ranking table is money rather than a unitless score.
"""
from __future__ import annotations

import numpy as np

from app.market.instruments import get_spec
from app.research import phase43
from app.research.phase24 import conditions as p24cond
from app.research.phase24 import data as p24data
from app.research.phase24 import discover as p24disc
from app.research.phase24 import pool as p24pool
from app.research.phase43 import mechanisms, pair, stats

DEV, VAL, HOLD = "train", "validation", "holdout"
WINDOWS = (DEV, VAL, HOLD)


def split_sessions(session: np.ndarray) -> dict[str, np.ndarray]:
    """Chronological window masks over whole sessions.

    Whole sessions rather than timestamps: a trade opened in one window and
    resolved in another would be counted in a window that could not have known
    it, and an intraday cut would do exactly that at every boundary.
    """
    uniq = np.unique(session)
    n = uniq.size
    dev_end = int(round(n * phase43.DEV_SHARE))
    val_end = int(round(n * (phase43.DEV_SHARE + phase43.VAL_SHARE)))
    sets = {
        DEV: uniq[:dev_end],
        VAL: uniq[dev_end:val_end],
        HOLD: uniq[val_end:],
    }
    return {k: np.isin(session, v) for k, v in sets.items()}


class Book:
    """One instrument's resolved pool, plus everything a candidate needs of it."""

    __slots__ = ("instrument", "pool", "masks", "ratios", "lot", "money",
                 "windows")

    def __init__(self, instrument: str, cost_multiplier: float) -> None:
        p = p24pool.build(
            instrument,
            stop_atr=phase43.STOP_ATR,
            t1_r=phase43.T1_R,
            spread_multiplier=float(cost_multiplier),
        )
        series = p24data.load_series(instrument)
        if p is None or series is None:
            raise ValueError("no five-year series for this instrument")
        self.instrument = instrument
        self.pool = p
        self.masks = p24cond.masks(p.feat, p.side)
        # The decision bar's own close, which the pool does not carry. It is the
        # last price the rule is allowed to see: the fill is the *next* bar's
        # open, so costing the ratio at the fill would let the selection read a
        # price from after the decision.
        feat = dict(p.feat)
        feat["close"] = series.close[p.idx]
        self.ratios = mechanisms.ratios(instrument, feat)
        self.lot = float(get_spec(instrument).lot_size or 1)
        o = p.out
        self.money = {
            "gross": o.gross_points * self.lot,
            "net": o.net_points * self.lot,
            "cost": o.cost_points * self.lot,
            "mfe": o.mfe_r * o.risk * self.lot,
            "mae": o.mae_r * o.risk * self.lot,
            "net_r": o.net_r,
            "win": o.t1_before_sl,
        }
        self.windows = split_sessions(p.session)

    def cohort(self, candidate: dict) -> np.ndarray:
        m = self.pool.out.resolved.copy()
        for name in candidate["conditions"]:
            m &= self.masks[name]
        if candidate.get("ratio"):
            r = self.ratios[candidate["ratio"]]
            m &= np.isfinite(r) & (r >= float(candidate["threshold"]))
        return m

    def score(self, candidate: dict, window: np.ndarray) -> dict:
        m = self.cohort(candidate) & window
        mo = self.money
        idx = self.pool.idx[m]
        out = stats.summarise(
            gross=mo["gross"][m], net=mo["net"][m], cost=mo["cost"][m],
            mfe=mo["mfe"][m], mae=mo["mae"][m], win=mo["win"][m],
            session=self.pool.session[m], order=np.argsort(idx, kind="stable"),
        )
        if out["trades"]:
            out["expectancy_r"] = round(float(mo["net_r"][m].mean()), 4)
            out["t"] = stats.t_statistic(mo["net"][m])
            out["p_value"] = stats.p_value_one_sided(out["t"], out["trades"])
            out["win_rate_ci95_low_pct"] = stats.wilson_low(
                int(np.asarray(mo["win"][m], dtype=bool).sum()), out["trades"]
            )
        return out


class PairBook:
    """The aligned two-instrument book for family B."""

    __slots__ = ("names", "aligned", "cost_multiplier")

    def __init__(self, a: str, b: str, cost_multiplier: float) -> None:
        al = pair.align(a, b)
        if al is None:
            raise ValueError("the two series share too few timestamps")
        self.names = (a, b)
        self.aligned = al
        self.cost_multiplier = float(cost_multiplier)

    def resolved(self, candidate: dict) -> dict[str, np.ndarray]:
        return pair.trades(
            self.aligned,
            beta_adjusted=bool(candidate["beta_adjusted"]),
            threshold=float(candidate["threshold"]),
            cost_multiplier=self.cost_multiplier,
        )

    def score(self, res: dict[str, np.ndarray], window_sessions: np.ndarray) -> dict:
        if res["net"].size == 0:
            return {"trades": 0, "sessions": 0, "status": stats.EMPTY}
        m = np.isin(res["session"], window_sessions)
        net = res["net"][m]
        out = stats.summarise(
            gross=res["gross"][m], net=net, cost=res["cost"][m],
            mfe=res["mfe"][m], mae=res["mae"][m], win=res["win"][m],
            session=res["session"][m],
            order=np.argsort(res["ts"][m], kind="stable"),
        )
        if out["trades"]:
            out["expectancy_bps"] = round(
                1e4 * float(net.mean()) / pair.NOTIONAL_PER_LEG, 3
            )
            out["t"] = stats.t_statistic(net)
            out["p_value"] = stats.p_value_one_sided(out["t"], out["trades"])
        return out


def _meaningful(row: dict) -> bool:
    """Whether the holdout expectancy clears the economic floor.

    Two floors because the two kinds of candidate have different units, and both
    are declared in :mod:`app.research.phase43` before measurement.
    """
    if "expectancy_r" in row:
        return float(row["expectancy_r"]) >= phase43.MIN_EXPECTANCY_R
    if "expectancy_bps" in row:
        return float(row["expectancy_bps"]) >= phase43.MIN_EXPECTANCY_BPS
    return False


def status(splits: dict[str, dict], stress: list[dict], sessions: int) -> dict:
    """The label, and the first reason that decided it.

    A single ordered list of tests, so the reason a candidate is not a lead is
    always the *first* thing wrong with it rather than whichever check happened
    to run last.
    """
    hold = splits[HOLD]
    train, val = splits[DEV], splits[VAL]
    if sessions < phase43.MIN_SESSIONS:
        return {"status": phase43.REQUIRES_MORE_DATA,
                "reason": f"only {sessions} sessions carry trades"}
    for name in WINDOWS:
        if splits[name].get("trades", 0) < phase43.MIN_TRADES:
            return {"status": phase43.REQUIRES_MORE_DATA,
                    "reason": (f"{splits[name].get('trades', 0)} trades in the "
                               f"{name} window against a floor of "
                               f"{phase43.MIN_TRADES}")}
    if (train.get("net_total") or 0) <= 0:
        return {"status": phase43.REJECTED,
                "reason": "negative after costs in its own train window"}
    if (val.get("net_total") or 0) <= 0:
        return {"status": phase43.REJECTED,
                "reason": "train-positive but negative in validation"}
    if (hold.get("net_total") or 0) <= 0:
        return {"status": phase43.REJECTED,
                "reason": "did not survive the chronological holdout"}
    if not _meaningful(hold):
        return {"status": phase43.REJECTED,
                "reason": "holdout expectancy below the declared economic floor"}
    share = hold.get("best_session_share_pct")
    if share is not None and float(share) > phase43.MAX_SINGLE_SESSION_SHARE_PCT:
        return {"status": phase43.REJECTED,
                "reason": f"{share}% of the holdout total is one session"}
    if (hold.get("net_total_excl_top1pct") or 0) <= 0:
        return {"status": phase43.REJECTED,
                "reason": "holdout turns negative without its top 1% of trades"}
    failed = [s["variant"] for s in stress if not s["survives"]]
    if failed:
        return {"status": phase43.REJECTED,
                "reason": f"does not survive cost stress at {failed[0]}"}
    return {"status": phase43.HISTORICAL_LEAD,
            "reason": ("positive after costs in train, validation and holdout, "
                       "not carried by one session or one trade, and still "
                       "positive at every stressed cost")}


def fdr(rows: list[dict], alpha: float = phase43.FDR_ALPHA) -> None:
    """Benjamini-Hochberg over every candidate measured in the run.

    The denominator is every hypothesis this phase evaluated, controls included,
    not the ones that survived. A candidate that fails the correction keeps its
    arithmetic and loses its lead label, because a lead is a claim about the
    population and this is the only check that prices the search.
    """
    scored = [r for r in rows if r["splits"][HOLD].get("p_value") is not None]
    keep = p24disc.benjamini_hochberg(
        [float(r["splits"][HOLD]["p_value"]) for r in scored], alpha=alpha
    )
    for r in rows:
        r["fdr_tested"] = False
        r["fdr_survives"] = None
    for r, ok in zip(scored, keep):
        r["fdr_tested"] = True
        r["fdr_survives"] = bool(ok)
        if not ok and r["status"] == phase43.HISTORICAL_LEAD:
            r["status"] = phase43.REJECTED
            r["reason"] = (
                f"positive throughout but does not survive the "
                f"false-discovery correction across {len(scored)} hypotheses"
            )
    return None
