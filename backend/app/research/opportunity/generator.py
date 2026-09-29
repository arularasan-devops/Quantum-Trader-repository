"""§4/§17 — the bounded candidate generator.

A candidate is one frozen sentence: *this mechanism, with these parameters, on
this instrument, expressed through this vehicle, exited this way, costed this
way*. The generator writes all of them at once, which is the change from the
serial habit — the whole set of hypotheses exists before any of them is scored,
so the count used for the false-discovery correction is a real count and not
the number that happened to survive.

The bound is the reason this is trustworthy. Mechanism grid times exit grid
times vehicle grid times instruments is a product that reaches tens of
thousands if nobody stops it, and at that size the best-looking result is
almost certainly noise no matter how careful the statistics are. So the exit
and vehicle grids are varied on a **restricted subset** rather than crossed
with everything, :data:`MAX_CANDIDATES` is a hard ceiling, and generation stops
with a recorded ``BOUND_REACHED`` rather than quietly truncating.

The vehicle (E) and option-structure (F) families are generated with their
scope declared even though candle history cannot price them. They screen as
``UNMEASURED`` rather than as failures — the distinction between *tested and
found wanting* and *never testable on this data* is exactly what a wide funnel
tends to lose, and losing it is how an untested idea ends up in a promotion
table.
"""
from __future__ import annotations

import itertools

from app.research.opportunity import (
    CE,
    FAMILY_CAPTURE,
    FAMILY_OPTION,
    FAMILY_VEHICLE,
    FUTURES,
    MIN_SCREEN_SESSIONS,
    MIN_TRAIN_TRADES,
    PE,
    PROMOTION_MIN_SESSIONS,
    PROMOTION_MIN_TRADES,
    VERSION,
)
from app.research.opportunity import bars as oppbars
from app.research.opportunity import mechanisms as mech
from app.research.opportunity import registry, universe

# Hard ceiling on one cycle's generation. Not a performance limit — a
# statistical one. Every extra hypothesis raises the corrected bar for all the
# others, so generating cheaply is not free.
MAX_CANDIDATES = 600

# §4 G — profit capture. Each is a complete exit rule, declared before use.
#
# The target multiples are large relative to a one-minute bar range for a
# measured reason rather than a stylistic one: on NIFTY the mean 30-bar range is
# about 0.05% of price, and the modelled round trip is 0.06%. A one-ATR target
# is therefore *inside the cost* — every such trade is a loser before the market
# does anything, which is a statement about the geometry and not about the
# mechanism. So the smallest target here clears the round trip several times
# over, and a candidate whose expected move cannot reach it is rejected on cost
# feasibility instead of being run and blamed for direction.
EXITS: dict[str, dict] = {
    "T1_THEN_BREAKEVEN": {
        "t1_atr": 3.0, "stop_atr": 2.0, "breakeven_after_t1": True,
        "trail": None, "time_stop_min": 120,
    },
    "T1_THEN_TRAIL": {
        "t1_atr": 3.0, "stop_atr": 2.0, "breakeven_after_t1": False,
        "trail": {"giveback_frac": 0.5}, "time_stop_min": 120,
    },
    "TIME_STOP_ONLY": {
        "t1_atr": None, "stop_atr": 3.0, "breakeven_after_t1": False,
        "trail": None, "time_stop_min": 30,
    },
    "VOLATILITY_EXIT": {
        "t1_atr": 4.0, "stop_atr": 2.0, "breakeven_after_t1": False,
        "trail": None, "time_stop_min": 60,
    },
}
DEFAULT_EXIT = "T1_THEN_BREAKEVEN"

# The timeframe sweep. Its reason is measured rather than stylistic: the first
# cycle's rejections were not near-misses — 47 of 82 candidates had a positive
# gross edge, and the best of them on a usable sample earned +0.0194% per trade
# against a 0.0600% round trip. The round trip is charged per *trade*, so the
# only lever that changes that ratio is the size of the move, and the size of
# the move is bounded by the bar.
#
# The sweep is deliberately narrow in every direction except the one under test:
# one declared parameter set per mechanism, so the comparison is between
# timeframes rather than between 90 parameter combinations, whose best member
# would be luck. One minute is included as the controlled base arm rather than
# read across from the existing one-minute candidates, because those carry a
# minute-denominated time stop and would make the base arm a different rule.
HTF_TIMEFRAMES: tuple[int, ...] = oppbars.TIMEFRAMES

