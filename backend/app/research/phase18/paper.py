"""The CAS paper book — §15, §16, §19. No order path exists in this file.

There is no broker client imported here, no ``buy``, no ``place_order``, and no
flag that turns this into a real book. That is structural, not a policy: the only
prices this module can act on are quotes already recorded on disk, and the only
thing it can write is a JSONL row. The existing Zero-to-Hero sleeve reaches
``self.buy(...)`` through the state machine, which is exactly why CAS was built
as a separate strategy family rather than as an option on that one.

Five entry strategies are run in parallel on the same window (§15), so they are
compared on identical books rather than on different days:

    A  buy before the auction opens (15:10-15:14)
    B  buy at the auction open (15:15)
    C  buy after the first confirmed move
    D  buy a pullback inside the auction
    E  buy only when the spread and economics clear a floor

Every leg is settled by :mod:`execution`: in at the ask, out at the bid, costed,
with the slippage grid applied. A leg that cannot be priced off a two-sided book
is closed as ``UNKNOWN`` and contributes to no P&L statistic — it is counted in
reconciliation instead, so it cannot vanish.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.research.phase18 import execution, quality, schema, session

STRATEGIES: tuple[str, ...] = ("A", "B", "C", "D", "E")
STRATEGY_LABEL: dict[str, str] = {
    "A": "buy before CAS (15:10-15:14)",
    "B": "buy at CAS open (15:15)",
    "C": "buy after first confirmed move",
    "D": "buy pullback during CAS",
    "E": "buy only when spread/economics acceptable",
}

# Entry-timing experiment clocks (§8), minutes of day.
ENTRY_TIMES: tuple[str, ...] = ("15:10", "15:12", "15:15", "15:17", "15:20")

# Exit variants (§19).
BASELINE = "BASELINE"
FIXED_PROFIT = "FIXED_PROFIT"
TRAIL = "TRAIL"
GIVEBACK = "GIVEBACK"
PARTIAL = "PARTIAL"
BREAKEVEN = "BREAKEVEN"
HYBRID = "HYBRID"
EXIT_VARIANTS: tuple[str, ...] = (
    BASELINE, FIXED_PROFIT, TRAIL, GIVEBACK, PARTIAL, BREAKEVEN, HYBRID,
)

# A "confirmed move" for strategy C: the window has travelled this share of ATR.
CONFIRM_ATR = 0.25
# A pullback for strategy D: premium has retraced this share of its excursion.
PULLBACK_FRACTION = 0.35
# Strategy E's economics floor.
E_MAX_SPREAD_PCT = 2.0
# Giveback and trail thresholds, in fractions of the peak gain.
GIVEBACK_FRACTION = 0.40
TRAIL_FRACTION = 0.30
FIXED_PROFIT_R = 1.0


def _f(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if f != f else f


@dataclass
class Leg:
    """One paper position, from signal to resolution."""

    episode_id: str
    session: str
    instrument: str
    strategy_variant: str
    option_type: str
    rung: str
    strike: float | None
    symbol: str | None
    expiry: str | None
    days_to_expiry: int | None
    expiry_class: str | None
    signal_ts: float
    entry_ts: float | None = None
    entry_quote: dict | None = None
    entry_price: float | None = None
    stop: float | None = None
    t1: float | None = None
    t2: float | None = None
    t3: float | None = None
    status: str = schema.WAIT
    peak_bid: float | None = None
    trough_bid: float | None = None
    peak_ts: float | None = None
    t1_ts: float | None = None
    exit_ts: float | None = None
    exit_quote: dict | None = None
    peak_quote: dict | None = None
    overnight: bool = False
    milestones: list[str] = field(default_factory=list)
    data_quality: str = quality.MISSING

    def to_dict(self) -> dict:
        return {
            "strategy": schema.STRATEGY,
            "paper_only": True,
            "no_real_order": True,
            "episode_id": self.episode_id,
            "session": self.session,
            "instrument": self.instrument,
            "strategy_variant": self.strategy_variant,
            "strategy_label": STRATEGY_LABEL.get(self.strategy_variant),
            "option_type": self.option_type,
            "rung": self.rung,
            "strike": self.strike,
            "symbol": self.symbol,
            "expiry": self.expiry,
            "days_to_expiry": self.days_to_expiry,
            "expiry_class": self.expiry_class,
            "signal_ts": self.signal_ts,
            "entry_ts": self.entry_ts,
            "entry_price": self.entry_price,
            "stop": self.stop,
            "t1": self.t1,
            "t2": self.t2,
            "t3": self.t3,
            "status": self.status,
            "peak_bid": self.peak_bid,
            "trough_bid": self.trough_bid,
            "exit_ts": self.exit_ts,
            "overnight": self.overnight,
            "milestones": list(self.milestones),
            "data_quality": self.data_quality,
        }


def should_enter(
    variant: str,
    *,
    ts: float,
    window_prices: list[float],
    atr: float | None,
    premium_series: list[float],
    spread_pct: float | None,
) -> tuple[bool, str]:
    """Whether ``variant`` takes the entry at this instant, and why."""
    state = session.state(ts)
    sub = session.sub_window(ts)
    if variant == "A":
        ok = sub == "W_1510_1515"
        return ok, "pre-auction window" if ok else "not in 15:10-15:15"
    if variant == "B":
        ok = sub == "W_1515_1520"
        return ok, "auction open" if ok else "not at the auction open"
    if variant == "C":
        if len(window_prices) < 2:
            return False, "no in-window move yet"
        move = window_prices[-1] - window_prices[0]
        if atr and atr > 0:
            frac = abs(move) / atr
            return (
                frac >= CONFIRM_ATR,
                f"in-window move {frac:.2f} ATR vs {CONFIRM_ATR} needed",
            )
        return False, "no ATR — cannot define a confirmed move"
    if variant == "D":
        if len(premium_series) < 3:
            return False, "no premium history to retrace"
        peak = max(premium_series)
        base = min(premium_series)
        last = premium_series[-1]
        if peak <= base:
            return False, "premium flat"
        retrace = (peak - last) / (peak - base)
        return (
            retrace >= PULLBACK_FRACTION,
            f"retraced {retrace:.0%} of the excursion",
        )
    if variant == "E":
        if spread_pct is None:
            return False, "no book — economics unknown"
        ok = spread_pct <= E_MAX_SPREAD_PCT and state != session.OUTSIDE
        return ok, f"spread {spread_pct:.1f}% vs {E_MAX_SPREAD_PCT}% floor"
    return False, f"unknown variant {variant}"


def update(leg: Leg, quote: dict | None, ts: float) -> Leg:
    """Advance one open leg against a fresh quote.

    Marks are taken off the **bid**, because the bid is what the position could
    have been sold into. Using the mid here would make every MFE optimistic and
    every giveback statistic wrong in the same direction.
    """
    if leg.status in schema.CLOSED or not quote:
        return leg
    bid = _f(quote.get("bid"))
    state = str(quote.get("data_quality") or quality.MISSING)
    if bid is None or state not in quality.USABLE:
        return leg

    if leg.peak_bid is None or bid > leg.peak_bid:
        leg.peak_bid, leg.peak_ts, leg.peak_quote = bid, ts, quote
    if leg.trough_bid is None or bid < leg.trough_bid:
        leg.trough_bid = bid

    for level, name in ((leg.t3, schema.T3), (leg.t2, schema.T2), (leg.t1, schema.T1)):
        if level is not None and bid >= level and name not in leg.milestones:
            leg.milestones.append(name)
            if name == schema.T1 and leg.t1_ts is None:
                leg.t1_ts = ts

    if leg.stop is not None and bid <= leg.stop:
        leg.status, leg.exit_ts, leg.exit_quote = schema.SL, ts, quote
        return leg
    if schema.T3 in leg.milestones:
        leg.status, leg.exit_ts, leg.exit_quote = schema.EXIT, ts, quote
        return leg
    leg.status = schema.HOLD
    return leg


def close(leg: Leg, quote: dict | None, ts: float, *, status: str) -> Leg:
    leg.status = status
    leg.exit_ts = ts
    leg.exit_quote = quote
    return leg


def exit_variants(leg: Leg, series: list[dict]) -> dict:
    """§19: what each exit rule would have realised on the same recorded path.

    All seven run on one leg's actual quote series, so the comparison is not
    contaminated by different days or different entries. None of them touches the
    production exit rules, which are a different engine on a different strategy.
    """
    if leg.entry_price is None or not series:
        return {v: {"exit_bid": None, "reason": "no entry or no path"}
                for v in EXIT_VARIANTS}

    entry = leg.entry_price
    risk = max(0.05, entry - (leg.stop if leg.stop is not None else entry * 0.5))
    out: dict[str, dict] = {}
    bids = [(s["ts"], s["bid"]) for s in series
            if isinstance(s.get("bid"), (int, float))]
    if not bids:
        return {v: {"exit_bid": None, "reason": "no bid anywhere on the path"}
                for v in EXIT_VARIANTS}

    def first_where(pred) -> tuple[float, float] | None:
        peak = entry
        for ts, b in bids:
            peak = max(peak, b)
            if pred(b, peak):
                return ts, b
        return None

    last_ts, last_bid = bids[-1]
    peak_bid = max(b for _, b in bids)

    out[BASELINE] = {"exit_bid": last_bid, "exit_ts": last_ts,
                     "reason": "held to the end of the window"}

    hit = first_where(lambda b, _p: b >= entry + FIXED_PROFIT_R * risk)
    out[FIXED_PROFIT] = (
        {"exit_bid": hit[1], "exit_ts": hit[0],
         "reason": f"+{FIXED_PROFIT_R}R reached"}
        if hit else {"exit_bid": last_bid, "exit_ts": last_ts,
                     "reason": "target never reached — held to the end"}
    )

    hit = first_where(
        lambda b, p: p > entry and b <= p - TRAIL_FRACTION * (p - entry)
    )
    out[TRAIL] = (
        {"exit_bid": hit[1], "exit_ts": hit[0],
         "reason": f"trailed {TRAIL_FRACTION:.0%} off the peak"}
        if hit else {"exit_bid": last_bid, "exit_ts": last_ts,
                     "reason": "never trailed out"}
    )

    hit = first_where(
        lambda b, p: p > entry and b <= p - GIVEBACK_FRACTION * (p - entry)
    )
    out[GIVEBACK] = (
        {"exit_bid": hit[1], "exit_ts": hit[0],
         "reason": f"gave back {GIVEBACK_FRACTION:.0%} of the peak gain"}
        if hit else {"exit_bid": last_bid, "exit_ts": last_ts,
                     "reason": "never gave back the threshold"}
    )

    half = first_where(lambda b, _p: b >= entry + 0.5 * risk)
    out[PARTIAL] = {
        "exit_bid": (
            round(0.5 * half[1] + 0.5 * last_bid, 2) if half else last_bid
        ),
        "exit_ts": last_ts,
        "reason": (
            "half out at +0.5R, remainder to the end" if half
            else "+0.5R never reached — full size to the end"
        ),
    }

    be = first_where(lambda b, p: p >= entry + 0.5 * risk and b <= entry)
    out[BREAKEVEN] = (
        {"exit_bid": be[1], "exit_ts": be[0],
         "reason": "stop moved to breakeven after +0.5R and hit"}
        if be else {"exit_bid": last_bid, "exit_ts": last_ts,
                    "reason": "breakeven stop never touched"}
    )

    hyb = first_where(
        lambda b, p: (p >= entry + 0.5 * risk and b <= entry)
        or (p > entry and b <= p - TRAIL_FRACTION * (p - entry))
    )
    out[HYBRID] = (
        {"exit_bid": hyb[1], "exit_ts": hyb[0],
         "reason": "breakeven after +0.5R, then a trail"}
        if hyb else {"exit_bid": last_bid, "exit_ts": last_ts,
                     "reason": "neither rule triggered"}
    )

    for v, row in out.items():
        b = row.get("exit_bid")
        row["net_points"] = None if b is None else round(float(b) - entry, 2)
        row["r_multiple"] = (
            None if b is None else round((float(b) - entry) / risk, 3)
        )
        row["capture_pct"] = (
            None if b is None or peak_bid <= entry
            else round(100.0 * (float(b) - entry) / (peak_bid - entry), 1)
        )
    return out


def settle_leg(leg: Leg, *, lots: int = 1, slippage_ticks: int = 0) -> dict:
    """The resolved row for one leg: three columns, costs, R, MFE/MAE, timings."""
    s = execution.settle(
        instrument=leg.instrument,
        entry_quote=leg.entry_quote,
        exit_quote=leg.exit_quote,
        peak_quote=leg.peak_quote,
        lots=lots,
        slippage_ticks=slippage_ticks,
    )
    row = leg.to_dict()
    row.update({
        "execution": s,
        "executability": s.get("executability"),
        "net_rupees": s.get("net_rupees"),
        "gross_rupees": s.get("gross_rupees"),
        "theoretical_rupees": s.get("theoretical_rupees"),
        "bid_executable_rupees": s.get("bid_executable_rupees"),
        "vanished_rupees": s.get("vanished_rupees"),
        "cost_rupees": s.get("cost_rupees"),
        "slippage_ticks": slippage_ticks,
    })

    entry = leg.entry_price
    if entry and entry > 0:
        risk = max(0.05, entry - (leg.stop if leg.stop is not None else entry * 0.5))
        net_pts = s.get("net_points")
        row["net_r"] = (
            None if not isinstance(net_pts, (int, float))
            else round(float(net_pts) / risk, 3)
        )
        row["mfe_points"] = (
            None if leg.peak_bid is None else round(leg.peak_bid - entry, 2)
        )
        row["mae_points"] = (
            None if leg.trough_bid is None else round(leg.trough_bid - entry, 2)
        )
        row["mfe_capture_pct"] = (
            None
            if leg.peak_bid is None or not isinstance(net_pts, (int, float))
            or leg.peak_bid <= entry
            else round(100.0 * float(net_pts) / (leg.peak_bid - entry), 1)
        )
        row["giveback_points"] = (
            None
            if leg.peak_bid is None or not isinstance(net_pts, (int, float))
            else round((leg.peak_bid - entry) - float(net_pts), 2)
        )
    row["seconds_to_t1"] = (
        None if leg.t1_ts is None or leg.entry_ts is None
        else round(leg.t1_ts - leg.entry_ts)
    )
    row["seconds_to_peak"] = (
        None if leg.peak_ts is None or leg.entry_ts is None
        else round(leg.peak_ts - leg.entry_ts)
    )
    row["hold_seconds"] = (
        None if leg.exit_ts is None or leg.entry_ts is None
        else round(leg.exit_ts - leg.entry_ts)
    )
    row["seconds_to_sl"] = (
        row["hold_seconds"] if leg.status == schema.SL else None
    )
    row["slippage_grid"] = execution.slippage_table(
        instrument=leg.instrument, entry_quote=leg.entry_quote,
        exit_quote=leg.exit_quote, lots=lots,
    )["grid"]
    row["outcome"] = _outcome(row)
    return row


def _outcome(row: dict) -> str:
    if row.get("executability") != schema.EXECUTABLE:
        return schema.EXECUTABILITY_UNKNOWN
    net = row.get("net_rupees")
    if not isinstance(net, (int, float)):
        return schema.UNKNOWN
    if row.get("status") == schema.SL:
        return "LOSS_STOPPED"
    return "WIN_NET" if float(net) > 0 else "LOSS_NET"
