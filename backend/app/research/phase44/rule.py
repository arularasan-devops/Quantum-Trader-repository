"""Phase 44 §2 — the arm evaluated at a decision instant, from live evidence.

Three properties this module is built around, in order of how easily each could
be lost:

* **it cannot drift from Phase 43.** The direction conditions come from
  :mod:`app.research.phase43.mechanisms` and the features from
  :mod:`app.research.phase24.features`; nothing about trend, momentum or ATR is
  restated here. If the historical rule is ever edited, this one moves with it
  and the fingerprint says so;
* **it cannot see the future.** Only minutes that had *completed* before the
  decision's own minute enter the trailing window, and the smoke parses this
  file for any mention of an outcome field (:data:`OUTCOME_FIELDS`). The
  project has already once read a realised peak as an entry filter, so the
  guard is a parser rather than a sentence;
* **it records two costs, never one.** ``modelled`` is the Phase 43 cost:
  statutory charges plus the *assumed* slippage constant
  (``settings.futures_slippage_points``), because five-year candles have no
  book to read a spread from. ``measured`` is the same charges plus the spread
  actually quoted at that instant. So the comparison this phase makes is a
  standing assumption against a measurement, per decision — and if the
  assumption is the smaller of the two, the historical arm was dividing by too
  little cost and will fire less often live. Collapsing the two into one number
  would delete the experiment.
"""
from __future__ import annotations

import numpy as np

from app.market.instruments import get_spec
from app.research import phase44
from app.research.phase19 import futcosts
from app.research.phase24 import conditions as p24cond
from app.research.phase24 import data as p24data
from app.research.phase24 import features as p24feat
from app.research.phase24 import outcomes as p24out

# Fields that exist only once a leg is over. None of them may appear in this
# module; the smoke fails the build if one does.
OUTCOME_FIELDS: tuple[str, ...] = (
    "peak_pct", "mfe_pct", "mae_pct", "giveback", "giveback_pct",
    "net_pct", "gross_pct", "exit_price", "exit_ts", "exit_reason",
    "time_to_peak_min", "resolved", "t1", "realised",
)
# What the arm is built from, declared so the freeze can check the declaration
# against the behaviour rather than against a comment.
RULE_INPUTS: tuple[str, ...] = (
    "minute_bars_before_the_decision", "side", "quoted_bid", "quoted_ask",
    "lot_size",
)

MINUTE = 60.0
LONG, SHORT = "LONG", "SHORT"


def minute_bars(samples: list[tuple[float, float]]) -> list[dict]:
    """One OHLC bar per minute from ``(ts, price)`` observations.

    The capture samples irregularly, so a minute with forty observations would
    otherwise weigh forty times a quiet one and the trailing ATR would measure
    the capture rate rather than the market. Bars are built from the
    observations inside each minute and carry no volume: MCX futures quotes in
    the raw store do not reliably have it, and every feature used here is
    volume-free except the session VWAP, which falls back to a typical-price
    mean when volume is absent.
    """
    buckets: dict[float, list[float]] = {}
    for ts, price in samples:
        if price > 0:
            buckets.setdefault(ts - (ts % MINUTE), []).append(price)
    out: list[dict] = []
    for minute in sorted(buckets):
        prices = buckets[minute]
        out.append({
            "time": int(minute), "open": prices[0], "high": max(prices),
            "low": min(prices), "close": prices[-1], "volume": 0.0,
        })
    return out


class Context:
    """A session's minute bars, with the Phase 24 features computed once.

    Built per (session, instrument) and queried per decision, because computing
    the feature stack for every decision instant separately would be quadratic
    in a session's observation count.
    """

    __slots__ = ("bars", "series", "feat", "n")

    def __init__(self, instrument: str, bars: list[dict]) -> None:
        self.bars = bars
        self.n = len(bars)
        self.series = p24data.Series(instrument, bars) if bars else None
        self.feat = p24feat.build(self.series) if self.series else None

    def index_before(self, ts: float) -> int | None:
        """The last bar that had *completed* before ``ts``'s own minute.

        Strictly before: the decision's own minute was still forming when the
        decision was taken, and letting it into the trailing window would let
        the move being forecast contribute to its own forecast.
        """
        if self.feat is None:
            return None
        cut = ts - (ts % MINUTE)
        idx = int(np.searchsorted(self.series.ts, cut, side="left")) - 1
        return idx if idx >= phase44.MIN_BARS_BEFORE else None

    def window_fault(self, idx: int, ts: float) -> str | None:
        """Why the trailing window at ``idx`` cannot be trusted, or ``None``.

        Bars exist only for the minutes the capture observed, so a gap is
        invisible in the index and visible only in the timestamps. Two ways it
        corrupts the ratio, both refused rather than repaired:

        * the last completed bar is not the minute immediately before the
          decision, so the trailing ATR describes a market that has since moved
          unobserved;
        * the window itself is not contiguous, so at least one bar's true range
          contains a jump across minutes nobody watched, inflating the ATR this
          arm divides by cost.

        Refused and not interpolated: the missing prices were never seen, and a
        filled-in bar is a fabricated observation carrying a real admission.
        """
        if self.series is None:
            return phase44.NO_BARS
        cut = ts - (ts % MINUTE)
        last = float(self.series.ts[idx])
        if round(cut - last) != MINUTE:
            return phase44.STALE_WINDOW
        span = last - float(self.series.ts[idx - phase44.MIN_BARS_BEFORE])
        if round(span) != MINUTE * phase44.MIN_BARS_BEFORE:
            return phase44.GAPPY_WINDOW
        return None


