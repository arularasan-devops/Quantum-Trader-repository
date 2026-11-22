"""Dynamic capital-based lot sizing for a new auto entry.

Only the instruments in ``settings.dyn_sizing_instruments`` (NIFTY, SENSEX) are
sized dynamically; every other instrument is exactly 1 lot, and that 1 lot still
has to pass every check below. The lots are the minimum of:

* capital allocation  - ``max_capital_pct`` of the usable funds / margin per lot
* broker margin       - usable funds / margin per lot
* risk                - risk budget / (stop distance + slippage both ways + charges) per lot
* instrument exposure - room under ``max_exposure`` for this instrument
* portfolio exposure  - room under ``dyn_sizing_max_portfolio_exposure``
* open positions      - 0 once ``dyn_sizing_max_open_positions`` are open
* configured max lots - ``max_lots`` (1 for non-dynamic instruments)

Funds and margin must be fresh (younger than ``dyn_sizing_max_quote_age_sec``);
missing or stale inputs reject the trade. Pure function: no broker calls here.
"""
from __future__ import annotations

import math
import time

from app.config import settings

DEFAULT_KEY = "DEFAULT"


def is_dynamic(instrument: str) -> bool:
    return instrument.upper() in {i.upper() for i in settings.dyn_sizing_instruments}


def limits_for(instrument: str) -> dict:
    lim = dict(settings.dyn_sizing_limits.get(DEFAULT_KEY) or {})
    lim.update(settings.dyn_sizing_limits.get(instrument.upper()) or {})
    if not is_dynamic(instrument):
        lim["max_lots"] = 1
    return lim


def slippage_points(instrument: str) -> float:
    s = settings.dyn_sizing_slippage_points
    return float(s.get(instrument.upper(), s.get(DEFAULT_KEY, 0.0)) or 0.0)


def _fresh(item: dict | None, now: float) -> tuple[float | None, str | None]:
    if not item or item.get("value") is None:
        return None, "UNVERIFIED"
    try:
        value = float(item["value"])
        age = now - float(item.get("ts") or 0.0)
    except (TypeError, ValueError):
        return None, "UNVERIFIED"
    if age < 0 or age > settings.dyn_sizing_max_quote_age_sec:
        return None, "STALE"
    return value, None


def _floor_lots(room: float, per_lot: float) -> int:
    if per_lot <= 0:
        return 0
    return max(0, int(math.floor(max(0.0, room) / per_lot + 1e-9)))


def compute(*, instrument: str, entry: float, stop: float | None, lot_size: int,
            funds: dict | None, margin_per_lot: dict | None, pending_reserve: float,
            deployed_instrument: float, deployed_portfolio: float, open_positions: int,
            day_pnl: float, extra_caps: dict[str, int] | None = None,
            now: float | None = None) -> dict:
    """Lots for one new entry plus every constraint that produced them.

    ``funds``/``margin_per_lot`` are ``{"value", "ts", "source"}`` snapshots.
    ``lots`` is 0 and ``reject`` names the reason when the trade must not go.
    """
    from app.analysis import option_costs

    now = time.time() if now is None else now
    inst = instrument.upper()
    lim = limits_for(inst)
    rec: dict = {
        "instrument": inst, "dynamic": is_dynamic(inst), "lot_size": int(lot_size),
        "entry_est": round(float(entry or 0.0), 4),
        "stop_est": None if stop is None else round(float(stop), 4),
        "limits": lim, "constraints": {}, "lots": 0, "reject": None, "binding": None,
        "funds_source": (funds or {}).get("source"),
        "margin_source": (margin_per_lot or {}).get("source"),
    }

    def reject(reason: str) -> dict:
        rec["reject"] = reason
        rec["lots"] = 0
        return rec

    if lot_size <= 0 or not entry or entry <= 0:
        return reject("NO_PRICE_OR_LOT_SIZE")
    if stop is None or stop >= entry:
        return reject("NO_PROTECTIVE_STOP")

    avail, err = _fresh(funds, now)
    if err:
        return reject(f"FUNDS_{err}")
    margin, err = _fresh(margin_per_lot, now)
    if err:
        return reject(f"MARGIN_{err}")
    if margin <= 0:
        return reject("MARGIN_UNVERIFIED")

    usable = max(0.0, avail - max(0.0, pending_reserve))
    slip = slippage_points(inst)
    stop_pts = (entry + slip) - max(0.0, stop - slip)
    ch = option_costs.charges(entry + slip, max(0.05, stop - slip), int(lot_size), inst)
    charges_per_lot = float(ch.total) if ch is not None else 0.0
    risk_per_lot = stop_pts * lot_size + charges_per_lot
    notional_per_lot = (entry + slip) * lot_size

    risk_budget = float(lim.get("risk_per_trade") or 0.0)
    daily = float(settings.dyn_sizing_daily_loss_limit or 0.0)
    daily_room = None
    if daily > 0:
        daily_room = max(0.0, daily + min(0.0, float(day_pnl or 0.0)))
        risk_budget = min(risk_budget, daily_room) if risk_budget > 0 else daily_room

    c = rec["constraints"]
    c["capital"] = _floor_lots(usable * float(lim.get("max_capital_pct") or 0.0) / 100.0, margin)
    c["margin"] = _floor_lots(usable, margin)
    c["risk"] = _floor_lots(risk_budget, risk_per_lot)
    c["instrument_exposure"] = _floor_lots(
        float(lim.get("max_exposure") or 0.0) - deployed_instrument, notional_per_lot)
    c["portfolio_exposure"] = _floor_lots(
        float(settings.dyn_sizing_max_portfolio_exposure or 0.0) - deployed_portfolio,
        notional_per_lot)
    c["open_positions"] = (10**6 if open_positions < max(1, settings.dyn_sizing_max_open_positions)
                           else 0)
    c["max_lots"] = max(0, int(lim.get("max_lots") or 0))
    for name, cap in (extra_caps or {}).items():
        c[name] = max(0, int(cap))

    rec.update({
        "available_funds": round(avail, 2), "pending_reserve": round(pending_reserve, 2),
        "usable_funds": round(usable, 2), "margin_per_lot": round(margin, 2),
        "slippage_points": slip, "stop_points_with_slippage": round(stop_pts, 4),
        "charges_per_lot": round(charges_per_lot, 2), "risk_per_lot": round(risk_per_lot, 2),
        "risk_budget": round(risk_budget, 2), "daily_loss_room": daily_room,
        "deployed_instrument": round(deployed_instrument, 2),
        "deployed_portfolio": round(deployed_portfolio, 2), "open_positions": open_positions,
    })
    binding = min(c, key=lambda k: c[k])
    lots = c[binding]
    rec["binding"] = binding
    if lots < 1:
        return reject(f"LIMIT_{binding.upper()}")
    rec["lots"] = int(lots)
    return rec
