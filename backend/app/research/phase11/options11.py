"""Phase 11 §3 — the options branch. RESEARCH ONLY. No order path.

The option leg is followed on the **recorded chain**, not on a model of it, and
it is charged the way a real fill is charged: **in at the ask, out at the bid**.
That single change is what turned the flow book's apparent +₹2.17L into a loss,
so it is not optional here.

Three rules this module will not bend:

* **Levels come from the production engine.** The stop and T1/T2/T3 followed here
  are the ones the Signal tab actually showed (``Decision.stop_loss`` /
  ``target1..3``). Research does not invent a better option stop and then claim
  options look good.
* **Provenance is never mixed.** A candidate is evaluated on one ``ChainSeries``,
  which carries one source (REAL_BROKER or SIMULATOR), and the row states which.
  A simulator premium path and a broker premium path are never averaged into one
  number.
* **An absent book is absent.** When the recorded leg has no bid/ask, the
  candidate is ``book_source=UNAVAILABLE`` and its costed R is ``None`` — not a
  gross figure quietly presented as if it were net.
"""
from __future__ import annotations

from app.models import OptionType
from app.research.phase7.dataset import ChainSeries

UNAVAILABLE = "UNAVAILABLE"

T1, T2, T3 = "T1", "T2", "T3"
STOP = "STOP"
TIMEOUT = "TIMEOUT"
NO_QUOTE = "NO_FORWARD_QUOTE"

# How many recorded snapshots the premium is followed for. ~1 per minute, so this
# is the same 90-minute window the futures branch uses.
FOLLOW_SNAPSHOTS = 90


