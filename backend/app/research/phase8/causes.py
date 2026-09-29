"""Phase 8 Part 3/11 — one primary cause per resolved trade. RESEARCH ONLY.

"The engine lost money" is not actionable. "39 of 61 losses were the direction,
not the exit" is, because the two point at opposite fixes: a direction problem
means take fewer trades, an exit problem means keep the same trades and change the
handling. Averaging the two produces a change that helps neither.

Two design decisions are stated rather than buried, because they *are* the result:

1. **Every trade gets exactly one primary cause, assigned by the first matching
   test.** The order below is therefore the definition of "primary", and it is
   ordered by what would have had to be different for the outcome to change:
   the data the decision saw, then the side it chose, then the contract it chose,
   then whether the trade was even reachable, then how it was entered, then how it
   was managed. A cheaper fix later in the list cannot claim a trade that a more
   fundamental problem earlier in the list already explains.
2. **Every matching flag is also recorded**, so the ordering cannot hide a cause.
   The evidence table reports primary counts *and* flag counts, and they differ.

The same flags are computed for winners. A cause that appears just as often in
wins as in losses explains nothing, and that comparison is the only protection
against a taxonomy that simply describes the market.

Reuses Phase 7 for all arithmetic: paths for MFE/MAE/chase/expected move,
policies for the fill. Nothing here re-derives an R.
"""
from __future__ import annotations

from collections import Counter

from app.models import Candle
from app.research.phase7 import paths
from app.research.phase7.dataset import ChainSeries

from .findings import label, note

CAUSES = (
    "DATA_QUALITY",
    "WRONG_DIRECTION",
    "BAD_STRIKE",
    "INSUFFICIENT_ROOM",
    "LOW_LIQUIDITY",
    "BAD_SPREAD",
    "ENTRY_CHASE",
    "STOP_TOO_TIGHT",
    "EXIT_TOO_LATE",
    "REGIME_FAILURE",
    "UNKNOWN",
)

# Thresholds are stated constants, not tuned parameters. Each is a plain reading of
# the quantity it applies to, and every one of them is printed with the report.
DEAD_MFE_R = 0.3           # never went anywhere
FULL_R = 1.0               # a whole risk unit was on the table
WEAK_MFE_R = 0.5           # barely moved
SPREAD_HEAVY_PCT = 25.0    # book takes a quarter of the risk unit
CHASE_IMPROVEMENT_PCT = 2.0
FAR_OTM_DELTA = 0.25
FAVOURABLE_UNDERLYING_ATR = 0.5
EXHAUSTION_REGIMES = ("EXTENDED", "EXHAUSTED", "STALLED")


def underlying_excursion(candles: list[Candle], bar: int, side: str,
                         horizon: int = paths.HORIZON_BARS) -> dict:
    """Favourable/adverse underlying move after the signal, in price terms.

    Needed to tell "the market did not move" (a signal problem) apart from "the
    market moved and the option did not follow" (a contract problem).
    """
    window = candles[bar + 1 : bar + 1 + horizon]
    if not window:
        return {"bars": 0, "favourable": None, "adverse": None}
    ref = candles[bar].close
    highs = max(c.high for c in window)
    lows = min(c.low for c in window)
    fav = (highs - ref) if side == "CE" else (ref - lows)
    adv = (ref - lows) if side == "CE" else (highs - ref)
    return {"bars": len(window), "favourable": round(fav, 2),
            "adverse": round(adv, 2), "ref": round(ref, 2)}


def mirror_outcome(ev, series: ChainSeries, horizon: int = paths.HORIZON_BARS) -> dict:
    """What the opposite leg at the same strike did over the same window.

    This is the direct test of direction selection: if the trade went nowhere while
    its mirror reached target, the loss is the side that was chosen, not the way the
    trade was handled. Built with the same ``paths.walk`` used for the real leg, and
    with the risk fraction and R:R the engine itself chose, so the comparison is
    like-for-like.
    """
    snap = series.at(ev.ts)
    if not snap or ev.path is None:
        return {"available": False, "reason": "no snapshot at signal time"}
    want = "PE" if ev.side == "CE" else "CE"
    mirror = None
    for sym, leg in snap.items():
        try:
            same_strike = abs(float(leg.get("strike") or 0.0) - ev.strike) < 1e-6
        except (TypeError, ValueError):
            continue
        if same_strike and str(leg.get("option_type")) == want:
            mirror = (sym, leg)
            break
    if mirror is None:
        return {"available": False, "reason": f"no {want} leg at strike {ev.strike}"}
    sym, leg = mirror
    entry = float(leg.get("premium") or 0.0)
    if entry <= 0:
        return {"available": False, "reason": "mirror leg had no premium"}
    risk_frac = max(0.01, ev.entry - ev.stop) / ev.entry
    rr = (ev.target1 - ev.entry) / max(0.01, ev.entry - ev.stop)
    stop = entry * (1.0 - risk_frac)
    target = entry + (entry - stop) * rr
    path = paths.walk(series, sym, ev.ts, entry, stop, target, horizon)
    if len(path.quotes) < 3:
        return {"available": False, "reason": "mirror leg had no forward quotes"}
    return {"available": True, "symbol": sym, "side": want,
            "outcome": path.outcome, "mfe_r": round(path.mfe_r, 3),
            "mae_r": round(path.mae_r, 3)}


