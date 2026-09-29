"""One verdict, on conditions written before the numbers were read.

    SURVIVES              every condition passed. Paper only, still: surviving
                          a study is permission to keep paper trading it, not
                          permission to trade money
    FAILS                 at least one condition failed on evidence that was
                          adequate to fail it
    REQUIRES_MORE_DATA    nothing failed, but at least one condition could not
                          be answered by the evidence available

The ordering matters and is deliberate: a failure on adequate evidence outranks
a gap in the evidence, so a setup cannot be rescued from a clear negative by
pointing at something else that is merely unmeasured.
"""
from __future__ import annotations

from app.research.phase15 import folds as folds_mod
from app.research.phase22 import definition as defn, study as study_mod

SURVIVES = "SURVIVES"
FAILS = "FAILS"
THIN = "REQUIRES_MORE_DATA"

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

# Conditions, fixed here so they cannot be softened after seeing a result.
MIN_HOLDOUT_TRADES = study_mod.MIN_SAMPLE
MIN_PROFIT_FACTOR = 1.0
MIN_MEASURED_VEHICLE_LEGS = 100
# In units of the plan's own risk, on the holdout. A book that gives back 20R
# is not tradeable at any position size a research account would carry.
MAX_DRAWDOWN_R = -20.0


def _check(name: str, state: str, detail: str, **extra) -> dict:
    return {"condition": name, "state": state, "detail": detail, **extra}


def _holdout_expectancy(result: dict) -> dict:
    hold = result["periods"]["holdout"][defn.LABEL]
    n = hold.get("trades") or 0
    exp = hold.get("expectancy_r")
    if n < MIN_HOLDOUT_TRADES or not isinstance(exp, (int, float)):
        return _check("CHRONOLOGICAL_HOLDOUT_NET_EXPECTANCY", UNKNOWN,
                      f"{n} holdout candidates, under the {MIN_HOLDOUT_TRADES} "
                      "needed to read an expectancy", trades=n, expectancy_r=exp)
    state = PASS if exp > 0 else FAIL
    return _check("CHRONOLOGICAL_HOLDOUT_NET_EXPECTANCY", state,
                  f"holdout expectancy {exp}R over {n} candidates",
                  trades=n, expectancy_r=exp)


def _profit_factor(result: dict) -> dict:
    hold = result["periods"]["holdout"][defn.LABEL]
    pf = hold.get("profit_factor")
    if not isinstance(pf, (int, float)) or not hold.get("measurable"):
        return _check("PROFIT_FACTOR_ABOVE_1", UNKNOWN,
                      "holdout has no readable profit factor", profit_factor=pf)
    state = PASS if pf > MIN_PROFIT_FACTOR else FAIL
    return _check("PROFIT_FACTOR_ABOVE_1", state, f"holdout PF {pf}",
                  profit_factor=pf)


def _beats_baseline(result: dict) -> dict:
    lift = result["periods"]["holdout"]["lift"]
    value = lift.get("expectancy_lift_r")
    if not lift.get("both_measurable") or not isinstance(value, (int, float)):
        return _check("BEATS_UNCHANGED_BASELINE", UNKNOWN,
                      "one side of the holdout comparison is too thin to read",
                      lift_r=value)
    state = PASS if value > 0 else FAIL
    return _check("BEATS_UNCHANGED_BASELINE", state,
                  f"PULLBACK minus NON_PULLBACK on the holdout: {value}R",
                  lift_r=value)


def _walk_forward(result: dict) -> dict:
    wf = result["walk_forward"]
    label = wf.get("verdict")
    if label in (None, folds_mod.THIN):
        return _check("STABLE_ACROSS_WALK_FORWARD", UNKNOWN,
                      "the pool cannot be cut into readable folds",
                      walk_forward=label)
    state = PASS if label == folds_mod.STABLE else FAIL
    return _check("STABLE_ACROSS_WALK_FORWARD", state, f"folds say {label}",
                  walk_forward=label)


def _sample(result: dict) -> dict:
    total = sum(result["periods"][p][defn.LABEL].get("trades") or 0
                for p in ("development", "validation", "holdout"))
    state = PASS if total >= MIN_HOLDOUT_TRADES else UNKNOWN
    return _check("ADEQUATE_SAMPLE", state,
                  f"{total} candidates carry the frozen label", candidates=total)


