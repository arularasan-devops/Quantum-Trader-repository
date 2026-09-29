"""Phase 29 §1 — pairing two real legs into one defined-risk structure.

This module is where the study can most easily lie to itself, so the rules are
in code and frozen before any measurement:

* the **strike step is measured from the captured ladder**, never assumed. A
  hard-coded 50-point step would silently build a two-step spread on an
  instrument that trades in 100s, and the width — the whole risk term — would be
  wrong;
* the short leg sits a whole number of steps out of the money from the strike
  nearest spot, and the protective leg a whole number of steps further out.
  Both distances are swept, and every value swept is counted as a hypothesis;
* **both legs must be quoted two-sided in the same snapshot.** A structure whose
  protective leg was not quoted is not a naked short with extra steps — it is
  not a candidate at all, and is counted as ``no_protective_leg`` so the report
  can show how often the ladder simply was not wide enough;
* the forward path is the **intersection** of the two contracts' later quotes.
  A spread cannot be closed at a timestamp where only one leg was quoted, so
  those timestamps are unobservable for this structure and are dropped rather
  than filled from the nearer quote.
"""
from __future__ import annotations

import numpy as np

from app.research.phase25 import books

CE, PE = "CE", "PE"

# The three structures. Both verticals are one short leg plus one further-out
# protective leg of the same type; the condor is the two of them opened together
# on the same snapshot, which is the direction-neutral case.
BULL_PUT = "BULL_PUT_CREDIT"      # sell PE, buy lower PE  -> up/neutral view
BEAR_CALL = "BEAR_CALL_CREDIT"    # sell CE, buy higher CE -> down/neutral view
IRON_CONDOR = "IRON_CONDOR"       # both, together         -> range view

STRUCTURES = (BULL_PUT, BEAR_CALL, IRON_CONDOR)

# The underlying view each structure expresses, in the same words the earlier
# phases use, so a reader does not need a translation table.
VIEW = {
    BULL_PUT: "LONG_VIEW",
    BEAR_CALL: "SHORT_VIEW",
    IRON_CONDOR: "RANGE_VIEW",
}

# Swept geometry. Each combination is a separate hypothesis and is counted in
# the multiple-testing denominator.
SHORT_STEPS = (1, 2, 3)     # steps out of the money for the short strike
WIDTH_STEPS = (1, 2)        # further steps out for the protective strike


class Pair:
    """One priced structure at one snapshot: strikes, legs and the width."""

    __slots__ = ("structure", "short_symbols", "long_symbols", "width_points",
                 "short_strikes", "long_strikes", "short_legs", "long_legs")

    def __init__(self, structure: str) -> None:
        self.structure = structure
        self.short_symbols: list[str] = []
        self.long_symbols: list[str] = []
        self.short_strikes: list[float] = []
        self.long_strikes: list[float] = []
        self.short_legs: list[dict] = []
        self.long_legs: list[dict] = []
        self.width_points = 0.0


def strike_step(chain: books.Chain) -> float:
    """The ladder's own strike spacing, as the median gap between strikes.

    Measured across every contract the capture ever quoted rather than from one
    snapshot, because a thin snapshot can show a single gap that is two steps
    wide and would double every width in the study.
    """
    strikes = np.unique(
        np.asarray([q.strike for q in chain.quotes.values()], dtype=np.float64)
    )
    if strikes.size < 2:
        return 0.0
    gaps = np.diff(strikes)
    gaps = gaps[gaps > 0]
    if gaps.size == 0:
        return 0.0
    return float(np.median(gaps))


def _by_strike(book: dict[str, dict], option_type: str) -> dict[float, dict]:
    """Strike -> leg for one side of one snapshot's book.

    A duplicated strike resolves deterministically to the tighter quoted spread,
    then the higher volume, then the symbol, so a rerun cannot pick a different
    leg and change the study.
    """
    out: dict[float, dict] = {}
    for leg in book.values():
        if str(leg.get("option_type") or "").upper() != option_type:
            continue
        strike = float(leg.get("strike") or 0.0)
        cur = out.get(strike)
        if cur is None:
            out[strike] = leg
            continue
        key_new = (
            float(leg["ask"]) - float(leg["bid"]),
            -float(leg.get("volume") or 0.0),
            str(leg.get("symbol") or ""),
        )
        key_cur = (
            float(cur["ask"]) - float(cur["bid"]),
            -float(cur.get("volume") or 0.0),
            str(cur.get("symbol") or ""),
        )
        if key_new < key_cur:
            out[strike] = leg
    return out


