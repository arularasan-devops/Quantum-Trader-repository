"""Phase 7 Part 6, 8, 14, 16 — research-only entry and exit policies. RESEARCH ONLY.

Every policy here is a pure function of a reconstructed path. None of them is
wired into the production engine, and Part 9 of the spec is the reason: with one
recorded session and a handful of BUYs, the best-looking policy is the one that
fits that session, and picking it would be the most expensive kind of progress.

Two rules are enforced in the code rather than in the prose:

* a policy may only read quotes at or after the timestamp it acts on, so no
  policy can "exit at the MFE" using knowledge of where the MFE was;
* the stop is preserved in every exit policy — a research variant may leave
  earlier than the baseline, never later, and never wider.
"""
from __future__ import annotations

from dataclasses import dataclass

from .paths import BuyEvent, OptionPath

# Part 16 — the cost model. These are ASSUMPTIONS, printed with every result: the
# real numbers need one contract note, which has not been supplied. Options costs
# are per round trip on one lot; nothing here claims to be the user's brokerage.
COST_PER_ROUNDTRIP_OPTIONS = 120.0
COST_PER_ROUNDTRIP_FUTURES = 1200.0

ENTRY_POLICIES = ("CONTROL", "PULLBACK", "VWAP_RETEST", "EMA9_RETEST",
                  "EMA20_RETEST", "PREV_MID", "BREAKOUT_RETEST", "ENTRY_QUALITY",
                  "SWING_RETEST", "FVG_RETEST", "NEXT_CANDLE_CONFIRM")
# NEXT_CANDLE_CONFIRM waits for the next bar to confirm the setup instead of
# waiting for a cheaper price, so it can pay MORE than the signal price. It is
# scored on the same footing as the retests precisely so that cost is visible.
CONFIRMATION_POLICIES = ("NEXT_CANDLE_CONFIRM",)
EXIT_POLICIES = ("A_BASELINE", "B_FIXED_2PCT", "B_FIXED_3PCT", "B_FIXED_5PCT",
                 "B_FIXED_7PCT", "C_TRAIL_30", "C_TRAIL_50", "D_MOMENTUM_FAIL",
                 "E_TIMEOUT_10", "E_STAGNATION", "F_HYBRID_HALF")

# Entry policies wait for a better price. ``WAIT_BARS`` is how long they are
# allowed to wait before the signal is abandoned as a missed trade — the same
# budget for all of them, so the comparison is not a comparison of patience.
WAIT_BARS = 5


@dataclass
class Fill:
    """The outcome of one policy pair on one BUY event."""

    entered: bool
    signal_entry: float = 0.0
    entry: float = 0.0
    stop: float = 0.0
    entry_ts: int = 0
    delay_bars: int = 0
    exit: float = 0.0
    exit_ts: int = 0
    exit_reason: str = "NONE"
    r: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    held_min: float = 0.0
    entered_at_peak: bool = False


def _entry_trigger(ev: BuyEvent, name: str) -> float | None:
    """The premium a policy is willing to pay, or None for "take the signal price".

    Underlying-level policies (VWAP / EMA / midpoint / breakout retest) are
    translated into a premium limit through the leg's own quoted sensitivity to
    the underlying: the recorded snapshot carries delta, so a retest level maps
    to a premium without a model.
    """
    if name == "CONTROL":
        return None
    if name == "PULLBACK":
        # A pullback of a fifth of the intended risk — expressed in the trade's
        # own risk unit so it scales with the stop the engine chose.
        return ev.entry - 0.2 * max(0.01, ev.entry - ev.stop)
    if name == "ENTRY_QUALITY":
        return ev.entry - 0.1 * max(0.01, ev.entry - ev.stop)
    if name == "SWING_RETEST":
        level = ev.swing_low if ev.side == "CE" else ev.swing_high
    elif name == "FVG_RETEST":
        level = ev.fvg_bull if ev.side == "CE" else ev.fvg_bear
    else:
        level = {"VWAP_RETEST": ev.vwap, "EMA9_RETEST": ev.ema9,
                 "EMA20_RETEST": ev.ema20, "PREV_MID": ev.prev_mid,
                 "BREAKOUT_RETEST": ev.breakout_level}.get(name)
    if not level:
        # No such level in the window (no swing pivot yet, no unfilled gap): the
        # policy declines rather than aiming at a substitute level.
        return None
    move = ev.spot - level if ev.side == "CE" else level - ev.spot
    if move <= 0:
        # The level is already beyond price in the direction of the trade, so a
        # retest would mean the trade is wrong, not cheaper.
        return None
    # The recorded leg carries its own delta, so an underlying level maps to a
    # premium without a pricing model. Without a delta the policy declines rather
    # than guessing a sensitivity.
    if ev.leg_delta <= 0:
        return None
    limit = ev.entry - move * ev.leg_delta
    return limit if limit > 0.05 else None