# The time stop in bars, identical at every timeframe, so each arm gets the same
# number of decision units and the wall-clock hold scales with the bar.
HTF_TIME_STOP_BARS = 30

HTF_REPRESENTATIVE: dict[str, dict] = {
    "breakout": {"lookback": 30, "buffer_atr": 0.5},
    "momentum_persistence": {"lookback": 30, "min_run": 3},
    "pullback_continuation": {"lookback": 30, "retrace": 0.5},
    "reversal": {"lookback": 30, "extreme_atr": 1.0},
    "movement_vs_cost": {"lookback": 30, "multiple": 3.0},
    "range_expansion": {"lookback": 30, "ratio": 1.5},
    "volatility_expansion": {"lookback": 30, "ratio": 1.5},
    "relative_divergence": {"lookback": 30, "z_threshold": 2.0},
}

# session_position is left out of the sweep on purpose. Its parameters are
# minutes into the session, and at sixty minutes or a daily bar there is no
# intraday position left to measure — swept anyway it would produce rows that
# look like a negative result about the mechanism instead of a statement that
# the question does not exist at that bar length.
HTF_EXCLUDED = {"session_position": (
    "parameters are minutes into the session; at 60m and daily there is no "
    "intraday position to measure"
)}

# §4 F — option structure. Declared, and unpriceable on candle history; each
# carries the reason so no reader mistakes an absence for a negative result.
OPTION_STRUCTURES: dict[str, dict] = {
    "ATM_NEAR_EXPIRY": {"strike_distance": "ATM", "expiry": "NEAREST"},
    "OTM_1_NEAR_EXPIRY": {"strike_distance": "OTM_1", "expiry": "NEAREST"},
    "ATM_NEXT_EXPIRY": {"strike_distance": "ATM", "expiry": "NEXT"},
}

COST_MODEL = {
    "model": "PER_ORDER_BROKERAGE_PLUS_STATUTORY_CHARGES_PLUS_CONFIGURED_SLIPPAGE",
    "spread": "MEASURED_WHERE_A_TWO_SIDED_BOOK_EXISTS_OTHERWISE_UNMEASURED",
    "fills": "ASK_IN_BID_OUT_FOR_LONG_OPTIONS_EXECUTABLE_SIDE_FOR_FUTURES",
    "midpoint": "NEVER",
}


def _periods(inst_row: dict) -> dict:
    """Chronological split boundaries for the instrument, in epoch seconds.

    Split on the calendar span rather than on the trade list: a candidate that
    trades more often would otherwise get a later boundary than a quiet one,
    and the two would be judged on different years while appearing in the same
    table.
    """
    from app.research.opportunity import (
        HOLDOUT_FRACTION,
        TRAIN_FRACTION,
        VALIDATION_FRACTION,
    )
    first, last = int(inst_row["first_ts"]), int(inst_row["last_ts"])
    span = max(last - first, 1)
    t_end = first + int(span * TRAIN_FRACTION)
    v_end = t_end + int(span * VALIDATION_FRACTION)
    return {
        "train_period": [first, t_end],
        "validation_period": [t_end, v_end],
        "holdout_period": [v_end, last],
        "fractions": [TRAIN_FRACTION, VALIDATION_FRACTION, HOLDOUT_FRACTION],
    }


