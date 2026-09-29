"""RESEARCH-ONLY futures signal engine — its own vehicle, its own validation.

Why this exists as a separate engine
------------------------------------
The obvious shortcut is to take the option signal and drop the strike. That
produces a number, not a plan, and it would be wrong in every way that matters:

* the option plan's stop and targets are a percentage of the **premium**, and a
  premium moves several times faster than the underlying — a 20% premium stop can
  be a 0.4% move in the future, i.e. noise;
* a bearish option view is a long PUT, whose worst case is the premium paid; the
  same view in futures is a **SHORT** with an unbounded loss, so the stop is the
  risk control rather than an advisory level;
* the option's biggest recorded cost is its own spread — measured at 6.76% of mid
  on single-stock options against 0.63% on index options — which does not
  transfer to a futures book at all, and the futures book is not recorded here,
  so it is reported UNAVAILABLE rather than assumed;
* futures carry statutory charges on the **full notional**, which is a cost the
  option side simply does not have.

So the market read is shared (direction, regime, ATR, trend, momentum) and
everything downstream of it is futures arithmetic, reusing the production futures
tool's own ``_entry_setup`` / ``_levels`` / ``_cost_points``. Reusing them is
deliberate: a second stop formula here would make any option-vs-futures
comparison a comparison of my arithmetic instead of the vehicles.

Hard limits
-----------
This module places no order, sizes no position, proposes no trade to the auto
path, and never replaces an option BUY. It imports nothing that can trade — only
the futures paper tool's pure level/cost helpers — and every card it returns
carries ``research_only=True`` and ``executable=False``.
"""
from __future__ import annotations

import datetime as _dt

from app.analysis import futures_feed_audit
from app.analysis import futures_rollover as rollover
from app.analysis import plan_validity
from app.config import settings
from app.execution.futures_paper import _cost_points, _entry_setup, _levels
from app.market.instruments import REGISTRY
from app.models import (
    Candle,
    Decision,
    FuturesPlanCheck,
    FuturesSignalCard,
    IndicatorSnapshot,
    MarketStatus,
    OptionQuote,
)

VALID = "VALID_FUTURES_PLAN"
INVALID = "INVALID_FUTURES_PLAN"

LONG = "LONG"
SHORT = "SHORT"

NO_SIGNAL = "NO_SIGNAL"
BUY = "BUY"
SELL = "SELL"

# Refusal reasons. Named so the Futures dashboard and the reconciliation report
# can state why a plan was refused instead of showing an empty card.
NO_SETUP = "NO_SETUP"
NO_DATA = "INSUFFICIENT_DATA"
NO_CONTRACT = "CONTRACT_UNKNOWN"
NO_LEVELS = "NO_LEVELS"
BAD_GEOMETRY = "PLAN_GEOMETRY_INVALID"
COST_TOO_HIGH = "COST_TO_RISK_TOO_HIGH"
RR_TOO_LOW = "REWARD_RISK_TOO_LOW"
EXPIRY_TOO_CLOSE = "EXPIRY_TOO_CLOSE"
STALE_FEED = "STALE_FEED"
ROOM_TOO_SMALL = "EXPECTED_MOVE_BELOW_TARGET"

# A futures contract in its last day or two rolls: the liquidity moves to the
# next expiry and the plan would be written on the contract being abandoned.
MIN_DAYS_TO_EXPIRY = 1
# T1 must be worth at least this much per unit of risk before costs, or the trade
# cannot survive its own round trip. Deliberately the futures tool's own T1
# multiple, not a new number invented here.
MIN_REWARD_RISK = 1.0
# Beyond this, the modelled round trip eats a share of the stop that no win rate
# recovers. Shares the production futures ceiling.
_MAX_FEED_AGE_SEC = 90.0


def _check(name: str, passed: bool, value: float | str | None = None,
           threshold: float | str | None = None,
           detail: str = "") -> FuturesPlanCheck:
    return FuturesPlanCheck(name=name, passed=passed, value=value,
                            threshold=threshold, detail=detail)