def apply_entry(ev: BuyEvent, name: str) -> tuple[bool, float, int, int]:
    """(entered, price, ts, delay_bars) for one entry policy."""
    path = ev.path
    if path is None or not path.quotes:
        return False, 0.0, 0, 0
    if name == "CONTROL":
        return True, ev.entry, ev.ts, 0
    if name == "NEXT_CANDLE_CONFIRM":
        # The post's rule for reversal candles: take the setup only if the next
        # bar confirms it. Reads one quote after the signal and pays that quote —
        # no lookahead, and no cheaper price assumed.
        ts, px = path.quotes[0]
        if px <= ev.entry:
            return False, 0.0, 0, 0
        return True, px, ts, 1
    limit = _entry_trigger(ev, name)
    if limit is None:
        return False, 0.0, 0, 0
    for n, (ts, px) in enumerate(path.quotes[:WAIT_BARS], start=1):
        if px <= limit:
            return True, px, ts, n
    return False, 0.0, 0, 0


def _momentum_failed(quotes: list[tuple[int, float]], k: int) -> bool:
    """Two consecutive lower quotes after a favourable excursion — the cheapest
    honest definition of "the move stopped working" on a 60s premium series."""
    if k < 2:
        return False
    return quotes[k][1] < quotes[k - 1][1] < quotes[k - 2][1]


def apply_exit(path: OptionPath, entry: float, entry_ts: int, stop: float,
               target: float, name: str) -> tuple[float, int, str]:
    """(price, ts, reason) for one exit policy, walking forward only."""
    quotes = [(ts, px) for ts, px in path.quotes if ts >= entry_ts]
    if not quotes:
        return entry, entry_ts, "NO_FORWARD_QUOTES"
    risk = max(0.01, entry - stop)
    peak = entry
    stagnant = 0
    for k, (ts, px) in enumerate(quotes):
        if px > peak:
            peak = px
            stagnant = 0
        else:
            stagnant += 1
        # Stop first and target last: every policy keeps the production stop and
        # the production target, and may only add an EARLIER exit between them.
        if px <= stop:
            return px, ts, "STOP"
        gain_pct = 100.0 * (px - entry) / entry
        if name.startswith("B_FIXED_"):
            want = float(name.rsplit("_", 1)[1].replace("PCT", ""))
            if gain_pct >= want:
                return px, ts, f"FIXED_{want:g}PCT"
        elif name.startswith("C_TRAIL_"):
            frac = float(name.rsplit("_", 1)[1]) / 100.0
            if peak - entry >= 0.5 * risk and px <= peak - frac * (peak - entry):
                return px, ts, f"TRAIL_{int(frac * 100)}"
        elif name == "D_MOMENTUM_FAIL":
            if peak - entry >= 0.3 * risk and _momentum_failed(quotes, k):
                return px, ts, "MOMENTUM_FAIL"
        elif name == "E_TIMEOUT_10":
            if k >= 9:
                return px, ts, "TIMEOUT_10"
        elif name == "E_STAGNATION":
            if stagnant >= 5:
                return px, ts, "STAGNATION_5"
        elif name == "F_HYBRID_HALF":
            # Half off at +1R, the remainder trailed 40% from its peak. Reported
            # as one blended price so it stays comparable with the others.
            if peak - entry >= 1.0 * risk and px <= peak - 0.4 * (peak - entry):
                first = entry + 1.0 * risk
                return (first + px) / 2.0, ts, "HYBRID_HALF"
        if px >= target:
            return px, ts, "TARGET1"
    ts, px = quotes[-1]
    return px, ts, "HORIZON_END"


def simulate(ev: BuyEvent, entry_policy: str, exit_policy: str) -> Fill:
    """One BUY under one entry × exit pair (Part 14's grid cell)."""
    entered, price, ts, delay = apply_entry(ev, entry_policy)
    if not entered or ev.path is None:
        return Fill(entered=False)
    risk_frac = max(0.01, ev.entry - ev.stop) / ev.entry
    stop = price * (1.0 - risk_frac)
    rr = (ev.target1 - ev.entry) / max(0.01, ev.entry - ev.stop)
    target = price + (price - stop) * rr
    px, exit_ts, reason = apply_exit(ev.path, price, ts, stop, target, exit_policy)
    risk = max(0.01, price - stop)
    fwd = [q for q in ev.path.quotes if ts <= q[0] <= exit_ts]
    best = max((p for _, p in fwd), default=price)
    worst = min((p for _, p in fwd), default=price)
    return Fill(
        entered=True, signal_entry=ev.entry, entry=price, stop=stop, entry_ts=ts,
        delay_bars=delay,
        exit=px, exit_ts=exit_ts, exit_reason=reason,
        r=(px - price) / risk, mfe_r=(best - price) / risk,
        mae_r=(worst - price) / risk,
        held_min=max(0.0, (exit_ts - ts) / 60.0),
        entered_at_peak=bool(best <= price + 1e-9),
    )