def _definition(*, name: str, family: str, instrument: str, vehicle: str,
                mechanism: str, params: dict, exit_name: str,
                periods: dict, structure: dict | None = None,
                pair_with: str | None = None,
                timeframe: int | None = None,
                time_stop_bars: int | None = None) -> dict:
    entry = {
        "mechanism": mechanism,
        "params": dict(sorted(params.items())),
        "evaluated_on": "THE_DECISION_BAR_AND_EARLIER_BARS_ONLY",
    }
    if pair_with:
        entry["pair_with"] = pair_with
    # Written only when the sweep asked for a timeframe. A one-minute candidate
    # generated before the sweep existed keeps the definition it was
    # fingerprinted with; adding a key to every definition would re-identify
    # every candidate already on the record.
    if timeframe is not None:
        entry["timeframe_minutes"] = int(timeframe)
    exit_def = {"rule": exit_name, **EXITS[exit_name]}
    if time_stop_bars is not None:
        exit_def["time_stop_bars"] = int(time_stop_bars)
    return {
        "candidate_name": name,
        "mechanism_family": family,
        "instrument_scope": [instrument] + ([pair_with] if pair_with else []),
        "vehicle_scope": [vehicle],
        "entry_definition": entry,
        "exit_definition": exit_def,
        "cost_definition": dict(COST_MODEL, structure=structure),
        "required_inputs": (
            ["ONE_MINUTE_OHLCV"] if vehicle == FUTURES else
            ["ONE_MINUTE_OHLCV", "TWO_SIDED_OPTION_BOOK_AT_THE_DECISION_INSTANT"]
        ) + ([f"AGGREGATED_PER_SESSION_TO_{int(timeframe)}_MINUTE_BARS"]
             if timeframe and int(timeframe) > 1 else []),
        "minimum_trades": MIN_TRAIN_TRADES,
        "minimum_sessions": MIN_SCREEN_SESSIONS,
        "promotion_gate": {
            "resolved_trades": PROMOTION_MIN_TRADES,
            "sessions": PROMOTION_MIN_SESSIONS,
            "holdout": "CHRONOLOGICAL_AND_UNTOUCHED",
        },
        **periods,
    }


def _param_combos(spec: dict):
    keys = sorted(spec["params"])
    for values in itertools.product(*(spec["params"][k] for k in keys)):
        yield dict(zip(keys, values, strict=True))