def _days_to_expiry(expiry: str | None, now: float) -> int | None:
    if not expiry:
        return None
    try:
        day = _dt.date.fromisoformat(expiry)
    except ValueError:
        return None
    today = _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc).date()
    return (day - today).days


def _ist(ts: float) -> str:
    ist = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
    return _dt.datetime.fromtimestamp(ts, tz=ist).strftime("%Y-%m-%d %H:%M:%S")


def _refused(card: FuturesSignalCard, reason: str, note: str) -> FuturesSignalCard:
    """A refusal states its reason and keeps whatever was measured on the card.

    The levels are kept visible on an invalid plan on purpose: a blank card
    cannot be argued with, and the recorded 24-Aug defect was precisely a plan
    that stayed on screen with no statement that it had stopped being enterable.
    """
    card.status = INVALID
    card.invalid_reason = reason
    card.reason = note
    return card


def evaluate(
    instrument: str,
    candles: list[Candle],
    price: float,
    ind_snap: IndicatorSnapshot,
    status: MarketStatus,
    *,
    contract: dict | None = None,
    chain: list[dict] | None = None,
    feed_age_sec: float | None = None,
    feed_source: str | None = None,
    minutes_to_close: float | None = None,
    now: float,
) -> FuturesSignalCard:
    """Build a research futures plan for this instrument, or say why there is none.

    Returns a card whose ``status`` is ``VALID_FUTURES_PLAN`` only when every
    check passed. Never raises into the caller's tick: an unexpected failure is
    returned as an invalid card with the reason on it.

    ``contract`` is the contract the feed quotes; ``chain`` is the forward chain
    it belongs to. In expiry week the plan is written on the next contract in the
    chain (Phase 12 §11) instead of refusing itself — research only, and the
    prices remain the quoted contract's, which the card states.
    """
    spec = REGISTRY.get(instrument)
    roll = rollover.select(instrument, contract,
                           chain if chain is not None else ([contract] if contract else []),
                           now)
    rollover.record(roll)
    lot_size = int(roll["lot_size"] or (contract or {}).get("lot_size")
                   or (spec.lot_size if spec else 0)) or None
    expiry = roll["expiry"]
    dte = roll["days_to_expiry"]
    atr_val = float(ind_snap.atr or 0.0)
    card = FuturesSignalCard(
        instrument=instrument,
        exchange=roll["exchange"] or (contract or {}).get("exchange")
        or (spec.exchange if spec else None),
        contract=roll["selected_contract"],
        expiry=expiry,
        days_to_expiry=dte,
        lot_size=lot_size,
        quoted_contract=roll["quoted_contract"],
        quoted_days_to_expiry=roll["quoted_days_to_expiry"],
        rolled=bool(roll["rolled"]),
        reason_for_roll=roll["reason_for_roll"],
        price_basis=roll["price_basis"],
        price_basis_note=roll["price_basis_note"],
        price=round(price, 2) if price else None,
        atr=round(atr_val, 2) if atr_val else None,
        atr_pct=round(atr_val / price * 100.0, 3) if atr_val and price else None,
        adx=round(float(ind_snap.adx), 1) if ind_snap.adx is not None else None,
        regime=status.value,
        trend=ind_snap.trend,
        momentum=_momentum_word(ind_snap.momentum),
        data_freshness_sec=round(feed_age_sec, 1) if feed_age_sec is not None else None,
        feed_state=futures_feed_audit.state(feed_age_sec),
        feed_source=feed_source,
        signal_ts=int(now),
        signal_time_ist=_ist(now),
    )

    try:
        return _evaluate(card, instrument, candles, price, ind_snap, status,
                         atr_val, lot_size, dte, feed_age_sec, minutes_to_close, now)
    except Exception as exc:  # research only: never break the caller's tick
        return _refused(card, "ENGINE_ERROR",
                        f"futures research could not be computed: {type(exc).__name__}")


