"""Per-bar exit simulation while the replay walks a candidate forward.

RESEARCH ONLY. Nothing here is read by the live engine; it adds fields to a
saved pool row.

Why this exists. Grading exit rules from a row's recorded ``mfe_r``/``mae_r``
answers only the rules that depend on the peak alone. It cannot answer any rule
that depends on *when* the peak happened relative to a later dip — a trailing
stop, or a breakeven stop that gets tagged out at entry and then watches the
trade run to target without it. Estimating from excursions credits the breakeven
stop with every rescued loser and charges it for none of the winners it cuts
short, which biases it upward by exactly the amount under test.

So each variant is simulated here, bar by bar, in the order the bars happened,
and the resulting R is written onto the row as ``alt_exits``. Two conventions,
both chosen against the variant rather than for it:

* **Adverse first within a bar.** When one candle's range covers both a
  variant's profit trigger and its stop, minute OHLC cannot say which came
  first, so the adverse side is taken — the same convention the replay already
  uses for stop-versus-target. A breakeven stop is therefore not armed on a bar
  that also dipped to entry, and a partial is not banked on a bar that also hit
  the stop.
* **A trail does not tighten the original stop.** The giveback only starts
  trailing once the peak is at least the giveback itself, so the trailing level
  is never worse than entry and never tighter than the plan's stop.
"""
from __future__ import annotations

from app.models import Candle

LONG = "LONG"

# Kept identical to the levels the Phase 15 exit study grades, and kept few and
# round: a grid of forty thresholds fitted to one book is a fitted curve.
TARGET_LEVELS = (0.5, 0.75, 1.0, 1.5)
BREAKEVEN_LEVELS = (0.5, 1.0)
PARTIAL_LEVELS = (0.5, 1.0)
TRAIL_GIVEBACKS = (0.5, 1.0)

# The plan's own stop, in R. Risk is defined as |entry - stop|, so the stop is
# always exactly -1R and a variant's own level is compared against it.
STOP_R = -1.0

PATH_MEASURED = "PATH_MEASURED"


def names() -> tuple[str, ...]:
    """Every variant this module prices, in report order."""
    return (
        tuple(f"target_at_{lvl}R" for lvl in TARGET_LEVELS)
        + tuple(f"breakeven_after_{lvl}R" for lvl in BREAKEVEN_LEVELS)
        + tuple(f"partial_half_at_{lvl}R" for lvl in PARTIAL_LEVELS)
        + tuple(f"trail_giveback_{g}R" for g in TRAIL_GIVEBACKS)
    )


def start(trade: dict) -> dict:
    """Fresh state for one candidate."""
    return {
        "bars": 0,
        "peak_r": 0.0,
        "peak_bar": 0,
        "trough_r": 0.0,
        "trough_bar": 0,
        "armed": {lvl: False for lvl in BREAKEVEN_LEVELS},
        "banked": {lvl: False for lvl in PARTIAL_LEVELS},
        "trailing": {g: False for g in TRAIL_GIVEBACKS},
        "closed": {},
        "retouched_entry_after": {lvl: False for lvl in BREAKEVEN_LEVELS},
    }


def _excursions(trade: dict, bar: Candle) -> tuple[float, float]:
    """This bar's best and worst reading for the trade, in R."""
    entry = float(trade["entry"])
    risk = float(trade["risk"])
    if trade["side"] == LONG:
        fav, adv = float(bar.high) - entry, float(bar.low) - entry
    else:
        fav, adv = entry - float(bar.low), entry - float(bar.high)
    return fav / risk, adv / risk


def step(state: dict, trade: dict, bar: Candle) -> None:
    """Advance every still-open variant across one bar."""
    fav_r, adv_r = _excursions(trade, bar)
    state["bars"] += 1
    peak_before = state["peak_r"]
    closed = state["closed"]

    for lvl in TARGET_LEVELS:
        name = f"target_at_{lvl}R"
        if name in closed:
            continue
        if adv_r <= STOP_R:
            closed[name] = STOP_R
        elif fav_r >= lvl:
            closed[name] = lvl

    for lvl in BREAKEVEN_LEVELS:
        name = f"breakeven_after_{lvl}R"
        if state["armed"][lvl] and adv_r <= 0.0:
            state["retouched_entry_after"][lvl] = True
        if name in closed:
            continue
        if state["armed"][lvl]:
            # Entry sits above the stop, so a fall to the stop passes breakeven
            # on the way: the scratch fires, not the stop.
            if adv_r <= 0.0:
                closed[name] = 0.0
        elif adv_r <= STOP_R:
            closed[name] = STOP_R
        elif adv_r > 0.0 and fav_r >= lvl:
            state["armed"][lvl] = True

    for lvl in PARTIAL_LEVELS:
        if state["banked"][lvl] or adv_r <= STOP_R:
            continue
        if fav_r >= lvl:
            state["banked"][lvl] = True

    for g in TRAIL_GIVEBACKS:
        name = f"trail_giveback_{g}R"
        if name in closed:
            continue
        if state["trailing"][g]:
            # Measured against the peak as it stood when this bar opened: the
            # peak and the dip inside one candle have no known order.
            level = peak_before - g
            if adv_r <= level:
                closed[name] = round(level, 4)
                continue
        if adv_r <= STOP_R:
            closed[name] = STOP_R
            continue
        if fav_r >= g:
            state["trailing"][g] = True

    if fav_r > state["peak_r"]:
        state["peak_r"] = fav_r
        state["peak_bar"] = state["bars"]
    if adv_r < state["trough_r"]:
        state["trough_r"] = adv_r
        state["trough_bar"] = state["bars"]


def finish(state: dict, trade: dict) -> None:
    """Close every open variant at the trade's real outcome and write the row.

    A variant that never triggered is worth what the trade itself made — that is
    what "the same trade with this exit rule" means, and it keeps the comparison
    against the traded book honest.
    """
    actual = trade.get("r")
    if actual is None:
        return
    actual = float(actual)
    closed = state["closed"]
    alt: dict[str, float] = {}
    for name in names():
        if name.startswith("partial_half_at_"):
            lvl = float(name[len("partial_half_at_"):-1])
            alt[name] = (round(0.5 * lvl + 0.5 * actual, 4)
                         if state["banked"][lvl] else actual)
            continue
        alt[name] = round(float(closed.get(name, actual)), 4)
    trade["alt_exits"] = alt
    trade["alt_exits_basis"] = PATH_MEASURED
    trade["path_facts"] = {
        "bars_to_peak": state["peak_bar"],
        "bars_to_trough": state["trough_bar"],
        "peak_before_trough": state["peak_bar"] <= state["trough_bar"],
        "retouched_entry_after": {
            f"{lvl}R": state["retouched_entry_after"][lvl]
            for lvl in BREAKEVEN_LEVELS
        },
    }