def nearest_strike(strikes: dict[float, dict], spot: float) -> float | None:
    """The quoted strike nearest spot; ties resolve to the lower strike."""
    if not strikes:
        return None
    return min(strikes, key=lambda k: (abs(k - float(spot)), k))


def _vertical(
    strikes: dict[float, dict],
    *,
    atm: float,
    step: float,
    short_steps: int,
    width_steps: int,
    direction: int,
) -> tuple[dict, dict] | None:
    """One vertical's two legs, or ``None`` when either strike was not quoted.

    ``direction`` is +1 when the structure sells above spot (a call spread) and
    -1 when it sells below (a put spread). Both legs come from the same
    snapshot's book by construction — the caller passes one snapshot.
    """
    short_strike = atm + direction * short_steps * step
    long_strike = short_strike + direction * width_steps * step
    short_leg = strikes.get(round(short_strike, 4)) or strikes.get(short_strike)
    long_leg = strikes.get(round(long_strike, 4)) or strikes.get(long_strike)
    if short_leg is None or long_leg is None:
        return None
    return short_leg, long_leg


def build_pair(
    book: dict[str, dict],
    *,
    spot: float,
    step: float,
    structure: str,
    short_steps: int,
    width_steps: int,
) -> Pair | None:
    """The structure's legs at one snapshot, or ``None`` when unpriceable.

    ``None`` is a real answer and the caller counts it: the captured ladder is a
    few strikes wide, so a three-step short with a two-step protective leg often
    simply falls off the end of what was quoted.
    """
    if step <= 0:
        return None
    pair = Pair(structure)
    width = float(width_steps) * float(step)

    if structure in (BULL_PUT, IRON_CONDOR):
        puts = _by_strike(book, PE)
        atm = nearest_strike(puts, spot)
        if atm is None:
            return None
        legs = _vertical(
            puts, atm=atm, step=step, short_steps=short_steps,
            width_steps=width_steps, direction=-1,
        )
        if legs is None:
            return None
        short_leg, long_leg = legs
        pair.short_legs.append(short_leg)
        pair.long_legs.append(long_leg)

    if structure in (BEAR_CALL, IRON_CONDOR):
        calls = _by_strike(book, CE)
        atm = nearest_strike(calls, spot)
        if atm is None:
            return None
        legs = _vertical(
            calls, atm=atm, step=step, short_steps=short_steps,
            width_steps=width_steps, direction=+1,
        )
        if legs is None:
            return None
        short_leg, long_leg = legs
        pair.short_legs.append(short_leg)
        pair.long_legs.append(long_leg)

    if not pair.short_legs:
        return None

    pair.short_symbols = [str(leg["symbol"]) for leg in pair.short_legs]
    pair.long_symbols = [str(leg["symbol"]) for leg in pair.long_legs]
    pair.short_strikes = [float(leg["strike"]) for leg in pair.short_legs]
    pair.long_strikes = [float(leg["strike"]) for leg in pair.long_legs]
    # An iron condor's two wings cannot both finish in the money, so the defined
    # loss is one wing's width, not the sum of both. Overstating it here would
    # flatter every R-multiple in the study.
    pair.width_points = width
    return pair


def common_path(chain: books.Chain, symbols: list[str], after_pos: int) -> np.ndarray:
    """Snapshot positions strictly after ``after_pos`` where *every* leg was quoted.

    The intersection, not the union: a structure can only be opened or closed at
    a timestamp where each of its legs carried a real two-sided quote.
    """
    common: np.ndarray | None = None
    for symbol in symbols:
        q = chain.quotes.get(symbol)
        if q is None or len(q) == 0:
            return np.zeros(0, dtype=np.int64)
        pos = np.asarray(q.pos, dtype=np.int64)
        common = pos if common is None else np.intersect1d(common, pos)
        if common.size == 0:
            return common
    if common is None:
        return np.zeros(0, dtype=np.int64)
    return common[common > int(after_pos)]


def quote_at(chain: books.Chain, symbol: str, pos: int) -> tuple[float, float]:
    """The stored ``(bid, ask)`` of one contract at one snapshot position.

    Raises ``KeyError`` when the contract was not quoted there: silently
    returning the nearest quote instead is exactly the substitution this study
    is built to refuse.
    """
    q = chain.quotes[symbol]
    k = int(np.searchsorted(q.pos, pos))
    if k >= len(q) or int(q.pos[k]) != int(pos):
        raise KeyError(f"{symbol} was not quoted at snapshot {pos}")
    return float(q.bid[k]), float(q.ask[k])