def _evaluate(
    card: FuturesSignalCard,
    instrument: str,
    candles: list[Candle],
    price: float,
    ind_snap: IndicatorSnapshot,
    status: MarketStatus,
    atr_val: float,
    lot_size: int | None,
    dte: int | None,
    feed_age_sec: float | None,
    minutes_to_close: float | None,
    now: float,
) -> FuturesSignalCard:
    checks: list[FuturesPlanCheck] = []

    # --- data the plan cannot be written without -----------------------------
    enough = len(candles) >= 60 and price > 0 and atr_val > 0
    checks.append(_check("history", enough, len(candles), 60,
                         "60 one-minute bars and a computable ATR"))
    card.checks = checks
    if not enough:
        return _refused(card, NO_DATA,
                        f"only {len(candles)} bars of futures history — no plan yet")

    fresh = feed_age_sec is None or feed_age_sec <= _MAX_FEED_AGE_SEC
    checks.append(_check("feed_freshness", fresh, feed_age_sec, _MAX_FEED_AGE_SEC,
                         "a plan written on a stalled feed is written on a guess"))
    if not fresh:
        return _refused(card, STALE_FEED,
                        f"futures feed is {feed_age_sec:.0f}s old — no plan on a stalled feed")

    # --- direction: shared market read, futures side ------------------------
    side, note = _entry_setup(candles, price, atr_val, status)
    card.setup_type = note
    checks.append(_check("setup", side is not None, side or "NONE", "LONG|SHORT", note))
    if side is None:
        card.signal = NO_SIGNAL
        return _refused(card, NO_SETUP, note)
    card.direction = side
    card.signal = BUY if side == LONG else SELL

    # --- the contract this plan would actually trade -------------------------
    named = bool(card.contract)
    checks.append(_check("contract", named, card.contract or "UNAVAILABLE",
                         "a named contract",
                         "a plan that cannot name its contract is not a plan"))
    if dte is not None:
        ok_dte = dte >= MIN_DAYS_TO_EXPIRY
        checks.append(_check("expiry", ok_dte, dte, MIN_DAYS_TO_EXPIRY,
                             "liquidity leaves the contract as it rolls"))
    else:
        ok_dte = True
        checks.append(_check("expiry", True, "UNAVAILABLE", MIN_DAYS_TO_EXPIRY,
                             "the feed did not state the futures expiry"))

    # --- levels, in points, from the production futures tool -----------------
    stop, t1, t2, t3 = _levels(side, price, atr_val, ind_snap.support, ind_snap.resistance)
    card.entry = round(price, 2)
    # Entry is a zone, not a price: a research plan that claims a single fill
    # price on a leveraged contract is claiming precision it does not have.
    zone = round(0.15 * atr_val, 2)
    card.entry_zone_low = round(price - zone, 2)
    card.entry_zone_high = round(price + zone, 2)
    card.stop, card.target1, card.target2, card.target3 = stop, t1, t2, t3

    risk = abs(price - stop)
    card.risk_points = round(risk, 2)
    card.risk_rupees_per_lot = round(risk * lot_size, 2) if lot_size else None

    # Geometry is checked with the SAME definition the option side uses, mapped
    # into points and direction, so "enterable" means one thing in this codebase.
    if side == LONG:
        bad = plan_validity.unusable(price, stop, t1)
    else:
        # A SHORT is the mirror image: the stop is above and the target below, so
        # the shared test is applied to the reflected levels rather than
        # reimplemented with the comparisons flipped.
        bad = plan_validity.unusable(-price, -stop, -t1)
    checks.append(_check("geometry", bad is None, bad or "OK", "stop and T1 on the right sides",
                         "stop below entry and T1 above it for a long, mirrored for a short"))
    if bad is not None:
        return _refused(card, BAD_GEOMETRY,
                        f"futures levels do not describe an enterable trade ({bad})")
    if risk <= 0:
        return _refused(card, NO_LEVELS, "entry and stop are the same price")

    card.reward_risk_t1 = round(abs(t1 - price) / risk, 2)
    card.reward_risk_t2 = round(abs(t2 - price) / risk, 2)

    # --- what the trade costs, against what it risks -------------------------
    cost_pts = _cost_points(instrument, price, t1, lot_size or 1, 1)
    card.cost_points = round(cost_pts, 2)
    card.cost_to_risk_pct = round(cost_pts / risk * 100.0, 2)
    max_cost = max(0.01, float(settings.futures_max_cost_to_risk_pct))
    ok_cost = card.cost_to_risk_pct <= max_cost
    checks.append(_check("cost_to_risk", ok_cost, card.cost_to_risk_pct, max_cost,
                         "modelled round trip as a share of the stop"))

    ok_rr = card.reward_risk_t1 >= MIN_REWARD_RISK
    checks.append(_check("reward_risk", ok_rr, card.reward_risk_t1, MIN_REWARD_RISK,
                         "T1 against the entry-to-stop distance"))

    # --- is there room for the target the plan claims? ----------------------
    # The honest question for a futures target: does the instrument typically
    # travel that far in the follow window? An ATR-based expectation is coarse but
    # it is measured, and it refuses a target the market has not been reaching.
    expected = atr_val * float(settings.futures_stop_atr)
    card.expected_move_points = round(expected, 2)
    need = abs(t1 - price)
    ok_room = expected >= need
    checks.append(_check("expected_room", ok_room, round(expected, 2), round(need, 2),
                         "typical travel against the distance to T1"))

    # --- is there session left to be right in? ------------------------------
    # A futures position here is intraday only, so a plan written minutes before
    # the close is a plan that would be squared off before its target.
    if minutes_to_close is not None:
        need_min = float(settings.futures_no_entry_minutes)
        ok_session = minutes_to_close > need_min
        checks.append(_check("session_room", ok_session, round(minutes_to_close, 1),
                             need_min, "minutes left before the intraday square-off"))
    else:
        checks.append(_check("session_room", True, "UNAVAILABLE", None,
                             "minutes to close not supplied"))

    # --- liquidity: reported, not assumed -----------------------------------
    # The futures book is not recorded by this feed, so the spread is UNAVAILABLE
    # rather than assumed to be one tick. Volume is what can be honestly stated.
    vol = int(sum(c.volume for c in candles[-20:])) if candles else None
    card.volume = vol
    card.liquidity_note = (
        f"{vol} contracts traded in the last 20 bars; the futures book is not "
        "recorded, so the spread is UNAVAILABLE and is not assumed"
    ) if vol is not None else "UNAVAILABLE"
    checks.append(_check("liquidity", bool(vol), vol, 1,
                         "traded volume in the last 20 bars"))

    card.signal_score = _score(card, ind_snap)

    failed = [c for c in checks if not c.passed]
    card.checks = checks
    if failed:
        first = failed[0].name
        reason = {
            "contract": NO_CONTRACT, "expiry": EXPIRY_TOO_CLOSE,
            "cost_to_risk": COST_TOO_HIGH, "reward_risk": RR_TOO_LOW,
            "expected_room": ROOM_TOO_SMALL, "liquidity": "LIQUIDITY_UNKNOWN",
            "session_room": "TOO_CLOSE_TO_SQUARE_OFF",
        }.get(first, BAD_GEOMETRY)
        return _refused(card, reason, "; ".join(
            f"{c.name}: {c.value} vs {c.threshold}" for c in failed))

    card.status = VALID
    card.reason = (
        f"{side} {card.contract or instrument} at {price:.2f}, stop {stop:.2f} "
        f"({risk:.1f} pts), T1 {t1:.2f} at {card.reward_risk_t1:.2f}R; round trip "
        f"costs {card.cost_to_risk_pct:.1f}% of the stop. {note}. Research only."
    )
    return card