def flags(ev, fill, row: dict, spread_row: dict, mirror: dict,
          moved: dict, entry_leg: dict | None) -> dict[str, bool]:
    """Every cause that applies to this trade, independent of ranking."""
    mfe_r = float(row.get("mfe_r") or 0.0)
    realised = float(row.get("realised_r") or 0.0)
    chase_class = str(ev.chase.get("class") or "UNKNOWN")
    improvement = float(ev.chase.get("improvement_available_pct") or 0.0)
    oi = None if not entry_leg else entry_leg.get("oi")
    volume = None if not entry_leg else entry_leg.get("volume")

    # Room: the premium distance to target1, converted to the underlying move it
    # needs through the leg's own quoted delta, against the move the instrument's
    # own ATR says is available in the horizon. No pricing model, no assumption.
    required_move = None
    available_move = paths.expected_move(ev.atr) if ev.atr else None
    if ev.leg_delta > 0:
        required_move = (ev.target1 - ev.entry) / ev.leg_delta

    return {
        "DATA_QUALITY": bool(
            ev.data_flag in ("STALE", "NO_DATA")
            or (ev.chain_age_sec is not None and ev.chain_age_sec > 90)
            or (ev.path is not None and len(ev.path.quotes) < 5)),
        "WRONG_DIRECTION": bool(
            mfe_r < DEAD_MFE_R and mirror.get("available")
            and mirror.get("outcome") == "TARGET_FIRST"),
        "BAD_STRIKE": bool(
            mfe_r < DEAD_MFE_R and moved.get("favourable") is not None
            and ev.atr > 0
            and moved["favourable"] >= FAVOURABLE_UNDERLYING_ATR * ev.atr),
        "INSUFFICIENT_ROOM": bool(
            required_move is not None and available_move
            and required_move > available_move),
        "LOW_LIQUIDITY": bool(not oi or not volume),
        "BAD_SPREAD": bool(
            spread_row.get("measured")
            and float(spread_row.get("spread_share_of_risk_pct") or 0.0)
            >= SPREAD_HEAVY_PCT),
        "ENTRY_CHASE": bool(
            chase_class in ("CHASED_ENTRY", "SEVERELY_CHASED")
            or improvement >= CHASE_IMPROVEMENT_PCT),
        "STOP_TOO_TIGHT": bool(row.get("target_after_stop")),
        "EXIT_TOO_LATE": bool(mfe_r >= FULL_R and realised <= 0.0),
        "REGIME_FAILURE": bool(
            str(ev.regime).upper() in EXHAUSTION_REGIMES and mfe_r < WEAK_MFE_R),
    }


def primary(flagged: dict[str, bool]) -> str:
    for cause in CAUSES:
        if cause == "UNKNOWN":
            break
        if flagged.get(cause):
            return cause
    return "UNKNOWN"


def classify(ev, fill, row: dict, spread_row: dict, series: ChainSeries,
             candles: list[Candle], horizon: int = paths.HORIZON_BARS) -> dict:
    mirror = mirror_outcome(ev, series, horizon)
    moved = underlying_excursion(candles, ev.bar, ev.side, horizon)
    entry_leg = (series.at(fill.entry_ts) or {}).get(ev.symbol)
    flagged = flags(ev, fill, row, spread_row, mirror,
                    moved, entry_leg if isinstance(entry_leg, dict) else None)
    return {
        "symbol": ev.symbol,
        "instrument": ev.instrument,
        "side": ev.side,
        "ts_ist": row.get("ts_ist"),
        "expiry_class": getattr(ev, "expiry_class", "EXPIRY_UNKNOWN"),
        "regime": ev.regime,
        "confidence": ev.confidence,
        "outcome": "WIN" if float(row.get("realised_r") or 0.0) > 0 else "LOSS",
        "realised_r": row.get("realised_r"),
        "mfe_r": row.get("mfe_r"),
        "mae_r": row.get("mae_r"),
        "net_r": spread_row.get("net_r"),
        "primary_cause": primary(flagged),
        "flags": [k for k, v in flagged.items() if v],
        "mirror": mirror,
        "underlying_move": moved,
    }