def summarise(fills: list[Fill], events: int, lot_size: int = 1,
              cost: float = COST_PER_ROUNDTRIP_OPTIONS) -> dict:
    """The metric block Part 6/8 asks for, on one policy pair."""
    taken = [f for f in fills if f.entered]
    rs = [f.r for f in taken]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross = sum(wins)
    bad = -sum(losses)
    equity = 0.0
    peak = 0.0
    dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    rupees = [
        (f.exit - f.entry) * lot_size - cost for f in taken
    ] if lot_size > 1 else []
    return {
        "events": events,
        "entered": len(taken),
        "entry_rate_pct": round(100.0 * len(taken) / max(1, events), 1),
        "missed_pct": round(100.0 * (events - len(taken)) / max(1, events), 1),
        "avg_delay_bars": round(sum(f.delay_bars for f in taken) / max(1, len(taken)), 2),
        # Positive = the policy paid LESS than the signal price for the same event.
        "avg_entry_improvement_pct": round(sum(
            100.0 * (f.signal_entry - f.entry) / f.signal_entry
            for f in taken) / max(1, len(taken)), 3) if taken else None,
        "target_before_stop_pct": round(100.0 * sum(
            1 for f in taken if f.exit_reason in ("TARGET1",)) / max(1, len(taken)), 1),
        "stopped_pct": round(100.0 * sum(
            1 for f in taken if f.exit_reason == "STOP") / max(1, len(taken)), 1),
        "expectancy_r": round(sum(rs) / len(rs), 3) if rs else None,
        "profit_factor": round(gross / bad, 3) if bad > 0 else None,
        "win_rate_pct": round(100.0 * len(wins) / len(rs), 1) if rs else None,
        "median_mfe_r": median([f.mfe_r for f in taken]),
        "median_mae_r": median([f.mae_r for f in taken]),
        "max_drawdown_r": round(dd, 3),
        "peak_entry_rate_pct": round(100.0 * sum(
            1 for f in taken if f.entered_at_peak) / max(1, len(taken)), 1),
        "median_hold_min": median([f.held_min for f in taken]),
        # A stop can only be acted on at the next recorded quote, so a stopped
        # trade loses MORE than 1R by construction. This is the size of that
        # effect in the data, not an assumption about slippage.
        "median_stop_overshoot_r": median([
            (f.stop - f.exit) / max(0.01, f.entry - f.stop)
            for f in taken if f.exit_reason == "STOP"]),
        "net_rupees_one_lot": round(sum(rupees), 0) if rupees else None,
        "cost_assumption_per_roundtrip": cost,
    }


def median(xs: list[float]) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    mid = len(ys) // 2
    return round(ys[mid] if len(ys) % 2 else (ys[mid - 1] + ys[mid]) / 2.0, 3)


def opportunity_cost(events: list[BuyEvent], fills: list[Fill],
                     exit_policy: str = "A_BASELINE") -> dict:
    """Part 4 — what one entry policy gave up on the signals it declined.

    A policy that waits looks better on the trades it filled and pays nothing for
    the ones it never entered, so every declined signal is charged here at what
    the immediate entry actually made and the excursion it actually reached.
    """
    declined = [ev for ev, f in zip(events, fills) if not f.entered]
    control = [simulate(ev, "CONTROL", exit_policy) for ev in declined]
    taken = [f for f in control if f.entered]
    rs = [f.r for f in taken]
    return {
        "declined": len(declined),
        "declined_priceable": len(taken),
        "missed_winners": sum(1 for r in rs if r > 0),
        "missed_median_mfe_r": median([f.mfe_r for f in taken]),
        "control_expectancy_on_declined_r": (
            round(sum(rs) / len(rs), 3) if rs else None),
        "note": ("the declined signals priced as the immediate entry would have "
                 "experienced them; a positive control expectancy here is the "
                 "cost of the policy's patience, not a saving"),
    }


def entry_study(events: list[BuyEvent], exit_policy: str = "A_BASELINE") -> dict:
    study: dict[str, dict] = {}
    for name in ENTRY_POLICIES:
        fills = [simulate(ev, name, exit_policy) for ev in events]
        block = summarise(fills, len(events))
        block["opportunity_cost"] = opportunity_cost(events, fills, exit_policy)
        block["pays_up_for_confirmation"] = name in CONFIRMATION_POLICIES
        study[name] = block
    return study


def exit_study(events: list[BuyEvent], entry_policy: str = "CONTROL") -> dict:
    return {name: summarise([simulate(ev, entry_policy, name) for ev in events],
                            len(events))
            for name in EXIT_POLICIES}


def combination_study(events: list[BuyEvent]) -> dict:
    """Part 14 — the full grid. Reported, never ranked into a recommendation:
    with this sample the best cell is a coincidence, and the spec says so."""
    grid: dict[str, dict] = {}
    for en in ENTRY_POLICIES:
        for ex in EXIT_POLICIES:
            block = summarise([simulate(ev, en, ex) for ev in events], len(events))
            grid[f"{en}|{ex}"] = {
                "entered": block["entered"],
                "expectancy_r": block["expectancy_r"],
                "profit_factor": block["profit_factor"],
                "max_drawdown_r": block["max_drawdown_r"],
            }
    return grid