def _score(card: FuturesSignalCard, ind_snap: IndicatorSnapshot) -> float:
    """A futures-specific score, in the 0-100 shape the dashboard already uses.

    Deliberately NOT the option score: it carries no premium, no IV and no theta
    term, and it is not comparable to a confidence number. It exists to rank
    futures plans against each other, and Part 39's own rule applies — it is not
    presented as a probability.
    """
    score = 50.0
    if card.reward_risk_t1:
        score += min(15.0, (card.reward_risk_t1 - 1.0) * 10.0)
    if card.cost_to_risk_pct is not None:
        score += max(-15.0, 10.0 - card.cost_to_risk_pct)
    if card.adx is not None:
        score += min(15.0, max(-10.0, (card.adx - 20.0) * 0.5))
    if card.expected_move_points and card.target1 and card.entry:
        need = abs(card.target1 - card.entry)
        if need > 0:
            score += min(10.0, (card.expected_move_points / need - 1.0) * 10.0)
    aligned = (card.direction == LONG and (ind_snap.trend or "").upper().startswith("UP")) or \
              (card.direction == SHORT and (ind_snap.trend or "").upper().startswith("DOWN"))
    score += 5.0 if aligned else -5.0
    return round(max(0.0, min(100.0, score)), 1)


def option_spread_pct(chain: list[OptionQuote], symbol: str | None) -> float | None:
    """The recommended leg's spread as a % of its premium, or None if unquoted.

    A spread computed from a missing bid is not a narrow spread, so absence stays
    absence — the comparison reports UNAVAILABLE rather than a flattering zero.
    """
    if not symbol:
        return None
    for q in chain:
        if q.symbol != symbol:
            continue
        if q.bid is None or q.ask is None or not q.premium:
            return None
        return round(100.0 * (float(q.ask) - float(q.bid)) / float(q.premium), 2)
    return None


