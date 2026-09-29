"""Phase 43 family B — relative value between the two five-year series.

What was asked for is NIFTY versus BANKNIFTY. BANKNIFTY has no five-year
one-minute series in this store, only weeks of captured candles, so that pair is
reported UNMEASURED by :mod:`answerability` and nothing is substituted for it.

What *can* be measured is the only pair this store contains: NIFTY and CRUDEOIL.
It is declared here as **one** hypothesis with three arms rather than a family,
and the report says plainly that an index and a commodity share no cointegrating
mechanism — a reverting spread between them is a statistical artefact until
something explains it. Declaring it once, in advance, is what keeps it from
becoming a fishing expedition; measuring it is what lets the answer be "no"
rather than "unknown".

Honesty properties specific to a pair:

* only timestamps present in **both** series are used, so no leg is ever priced
  at a minute the other market was closed;
* the hedge ratio and the spread's own scale come from a trailing window that
  ends at the decision bar. A full-sample beta is the classic pair-trading
  look-ahead;
* both legs are entered at the **next** aligned bar's open and charged their own
  instrument's round trip, in rupees, on a fixed notional per leg. Neither leg
  is priced at a midpoint, because no book exists to have a midpoint.
"""
from __future__ import annotations

import numpy as np

from app.research import phase43
from app.research.phase24 import data as p24data
from app.research.phase43 import mechanisms

# Declared before measurement.
RET_BARS = 30          # the divergence is measured over this many aligned bars
TRAIL_BARS = 300       # trailing window for the hedge ratio and the spread scale
HOLD_BARS = 60         # bounded hold; a reverting spread should not need a week
STRIDE = 5             # one candidate per five aligned bars, as in Phase 24
Z_ARMS = (2.0, 2.5, 3.0)
NOTIONAL_PER_LEG = 1_000_000.0   # rupees of gross exposure per leg
MIN_SIGMA = 1e-9

IST_OFFSET = 19_800


class Aligned:
    """Two series reduced to their common timestamps."""

    __slots__ = ("names", "ts", "close", "open", "high", "low", "session")

    def __len__(self) -> int:
        return int(self.ts.size)


def align(a: str, b: str) -> Aligned | None:
    """Both series on the timestamps they share, or ``None`` if they share none."""
    sa, sb = p24data.load_series(a), p24data.load_series(b)
    if sa is None or sb is None:
        return None
    common, ia, ib = np.intersect1d(sa.ts, sb.ts, return_indices=True)
    if common.size < TRAIL_BARS + HOLD_BARS + RET_BARS:
        return None
    out = Aligned()
    out.names = (a, b)
    out.ts = common
    out.close = (sa.close[ia], sb.close[ib])
    out.open = (sa.open[ia], sb.open[ib])
    out.high = (sa.high[ia], sb.high[ib])
    out.low = (sa.low[ia], sb.low[ib])
    out.session = (common + IST_OFFSET) // 86_400
    return out


def _trailing_beta(ra: np.ndarray, rb: np.ndarray) -> np.ndarray:
    """Rolling regression slope of leg A's returns on leg B's, ending at each bar.

    Computed from rolling sums so the whole five years costs one pass. The window
    ends at the decision bar inclusive; index ``i`` never sees ``i+1``.
    """
    n = ra.size
    win = TRAIL_BARS
    out = np.full(n, np.nan)
    if n <= win:
        return out
    def csum(x: np.ndarray) -> np.ndarray:
        return np.cumsum(np.insert(np.nan_to_num(x, nan=0.0), 0, 0.0))
    sa, sb = csum(ra), csum(rb)
    sab, sbb = csum(ra * rb), csum(rb * rb)
    k = np.arange(win, n + 1)
    m_a = (sa[k] - sa[k - win]) / win
    m_b = (sb[k] - sb[k - win]) / win
    cov = (sab[k] - sab[k - win]) / win - m_a * m_b
    var = (sbb[k] - sbb[k - win]) / win - m_b * m_b
    with np.errstate(invalid="ignore", divide="ignore"):
        beta = np.where(var > MIN_SIGMA, cov / var, np.nan)
    out[win - 1:] = beta
    return out


def _trailing_sigma(x: np.ndarray) -> np.ndarray:
    """Rolling standard deviation ending at each bar, same window and property."""
    n = x.size
    win = TRAIL_BARS
    out = np.full(n, np.nan)
    if n <= win:
        return out
    z = np.nan_to_num(x, nan=0.0)
    s1 = np.cumsum(np.insert(z, 0, 0.0))
    s2 = np.cumsum(np.insert(z * z, 0, 0.0))
    k = np.arange(win, n + 1)
    mean = (s1[k] - s1[k - win]) / win
    var = (s2[k] - s2[k - win]) / win - mean * mean
    out[win - 1:] = np.sqrt(np.maximum(var, 0.0))
    return out