def _num(leg: dict, key: str) -> float | None:
    v = leg.get(key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _book(leg: dict, premium: float) -> dict:
    """Bid/ask and what one crossing costs, or an explicit absence."""
    bid, ask = _num(leg, "bid"), _num(leg, "ask")
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return {"bid": None, "ask": None, "spread": None,
                "spread_pct_of_premium": None, "book_source": UNAVAILABLE}
    spread = ask - bid
    return {"bid": round(bid, 2), "ask": round(ask, 2),
            "spread": round(spread, 2),
            "spread_pct_of_premium": (round(100.0 * spread / premium, 2)
                                      if premium > 0 else None),
            "book_source": "RECORDED_BOOK"}


def _moneyness(opt: OptionType | str | None, strike: float | None,
               spot: float | None) -> str:
    if strike is None or spot is None or strike <= 0 or spot <= 0:
        return UNAVAILABLE
    kind = opt.value if isinstance(opt, OptionType) else str(opt or "")
    call = kind.upper().startswith("C")
    near = abs(strike - spot) <= 0.0015 * spot
    if near:
        return "ATM"
    if call:
        return "ITM" if strike < spot else "OTM"
    return "ITM" if strike > spot else "OTM"


def candidate(instrument: str, symbol: str, leg: dict, *, spot: float | None,
              stop: float, targets: tuple[float, float, float],
              series: ChainSeries, ts: int,
              follow: int = FOLLOW_SNAPSHOTS) -> dict:
    """Evaluate one option leg for one market event, costed at the book.

    ``stop`` and ``targets`` are premium levels produced by the production engine
    for this event. The premium path comes from ``series``, whose ``source`` is
    carried through onto the row.
    """
    premium = _num(leg, "premium") or 0.0
    book = _book(leg, premium)
    t1, t2, t3 = targets
    risk = premium - stop
    row: dict = {
        "vehicle": ("CALL" if str(leg.get("option_type", "")).upper().startswith("C")
                    else "PUT"),
        "instrument": instrument.upper(),
        "tradingsymbol": symbol,
        "strike": _num(leg, "strike"),
        "expiry": leg.get("expiry") or UNAVAILABLE,
        "option_type": leg.get("option_type") or UNAVAILABLE,
        "premium": round(premium, 2),
        "moneyness": _moneyness(leg.get("option_type"), _num(leg, "strike"), spot),
        "delta": _num(leg, "delta"),
        # Angel's chain carries no implied vol and no second-order greeks. They
        # are absent rather than inverted from an assumed vol and presented as
        # broker data.
        "iv": None,
        "gamma": None,
        "theta": None,
        "greeks_source": ("PROVIDER_DELTA_ONLY" if _num(leg, "delta") is not None
                          else UNAVAILABLE),
        "oi": _num(leg, "oi"),
        "oi_change": _num(leg, "oi_change"),
        "volume": _num(leg, "volume"),
        "data_source": series.source,
        "entry": round(premium, 2),
        "stop": round(stop, 2),
        "target1": round(t1, 2), "target2": round(t2, 2), "target3": round(t3, 2),
        "risk_points": round(risk, 2),
        "room_to_t1_pct_of_premium": (round(100.0 * (t1 - premium) / premium, 2)
                                      if premium > 0 else None),
        "reward_risk_t1": round((t1 - premium) / risk, 2) if risk > 0 else None,
        **book,
    }
    # Entry quality: what one crossing of this book costs as a share of the risk
    # the stop is meant to define. Above 100% the contract cannot pay, whatever
    # the direction does — this is the measurement that condemned GOLD.
    if book["spread"] is not None and risk > 0:
        row["spread_pct_of_risk"] = round(100.0 * book["spread"] / risk, 1)
        row["entry_quality"] = ("UNPAYABLE" if book["spread"] >= risk
                                else "POOR" if book["spread"] >= 0.5 * risk
                                else "FAIR" if book["spread"] >= 0.2 * risk
                                else "GOOD")
    else:
        row["spread_pct_of_risk"] = None
        row["entry_quality"] = UNAVAILABLE

    path = series.forward(ts, follow, symbol)
    if not path or risk <= 0:
        row.update({"measurable": False, "outcome": NO_QUOTE,
                    "reason": ("no premium was recorded after the signal"
                               if not path else
                               "the engine's stop is not below the premium")})
        return row

    reached: dict[str, dict] = {}
    sequence: list[str] = []
    mfe = mae = 0.0
    mfe_ts = mae_ts = ts
    outcome, exit_px, exit_ts = TIMEOUT, path[-1][1], path[-1][0]
    stop_ts: int | None = None
    for when, px in path:
        r = (px - premium) / risk
        if r > mfe:
            mfe, mfe_ts = r, when
        if r < mae:
            mae, mae_ts = r, when
        if px <= stop:
            outcome, exit_px, exit_ts, stop_ts = STOP, px, when, when
            sequence.append(STOP)
            break
        for name, level in ((T1, t1), (T2, t2), (T3, t3)):
            if name not in reached and px >= level:
                reached[name] = {"ts": when,
                                 "minutes_from_signal": round((when - ts) / 60.0, 1),
                                 "premium": round(px, 2),
                                 "r": round((level - premium) / risk, 3)}
                sequence.append(name)
        if T3 in reached:
            outcome, exit_px, exit_ts = T3, px, when
            break

    first = sequence[0] if sequence else None
    gross_r = (exit_px - premium) / risk
    # Costed: bought at the ask, sold at the bid, using the book recorded at the
    # signal. Without a recorded book there is no net figure at all.
    net_r = None
    cost_r = None
    if book["spread"] is not None:
        cost_r = book["spread"] / risk
        net_r = round(gross_r - cost_r, 3)
        cost_r = round(cost_r, 3)

    row.update({
        "measurable": True,
        "targets_reached": reached,
        "target_sequence": " -> ".join(sequence) or "NONE",
        "first_event": first,
        "target_before_stop": first in (T1, T2, T3),
        "outcome": outcome if first != STOP else STOP,
        "mfe_r": round(mfe, 3),
        "minutes_to_mfe": round((mfe_ts - ts) / 60.0, 1),
        "mae_r": round(mae, 3),
        "minutes_to_mae": round((mae_ts - ts) / 60.0, 1),
        "exit_premium": round(exit_px, 2),
        "minutes_to_resolution": round((exit_ts - ts) / 60.0, 1),
        "minutes_to_target": (reached[T1]["minutes_from_signal"]
                             if T1 in reached else None),
        "minutes_to_stop": (round((stop_ts - ts) / 60.0, 1)
                            if stop_ts is not None else None),
        "gross_r": round(gross_r, 3),
        "cost_r": cost_r,
        "net_r": net_r,
        "cost_basis": ("ask in, bid out at the book recorded with the signal; no "
                       "net figure when the book is absent"),
        "snapshots_followed": len(path),
    })
    return row


def evaluate(instrument: str, series: ChainSeries, ts: int, *,
             symbol: str | None, option_type: str | None,
             stop: float | None, targets: tuple[float | None, ...],
             spot: float | None = None,
             alternatives: int = 4,
             follow: int = FOLLOW_SNAPSHOTS) -> dict:
    """The production leg plus the nearest alternative strikes, all costed.

    ``symbol``, ``stop`` and ``targets`` are what the production engine produced
    for this event — research follows the levels the Signal tab showed and does
    not substitute its own.

    The alternatives exist to answer "was the *strike* the problem?" separately
    from "was the *vehicle* the problem?" — they are measurements, never a
    suggestion to trade a different strike, and nothing reads them back.
    """
    snap = series.at(ts)
    t1 = targets[0] if targets else None
    if not snap or stop is None or t1 is None:
        return {"measurable": False,
                "reason": ("no chain snapshot within tolerance of the signal"
                           if not snap else
                           "the production decision carried no stop or target"),
                "data_source": series.source}
    t2 = targets[1] if len(targets) > 1 and targets[1] else t1
    t3 = targets[2] if len(targets) > 2 and targets[2] else t2
    levels = (float(t1), float(t2), float(t3))
    stop = float(stop)
    want = (option_type.value if isinstance(option_type, OptionType)
            else str(option_type or ""))
    chosen = symbol

    rows: list[dict] = []
    if chosen and chosen in snap:
        row = candidate(instrument, chosen, snap[chosen], spot=spot, stop=stop,
                        targets=levels, series=series, ts=ts, follow=follow)
        row["role"] = "PRODUCTION_LEG"
        rows.append(row)

    # Same side, nearest strikes to the one production took, so the comparison is
    # like-for-like rather than a different view expressed as a different strike.
    ref = None
    if chosen and chosen in snap:
        ref = _num(snap[chosen], "strike")
    if ref is None:
        ref = spot
    same_side = [
        (sym, leg) for sym, leg in snap.items()
        if sym != chosen and _num(leg, "strike") is not None
        and str(leg.get("option_type", "")).upper()[:1] == want.upper()[:1]
    ]
    if ref is not None:
        same_side.sort(key=lambda kv: abs((_num(kv[1], "strike") or 0.0) - ref))
    for sym, leg in same_side[:max(0, alternatives)]:
        row = candidate(instrument, sym, leg, spot=spot, stop=stop,
                        targets=levels, series=series, ts=ts, follow=follow)
        row["role"] = "ALTERNATIVE_STRIKE"
        rows.append(row)

    payable = [r for r in rows if r.get("net_r") is not None]
    return {
        "measurable": bool(rows),
        "data_source": series.source,
        "production_option_type": want or UNAVAILABLE,
        "candidates": rows,
        "production_leg": next((r for r in rows
                                if r.get("role") == "PRODUCTION_LEG"), None),
        "best_net_r": (max(r["net_r"] for r in payable) if payable else None),
        "candidates_with_book": len(payable),
        "candidates_total": len(rows),
    }