def spec_lot(instrument: str, symbol: object) -> int | None:
    """The contract's lot from the instrument spec, when the symbol allows it.

    A futures lot is a specification, not a market observation, so falling back
    to it is not the same class of assumption as inventing a spread. The risk is
    a *different* contract on the same underlying — CRUDEOILM trades a tenth of
    CRUDEOIL's lot — so the captured symbol must be the plain contract for the
    fallback to apply. An absent symbol is allowed, because the instrument on
    the observation is what selected this row and a mini would arrive under its
    own instrument name.

    The test is deliberately narrow — a symbol that does not start with the
    instrument, or that starts with the MCX mini prefix (``CRUDEOILM...``) — so
    that an expiry-suffixed contract of the *right* underlying is still priced
    rather than refused on a formatting difference.
    """
    text = str(symbol or "").upper().strip()
    root = instrument.upper()
    if text and (not text.startswith(root) or text.startswith(root + "M")):
        return None
    lot = int(get_spec(instrument).lot_size or 0)
    return lot if lot > 0 else None


def measured_cost(
    instrument: str, *, bid: float | None, ask: float | None,
    lot_size: int | None, symbol: object = None,
) -> dict:
    """The round trip a taker actually pays at the quoted book, in points.

    Charges on a round trip priced at the mid, plus the quoted spread once. Once
    and not twice: crossing in and crossing out of the same book is one spread
    of cost against the mid, and charging it on both sides is the double-count
    Phase 35 §7 exists to avoid. The modelled slippage constant is set to zero
    here on purpose — crossing the quoted book *is* the slippage, and adding an
    assumed one on top would charge the same friction twice.

    The lot is the captured one where the capture recorded it and the contract
    spec otherwise; the two are reported under different evidence labels, never
    merged, so a later phase can demand the stronger one.
    """
    # The book is checked before the lot size on purpose. Both make the cost
    # unmeasurable, but they are different failures: a missing lot size is a
    # capture field that was added later, a missing second side means the
    # instant was never executable at all, and reporting the weaker of the two
    # would hide how many decisions had no book.
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return {"cost_points": None, "evidence": phase44.UNMEASURED,
                "reason": phase44.NO_BOOK, "spread_points": None,
                "charges_points": None}
    lots = lot_size if isinstance(lot_size, int) and lot_size > 0 else None
    evidence = phase44.MEASURED
    if lots is None:
        lots = spec_lot(instrument, symbol)
        evidence = phase44.SPEC_LOT
        if lots is None:
            return {"cost_points": None, "evidence": phase44.UNMEASURED,
                    "reason": phase44.CONTRACT_MISMATCH
                    if symbol else phase44.NO_LOT_SIZE,
                    "spread_points": None, "charges_points": None}
    mid = (bid + ask) / 2.0
    charges = futcosts.round_trip(
        instrument, entry=mid, exit_price=mid, lot_size=lots,
        spread_points=0.0, slippage_points=0.0,
    ).get("cost_points")
    if not isinstance(charges, (int, float)):
        return {"cost_points": None, "evidence": phase44.UNMEASURED,
                "reason": phase44.NO_COST, "spread_points": None,
                "charges_points": None}
    spread = float(ask) - float(bid)
    return {
        "cost_points": round(float(charges) + spread, 4),
        "charges_points": round(float(charges), 4),
        "spread_points": round(spread, 4),
        "mid": round(mid, 4),
        "evidence": evidence,
        "reason": None,
    }