def option_liquidity(chain: list[OptionQuote], symbol: str | None) -> dict:
    """The recommended leg's spread, volume and OI. Absence stays absence.

    A missing bid is not a narrow spread and a missing OI is not zero interest, so
    an unquoted field is reported as None for the comparison to say UNAVAILABLE.
    """
    out: dict = {"spread_pct": option_spread_pct(chain, symbol),
                "volume": None, "oi": None}
    if not symbol:
        return out
    for q in chain:
        if q.symbol == symbol:
            out["volume"] = q.volume
            out["oi"] = q.oi
            break
    return out


def _zone_quality(price: float | None,
                  low: float | None,
                  high: float | None) -> str:
    """Where the live price sits against the plan's entry zone."""
    if price is None or low is None or high is None:
        return "NO_ZONE"
    if float(price) < float(low):
        return "BELOW_ZONE"
    if float(price) > float(high):
        return "ABOVE_ZONE"
    return "IN_ZONE"


def compare(
    option_dec: Decision | None,
    futures_card: FuturesSignalCard,
    *,
    opt_spread_pct: float | None = None,
    opt_volume: float | None = None,
    opt_oi: float | None = None,
    resolved: dict | None = None,
) -> dict:
    """Option plan vs futures plan on the same market event. RESEARCH LABEL ONLY.

    Returns one of OPTION_BETTER / FUTURES_BETTER / BOTH_VALID / BOTH_BAD /
    UNKNOWN and the numbers behind it. It picks no winner for the trader: with
    two plans valid the label is BOTH_VALID, because a preference needs resolved
    outcomes across sessions, not one snapshot.
    """
    opt_bad = plan_validity.unusable(
        option_dec.current_premium if option_dec else None,
        option_dec.stop_loss if option_dec else None,
        option_dec.target1 if option_dec else None,
    ) if option_dec is not None else "NO_DECISION"
    opt_valid = opt_bad is None and bool(
        option_dec and option_dec.signal and option_dec.signal.value == BUY)
    fut_valid = futures_card.status == VALID

    if opt_valid and fut_valid:
        label = "BOTH_VALID"
    elif opt_valid:
        label = "OPTION_BETTER"
    elif fut_valid:
        label = "FUTURES_BETTER"
    elif opt_bad is not None and futures_card.invalid_reason:
        label = "BOTH_BAD"
    else:
        label = "UNKNOWN"

    prem = option_dec.current_premium if option_dec else None
    zone = option_dec.entry_range if option_dec else None
    opt_room = None
    if prem and option_dec and option_dec.target1 is not None:
        opt_room = round(100.0 * (float(option_dec.target1) - float(prem))
                         / float(prem), 2)
    fut_room = None
    if futures_card.target1 is not None and futures_card.entry is not None:
        fut_room = round(abs(float(futures_card.target1)
                            - float(futures_card.entry)), 1)

    reasons: list[str] = []
    if opt_bad is not None:
        reasons.append(f"option plan unusable: {opt_bad}")
    if futures_card.invalid_reason:
        reasons.append(f"futures plan unusable: {futures_card.invalid_reason}")
    if opt_spread_pct is not None and futures_card.spread_pct is not None:
        reasons.append(f"spread: option {opt_spread_pct}% vs futures "
                       f"{futures_card.spread_pct}%")
    elif opt_spread_pct is not None:
        reasons.append(f"option spread {opt_spread_pct}% of premium; "
                       "futures book not recorded")
    if not reasons:
        reasons.append("both plans measured; no vehicle preference is claimed "
                       "from one snapshot")

    return {
        "label": label,
        "option": {
            "available": option_dec is not None,
            "valid_plan": opt_valid,
            "invalid_reason": opt_bad,
            "premium": prem,
            "reward_risk_t1": _rr(option_dec),
            # The option side's dominant measured cost, kept in the comparison
            # because it is the reason the two vehicles differ most. Supplied by
            # the caller from the recorded book; None means it was not quoted.
            "spread_pct": opt_spread_pct,
            "volume": opt_volume,
            "oi": opt_oi,
            "entry_quality": _zone_quality(prem,
                                           zone[0] if zone else None,
                                           zone[1] if zone else None),
            "expected_room_pct": opt_room,
        },
        "futures": {
            "available": futures_card.enabled,
            "valid_plan": fut_valid,
            "invalid_reason": futures_card.invalid_reason,
            "price": futures_card.price,
            "reward_risk_t1": futures_card.reward_risk_t1,
            "cost_to_risk_pct": futures_card.cost_to_risk_pct,
            "spread_pct": futures_card.spread_pct,  # None: book not recorded
            "volume": futures_card.volume,
            "oi": futures_card.open_interest,
            "entry_quality": _zone_quality(futures_card.price,
                                           futures_card.entry_zone_low,
                                           futures_card.entry_zone_high),
            "expected_room_points": fut_room,
        },
        # Resolved history behind the two vehicles: net R, MFE, MAE and
        # target-before-stop, supplied by the caller from the ledgers. Absent when
        # nothing has resolved yet, which is not the same as zero.
        "resolved": resolved,
        "reasons": reasons,
        "note": ("research label only: no winner is selected, and neither side "
                 "replaces the other. A preference needs resolved outcomes across "
                 "sessions, not one snapshot."),
    }


def _rr(dec: Decision | None) -> float | None:
    if dec is None:
        return None
    prem, stop, t1 = dec.current_premium, dec.stop_loss, dec.target1
    if prem is None or stop is None or t1 is None:
        return None
    risk = float(prem) - float(stop)
    if risk <= 0:
        return None
    return round((float(t1) - float(prem)) / risk, 2)


def _momentum_word(value: float | None) -> str | None:
    """The momentum reading as a direction, since the card is read, not computed on."""
    if value is None:
        return None
    if value > 0:
        return f"RISING ({value:+.2f})"
    if value < 0:
        return f"FALLING ({value:+.2f})"
    return "FLAT (0.00)"


__all__ = ["evaluate", "compare", "option_spread_pct", "VALID", "INVALID"]