def _real_book(vehicle: dict | None) -> dict:
    if not vehicle:
        return _check("REAL_BID_ASK", UNKNOWN,
                      "no vehicle capture was supplied to this run")
    measured = sum(v.get("measured_legs") or 0 for v in vehicle["by_vehicle"])
    if measured < MIN_MEASURED_VEHICLE_LEGS:
        return _check("REAL_BID_ASK", UNKNOWN,
                      f"{measured} legs priced on a recorded book, under the "
                      f"{MIN_MEASURED_VEHICLE_LEGS} needed", measured_legs=measured)
    exp = vehicle["selected"].get("net_expectancy_r")
    if not isinstance(exp, (int, float)):
        return _check("REAL_BID_ASK", UNKNOWN,
                      "no vehicle was selected on a measured book",
                      measured_legs=measured)
    state = PASS if exp > 0 else FAIL
    joined = (vehicle.get("setup_joined") or {}).get("opportunities") or 0
    return _check("REAL_BID_ASK", state,
                  f"selected vehicles net {exp}R each on ask-in/bid-out prices "
                  f"across the whole capture; {joined} of those captures joined "
                  "a candidate the frozen definition labels PULLBACK, so this "
                  "condition reports that real books exist and not that the "
                  "setup itself is profitable",
                  measured_legs=measured, net_expectancy_r=exp,
                  joined_to_setup=joined)


def _drawdown(result: dict) -> dict:
    hold = result["periods"]["holdout"][defn.LABEL]
    dd = hold.get("max_drawdown_r")
    if not isinstance(dd, (int, float)) or not hold.get("measurable"):
        return _check("ACCEPTABLE_DRAWDOWN", UNKNOWN,
                      "holdout too thin for a drawdown to mean anything",
                      max_drawdown_r=dd)
    state = PASS if dd >= MAX_DRAWDOWN_R else FAIL
    return _check("ACCEPTABLE_DRAWDOWN", state, f"holdout drawdown {dd}R",
                  max_drawdown_r=dd)


def _contamination(result: dict, vehicle: dict | None) -> dict:
    """Leakage checks that can be answered mechanically, not by assertion."""
    notes: list[str] = []
    spans = result["pool"].get("spans") or {}
    dev = (spans.get("development") or {}).get("last")
    hold = (spans.get("holdout") or {}).get("first")
    if dev and hold and str(dev) >= str(hold):
        notes.append("development and holdout share a session")
    if result.get("definition_fingerprint") != defn.fingerprint():
        notes.append("the report was produced by a different definition")
    if vehicle:
        for row in vehicle["by_vehicle"]:
            if row["vehicle"] in ("CALL", "PUT") and row["pricing"] != \
                    "ASK_IN_BID_OUT_RECORDED_BOOK":
                notes.append(f"{row['vehicle']} priced off a real book")
    if notes:
        return _check("NO_LEAKAGE_OR_CONTAMINATION", FAIL, "; ".join(notes))
    return _check("NO_LEAKAGE_OR_CONTAMINATION", PASS,
                  "chronological split intact, definition fingerprint matches, "
                  "option legs priced ask-in/bid-out only")


def assess(result: dict, vehicle: dict | None = None) -> dict:
    checks = [
        _holdout_expectancy(result),
        _profit_factor(result),
        _beats_baseline(result),
        _walk_forward(result),
        _sample(result),
        _real_book(vehicle),
        _drawdown(result),
        _contamination(result, vehicle),
    ]
    states = {c["state"] for c in checks}
    if FAIL in states:
        final = FAILS
    elif UNKNOWN in states:
        final = THIN
    else:
        final = SURVIVES
    return {
        "verdict": final,
        "definition": defn.VERSION,
        "definition_fingerprint": defn.fingerprint(),
        "checks": checks,
        "passed": sum(1 for c in checks if c["state"] == PASS),
        "failed": sum(1 for c in checks if c["state"] == FAIL),
        "unknown": sum(1 for c in checks if c["state"] == UNKNOWN),
        "execution": "PAPER_ONLY",
        "promotion": ("manual human review only; nothing here enables an order "
                      "path or changes a production gate"),
    }