def modelled_cost(instrument: str, price: float) -> float | None:
    """Phase 43's cost for the same instant: charges plus assumed slippage.

    Exactly the number the historical arm divided by, reached through Phase
    24's model rather than restated, so it moves if that model moves. It sees
    no book at all: ``slippage_points=None`` takes the configured constant,
    which is a standing assumption about friction and not a measurement of it.
    Kept beside the measured cost so the journal shows, per decision, how much
    of the historical ratio was that assumption.
    """
    arr = np.array([float(price)], dtype=np.float64)
    lot = int(get_spec(instrument).lot_size or 1)
    out = p24out.cost_points_per_trade(
        instrument, arr, arr, lot_size=lot, slippage_points=None
    )
    value = float(out[0])
    return round(value, 4) if np.isfinite(value) and value > 0 else None


def direction_side(direction: object) -> int | None:
    """+1 long, -1 short, ``None`` when the capture recorded no direction.

    A row with no direction is not a refusal and not an admission: the arm is
    directional, so without a side there is no rule to evaluate, and guessing
    one would invent evidence.

    ``BULLISH``/``BEARISH`` is the vocabulary the raw store actually writes;
    the others are accepted so a later capture that words it differently is
    read rather than silently counted as directionless. An unrecognised word
    stays ``None``, which the recorder reports as an unmeasurable reason with
    its own count, so a vocabulary change shows up as a number instead of
    quietly emptying the sample.
    """
    text = str(direction or "").upper()
    if text in (LONG, "BUY", "UP", "BULLISH"):
        return 1
    if text in (SHORT, "SELL", "DOWN", "BEARISH"):
        return -1
    return None


def evaluate(
    ctx: Context, *, ts: float, direction: object, bid: float | None,
    ask: float | None, lot_size: int | None, instrument: str,
    symbol: object = None,
) -> dict:
    """What the frozen arm would have seen at one decision instant.

    Never raises on thin evidence: an instant the rule could not be formed at is
    a row with ``evidence=UNMEASURED`` and a reason, because how often the arm
    is *unmeasurable* live is one of the things this journal exists to count.
    """
    side = direction_side(direction)
    if side is None:
        return _unmeasured(phase44.NO_DIRECTION)
    idx = ctx.index_before(ts)
    if idx is None:
        return _unmeasured(phase44.NO_BARS)
    fault = ctx.window_fault(idx, ts)
    if fault is not None:
        return _unmeasured(fault)
    sides = np.full(ctx.n, side, dtype=np.int8)
    masks = p24cond.masks(ctx.feat, sides)
    base_ok = all(bool(masks[c][idx]) for c in phase44.BASE_CONDITIONS)
    atr = float(ctx.feat["atr"][idx])
    close = float(ctx.feat["close"][idx])
    if not np.isfinite(atr) or atr <= 0:
        return _unmeasured(phase44.NO_ATR, base_admits=base_ok)
    modelled = modelled_cost(instrument, close)
    meas = measured_cost(instrument, bid=bid, ask=ask, lot_size=lot_size,
                         symbol=symbol)
    r_mod = round(atr / modelled, 4) if modelled else None
    r_meas = (round(atr / meas["cost_points"], 4)
              if meas["cost_points"] else None)
    return {
        "evidence": (meas["evidence"] if r_meas is not None
                     else phase44.UNMEASURED),
        "reason": None if r_meas is not None else meas["reason"],
        "side": side,
        "base_admits": base_ok,
        "expected_move_points": round(atr, 4),
        "decision_close": round(close, 4),
        "modelled_cost_points": modelled,
        "measured_cost_points": meas["cost_points"],
        "measured_charges_points": meas["charges_points"],
        "measured_spread_points": meas["spread_points"],
        "ratio_modelled": r_mod,
        "ratio_measured": r_meas,
        # The arm admits only on the measured ratio. The modelled one is carried
        # for comparison and is deliberately not allowed to admit anything: it
        # is the quantity Phase 43 already showed is optimistic by one spread.
        "admits_modelled": None if r_mod is None else bool(
            base_ok and r_mod >= phase44.THRESHOLD),
        "admits": None if r_meas is None else bool(
            base_ok and r_meas >= phase44.THRESHOLD),
    }


def _unmeasured(reason: str, *, base_admits: bool | None = None) -> dict:
    return {
        "evidence": phase44.UNMEASURED, "reason": reason, "side": None,
        "base_admits": base_admits, "expected_move_points": None,
        "decision_close": None, "modelled_cost_points": None,
        "measured_cost_points": None, "measured_charges_points": None,
        "measured_spread_points": None, "ratio_modelled": None,
        "ratio_measured": None, "admits_modelled": None, "admits": None,
    }
