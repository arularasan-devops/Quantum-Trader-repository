"""Late-entry check: is this BUY chasing a move that has already happened?

Pure function, no I/O. Measures how far the underlying and the option premium
have already moved, the bid/ask spread and the reward left to T1 against the
stop. Default thresholds come from the 6-20 Nov real-option-price study
(SENSEX dip entries after a >=10% premium rise in 10 min lost ~16.75 pts on
average); they are research evidence, configurable, not a universal rule.
Runs only when ``late_entry_mode`` is "log" or "enforce" (default "off").
"""
from __future__ import annotations

from app.config import settings

WINDOWS = (1, 3, 5, 10)


def _chg_pct(closes: list[float], n: int, now_px: float) -> float | None:
    if now_px <= 0 or len(closes) <= n:
        return None
    ref = float(closes[-1 - n])
    return (now_px - ref) / ref * 100.0 if ref > 0 else None


def assess(*, call: bool, ask: float, bid: float | None, signal_premium: float | None,
           stop: float | None, target: float | None, option_closes: list[float],
           underlying_closes: list[float]) -> dict:
    """``option_closes``/``underlying_closes``: 1-min closes, oldest first, the
    last one being the current (still forming) minute."""
    rec: dict = {"ask": round(ask, 2), "bid": bid, "signal_premium": signal_premium,
                 "stop": stop, "target": target, "reasons": []}
    for n in WINDOWS:
        v = _chg_pct(option_closes, n, ask)
        rec[f"prem_chg_{n}m_pct"] = round(v, 2) if v is not None else None
    u = [float(c) for c in underlying_closes[-11:] if c]
    if len(u) >= 2:
        sign = 1.0 if call else -1.0
        start_i = (min if call else max)(range(len(u)), key=lambda i: u[i])
        moved = (u[-1] - u[start_i]) * sign
        rec.update(und_move_pts=round(moved, 2),
                   und_move_pct=round(moved / u[start_i] * 100.0, 3) if u[start_i] else None,
                   und_move_age_min=len(u) - 1 - start_i)
    if signal_premium and signal_premium > 0:
        rec["chase_pts"] = round(ask - float(signal_premium), 2)
    if bid and ask > 0:
        rec["spread_pct"] = round((ask - bid) / ask * 100.0, 2)
    rr = None
    if stop is not None and target is not None and ask > stop:
        rr = (float(target) - ask) / (ask - float(stop))
        rec["reward_risk"] = round(rr, 2)

    for n, lim in (settings.late_entry_max_prem_rise_pct or {}).items():
        v = rec.get(f"prem_chg_{int(n)}m_pct")
        if lim and v is not None and v >= float(lim):
            rec["reasons"].append(f"EXHAUSTED_PREMIUM: +{v:.1f}% in {int(n)}m >= {float(lim):g}%")
    lim_u = settings.late_entry_max_underlying_move_pct
    if lim_u > 0 and (rec.get("und_move_pct") or 0.0) >= lim_u:
        rec["reasons"].append(f"EXHAUSTED_UNDERLYING: {rec['und_move_pct']:.2f}% >= {lim_u:g}%")
    if settings.late_entry_min_reward_risk > 0:
        if rr is None:
            rec["reasons"].append("NO_REWARD_RISK: stop/T1 unknown or ask below stop")
        elif rr < settings.late_entry_min_reward_risk:
            rec["reasons"].append(
                f"LOW_REWARD_RISK: {rr:.2f} < {settings.late_entry_min_reward_risk:g}")
    lim_s = settings.late_entry_max_spread_pct
    if lim_s > 0 and (rec.get("spread_pct") or 0.0) > lim_s:
        rec["reasons"].append(f"WIDE_SPREAD: {rec['spread_pct']:.2f}% > {lim_s:g}%")
    rec["late"] = bool(rec["reasons"])
    rec["verdict"] = "LATE_ENTRY" if rec["late"] else "OK"
    return rec