def evidence_table(rows: list[dict], sessions: int) -> dict:
    """Part 11 — losses and wins by cause, with the flag counts beside them."""
    losses = [r for r in rows if r["outcome"] == "LOSS"]
    wins = [r for r in rows if r["outcome"] == "WIN"]
    loss_primary = Counter(r["primary_cause"] for r in losses)
    win_primary = Counter(r["primary_cause"] for r in wins)
    loss_flags: Counter = Counter()
    win_flags: Counter = Counter()
    for r in losses:
        loss_flags.update(r["flags"])
    for r in wins:
        win_flags.update(r["flags"])

    table = []
    for cause in CAUSES:
        lp, wp = loss_primary.get(cause, 0), win_primary.get(cause, 0)
        lf, wf = loss_flags.get(cause, 0), win_flags.get(cause, 0)
        table.append({
            "cause": cause,
            "loss_primary": lp,
            "win_primary": wp,
            "loss_primary_pct": round(100.0 * lp / len(losses), 1) if losses else 0.0,
            "loss_flagged": lf,
            "win_flagged": wf,
            # A cause that flags wins as often as losses is describing the market,
            # not explaining the loss. Reported per cause so the reader can see it.
            "flag_loss_rate_pct": round(100.0 * lf / (lf + wf), 1) if lf + wf else None,
            "label": label(lp + wp, sessions),
        })
    table.sort(key=lambda r: (-r["loss_primary"], r["cause"]))
    ranked = [r for r in table if r["loss_primary"]]
    return {
        "resolved": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "ordering_is_the_definition": list(CAUSES),
        "thresholds": {
            "dead_mfe_r": DEAD_MFE_R, "full_r": FULL_R, "weak_mfe_r": WEAK_MFE_R,
            "spread_heavy_pct_of_risk": SPREAD_HEAVY_PCT,
            "chase_improvement_pct": CHASE_IMPROVEMENT_PCT,
            "far_otm_delta": FAR_OTM_DELTA,
            "favourable_underlying_atr": FAVOURABLE_UNDERLYING_ATR,
            "exhaustion_regimes": list(EXHAUSTION_REGIMES),
        },
        "table": table,
        "top_loss_cause": ranked[0]["cause"] if ranked else None,
        "label": label(len(losses), sessions),
        "label_note": note(len(losses), sessions),
        "reading": "signal-side causes (WRONG_DIRECTION, BAD_STRIKE, "
                   "REGIME_FAILURE, INSUFFICIENT_ROOM) argue for taking fewer "
                   "trades; execution-side causes (ENTRY_CHASE, BAD_SPREAD, "
                   "LOW_LIQUIDITY, STOP_TOO_TIGHT, EXIT_TOO_LATE) argue for "
                   "keeping the same trades and changing the handling. The two "
                   "must not be averaged into one conclusion.",
    }


def by_expiry(rows: list[dict], sessions: int) -> dict:
    """Part K's second question: does the cause of a loss change on expiry day?

    Reported as the ranked cause list per class plus the top cause of each, because
    a rank order is readable at cell sizes where a percentage difference is not.
    """
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get("expiry_class")), []).append(r)
    out = {}
    for name, sub in sorted(groups.items()):
        block = evidence_table(sub, sessions)
        out[name] = {
            "resolved": block["resolved"],
            "wins": block["wins"],
            "losses": block["losses"],
            "top_loss_cause": block["top_loss_cause"],
            "ranked": [{"cause": r["cause"], "loss_primary": r["loss_primary"],
                        "win_primary": r["win_primary"]}
                       for r in block["table"] if r["loss_primary"] or r["win_primary"]],
            "label": block["label"],
        }
    tops = {k: v["top_loss_cause"] for k, v in out.items()}
    differs = len({t for t in tops.values() if t}) > 1
    return {
        "by_class": out,
        "top_cause_by_class": tops,
        "cause_mix_differs_between_classes": differs,
        "reading": ("the leading cause is not the same in every expiry class on this "
                    "sample — worth testing, and far too thin to act on"
                    if differs else
                    "the leading cause is the same in every expiry class on this "
                    "sample, which is weak evidence that expiry does not change the "
                    "failure mode"),
    }