def signals(al: Aligned, *, beta_adjusted: bool) -> dict[str, np.ndarray]:
    """The decision-time divergence score, notional-neutral or beta-adjusted."""
    ca, cb = al.close
    with np.errstate(invalid="ignore", divide="ignore"):
        ra = np.concatenate((np.full(RET_BARS, np.nan),
                             np.log(ca[RET_BARS:] / ca[:-RET_BARS])))
        rb = np.concatenate((np.full(RET_BARS, np.nan),
                             np.log(cb[RET_BARS:] / cb[:-RET_BARS])))
    beta = _trailing_beta(ra, rb) if beta_adjusted else np.ones(ra.size)
    spread = ra - beta * rb
    sigma = _trailing_sigma(spread)
    # The scale that standardises the spread must also end at the decision bar,
    # so it is shifted by one: index i uses the window closing at i-1.
    sigma_prev = np.concatenate((np.full(1, np.nan), sigma[:-1]))
    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.where(sigma_prev > MIN_SIGMA, spread / sigma_prev, np.nan)
    return {"z": z, "beta": beta, "spread": spread, "sigma": sigma_prev}


def _session_last(session: np.ndarray) -> np.ndarray:
    n = session.size
    out = np.empty(n, dtype=np.int64)
    start = 0
    for k in range(1, n + 1):
        if k == n or session[k] != session[start]:
            out[start:k] = k - 1
            start = k
    return out


def trades(al: Aligned, *, beta_adjusted: bool, threshold: float,
           cost_multiplier: float = 1.0) -> dict[str, np.ndarray]:
    """Resolve every pair candidate above ``threshold`` into rupee outcomes.

    Direction is mean reversion: the leg that has outperformed is sold and the
    other bought. Sizing is a fixed rupee notional per leg — notional-neutral by
    construction — scaled by the trailing beta when ``beta_adjusted``, which is
    the risk-adjusted version of the same relationship.
    """
    sig = signals(al, beta_adjusted=beta_adjusted)
    z = sig["z"]
    n = len(al)
    last = _session_last(al.session)
    idx = np.arange(n)
    eligible = np.zeros(n, dtype=bool)
    eligible[TRAIL_BARS + RET_BARS::STRIDE] = True
    eligible &= np.isfinite(z)
    # Room to enter on the next bar and to be flattened inside the same session.
    eligible &= (last - idx) >= (HOLD_BARS + 1)
    eligible &= np.abs(z) >= float(threshold)
    if beta_adjusted:
        eligible &= np.isfinite(sig["beta"]) & (np.abs(sig["beta"]) > 0)
    take = idx[eligible]
    if take.size == 0:
        return {k: np.array([]) for k in
                ("gross", "net", "cost", "mfe", "mae", "win", "session", "order",
                 "ts")}

    fill = take + 1
    exit_i = np.minimum(fill + HOLD_BARS, last[take])
    # Reversion: a positive z means leg A is rich, so leg A is sold.
    side_a = np.where(z[take] > 0, -1.0, 1.0)
    beta = sig["beta"][take] if beta_adjusted else np.ones(take.size)
    side_b = -side_a

    gross = np.zeros(take.size)
    cost = np.zeros(take.size)
    fav = np.zeros(take.size)
    adv = np.zeros(take.size)
    for leg, (side, scale) in enumerate((
        (side_a, np.ones(take.size)), (side_b, np.abs(beta)),
    )):
        op = al.open[leg]
        hi, lo = al.high[leg], al.low[leg]
        entry = op[fill]
        exit_price = op[exit_i]
        units = NOTIONAL_PER_LEG * scale / np.maximum(entry, MIN_SIGMA)
        gross += side * (exit_price - entry) * units
        # Charges come from the shared futures cost model, per unit, priced at
        # the decision close, and are charged on both legs of both instruments.
        per_unit = mechanisms.cost_estimate(al.names[leg], al.close[leg])[take]
        cost += per_unit * units * float(cost_multiplier)
        # Excursions are descriptive only: the widest favourable and adverse
        # marks of this leg while the trade was open.
        for k in range(1, HOLD_BARS + 1):
            alive = (fill + k) <= exit_i
            if not alive.any():
                break
            j = np.minimum(fill + k, exit_i)
            up = (hi[j] - entry) * units
            down = (entry - lo[j]) * units
            f = np.where(side > 0, up, down)
            a = np.where(side > 0, down, up)
            fav = np.where(alive, np.maximum(fav, f), fav)
            adv = np.where(alive, np.maximum(adv, a), adv)

    net = gross - cost
    return {
        "gross": gross,
        "net": net,
        "cost": cost,
        "mfe": fav,
        "mae": adv,
        "win": net > 0,
        "session": al.session[take],
        "ts": al.ts[take],
        "order": np.argsort(take, kind="stable"),
    }


def declared(a: str, b: str) -> list[dict]:
    """Family B's candidates: one relationship, two sizings, three arms."""
    rows: list[dict] = []
    for beta_adjusted in (False, True):
        kind = "BETA_ADJUSTED" if beta_adjusted else "NOTIONAL_NEUTRAL"
        for arm in Z_ARMS:
            rows.append({
                "candidate": f"B_{kind}_DIVERGENCE_{arm:g}SIGMA_{a}_{b}",
                "family": phase43.FAMILY_RELVAL,
                "instrument": f"{a}+{b}",
                "mechanism": (
                    f"{RET_BARS}-bar return divergence between {a} and {b}, "
                    f"{kind.lower().replace('_', ' ')}, entered against the "
                    f"divergence at {arm:g} trailing sigma and held "
                    f"{HOLD_BARS} aligned bars"
                ),
                "beta_adjusted": beta_adjusted,
                "threshold": float(arm),
                "is_control": False,
            })
    return rows