def generate(*, max_candidates: int = MAX_CANDIDATES) -> dict:
    """Generate and register the whole candidate set for this cycle.

    Idempotent through the registry: a second run over the same universe
    registers nothing new and reports ``created: 0``, so re-running a cycle
    does not inflate the hypothesis count.
    """
    screenable = universe.screenable()
    if not screenable:
        return {
            "generated": 0, "created": 0, "existing": 0,
            "status": "NO_SCREENABLE_INSTRUMENT",
            "reason": (
                "no instrument on disk has enough one-minute history to carry "
                "a chronological split; generating candidates against it "
                "would produce hypotheses that can never be tested"
            ),
            "universe": universe.funnel(),
        }

    instruments = [r["instrument"] for r in screenable]
    periods_by_inst = {r["instrument"]: _periods(r) for r in screenable}
    made: list[dict] = []
    bound_reached = False

    def emit(definition: dict) -> bool:
        nonlocal bound_reached
        if len(made) >= max_candidates:
            bound_reached = True
            return False
        made.append(registry.register(definition))
        return True

    # A/B/C — one mechanism, one parameter set, one instrument, default exit.
    for mech_name, spec in sorted(mech.GRID.items()):
        if mech_name in mech.PAIRED:
            continue
        for params in _param_combos(spec):
            tag = "_".join(f"{k}{v}" for k, v in sorted(params.items()))
            for inst in instruments:
                if not emit(_definition(
                    name=f"{mech_name}_{tag}_{inst}",
                    family=spec["family"], instrument=inst, vehicle=FUTURES,
                    mechanism=mech_name, params=params,
                    exit_name=DEFAULT_EXIT, periods=periods_by_inst[inst],
                )):
                    break

    # D — relative value. Needs two legs measured at the same instant, so it is
    # generated only for pairs that both have history; a pair with one screenable
    # leg is not a pair and is left out rather than half-tested.
    for mech_name in sorted(mech.PAIRED):
        spec = mech.GRID[mech_name]
        for params in _param_combos(spec):
            tag = "_".join(f"{k}{v}" for k, v in sorted(params.items()))
            for a, b in itertools.combinations(instruments, 2):
                if not emit(_definition(
                    name=f"{mech_name}_{tag}_{a}_vs_{b}",
                    family=spec["family"], instrument=a, vehicle=FUTURES,
                    mechanism=mech_name, params=params, exit_name=DEFAULT_EXIT,
                    periods=periods_by_inst[a], pair_with=b,
                )):
                    break

    # G — profit capture, varied on one restricted mechanism per instrument
    # rather than crossed with the whole grid. Crossing four exits with every
    # entry quadruples the hypothesis count to answer a question that one entry
    # can answer.
    base_mech, base_params = "breakout", {"lookback": 30, "buffer_atr": 0.5}
    for exit_name in sorted(EXITS):
        if exit_name == DEFAULT_EXIT:
            continue
        for inst in instruments:
            if not emit(_definition(
                name=f"exit_{exit_name}_{base_mech}_{inst}",
                family=FAMILY_CAPTURE, instrument=inst, vehicle=FUTURES,
                mechanism=base_mech, params=base_params, exit_name=exit_name,
                periods=periods_by_inst[inst],
            )):
                break

    # The timeframe sweep. One parameter set per mechanism, every declared
    # timeframe, every screenable instrument, the default exit with its time
    # stop in bars. Each is a separate hypothesis and is counted as one: a
    # 15-minute breakout is not the same sentence as a one-minute breakout, and
    # letting them share a correction would hide five tests inside one.
    for mech_name, params in sorted(HTF_REPRESENTATIVE.items()):
        spec = mech.GRID.get(mech_name)
        if spec is None or mech_name in HTF_EXCLUDED:
            continue
        paired = mech_name in mech.PAIRED
        tag = "_".join(f"{k}{v}" for k, v in sorted(params.items()))
        for tf in HTF_TIMEFRAMES:
            legs = (list(itertools.combinations(instruments, 2)) if paired
                    else [(inst, None) for inst in instruments])
            for a, b in legs:
                suffix = f"_vs_{b}" if b else ""
                if not emit(_definition(
                    name=f"tf{oppbars.label(tf)}_{mech_name}_{tag}_{a}{suffix}",
                    family=spec["family"], instrument=a, vehicle=FUTURES,
                    mechanism=mech_name, params=params, exit_name=DEFAULT_EXIT,
                    periods=periods_by_inst[a], pair_with=b,
                    timeframe=tf, time_stop_bars=HTF_TIME_STOP_BARS,
                )):
                    break

    # E/F — vehicle selection and option structure. Declared with their scope,
    # and unpriceable on candle history: they screen UNMEASURED, never REJECTED.
    for inst in instruments:
        for vehicle in (CE, PE):
            if not emit(_definition(
                name=f"vehicle_{vehicle}_{base_mech}_{inst}",
                family=FAMILY_VEHICLE, instrument=inst, vehicle=vehicle,
                mechanism=base_mech, params=base_params,
                exit_name=DEFAULT_EXIT, periods=periods_by_inst[inst],
            )):
                break
        for sname, structure in sorted(OPTION_STRUCTURES.items()):
            if not emit(_definition(
                name=f"structure_{sname}_{base_mech}_{inst}",
                family=FAMILY_OPTION, instrument=inst, vehicle=CE,
                mechanism=base_mech, params=base_params,
                exit_name=DEFAULT_EXIT, periods=periods_by_inst[inst],
                structure=structure,
            )):
                break

    created = sum(1 for m in made if m.get("created"))
    by_family: dict[str, int] = {}
    for m in made:
        fam = str(m.get("mechanism_family"))
        by_family[fam] = by_family.get(fam, 0) + 1
    return {
        "generated": len(made),
        "created": created,
        "existing": len(made) - created,
        "by_family": by_family,
        "families": len(by_family),
        "instruments": instruments,
        "timeframes": [oppbars.label(t) for t in HTF_TIMEFRAMES],
        "timeframe_sweep": {
            "mechanisms": sorted(HTF_REPRESENTATIVE),
            "excluded": HTF_EXCLUDED,
            "time_stop_bars": HTF_TIME_STOP_BARS,
            "one_parameter_set_per_mechanism": True,
        },
        "bound": max_candidates,
        "bound_reached": bound_reached,
        "generator_version": VERSION,
        "note": (
            "every generated candidate counts as a hypothesis in the "
            "false-discovery correction, including the ones that lose. That is "
            "what makes the correction mean anything."
        ),
    }
