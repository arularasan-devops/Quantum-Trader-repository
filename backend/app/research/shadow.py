"""Shadow mode — recommendation vs. actual outcome, with NO orders placed.

The frozen engine runs and every recommendation is logged with a reference price
and an evaluation horizon. Once the horizon has elapsed, the recommendation is
scored against the ACTUAL futures move:

* BUY (CE) is "correct" if the underlying rose beyond a small threshold;
* BUY (PE) is "correct" if it fell;
* WAIT / AVOID / NO_TRADE is "correct" if the move stayed within the threshold
  (i.e. standing aside genuinely avoided a directional trade).

This measures the decision logic under real conditions without risking capital.
The recommendation itself uses no lookahead; only its *scoring* looks at what
happened afterwards, which is the whole point of shadow evaluation.
"""
from __future__ import annotations

from collections import defaultdict

from app.config import settings
from app.engine import decision as eng
from app.models import Candle
from app.research.replay import _chain_at, _load
from app.research.store import ResearchStore, store


def _threshold(price: float) -> float:
    return max(0.0008 * price, 0.5)


def _actual_label(ref: float, later: float, thr: float) -> str:
    if later - ref > thr:
        return "UP"
    if later - ref < -thr:
        return "DOWN"
    return "FLAT"


def _correct(signal: str, option_type: str | None, actual: str) -> bool:
    if signal == "BUY" and option_type == "CE":
        return actual == "UP"
    if signal == "BUY" and option_type == "PE":
        return actual == "DOWN"
    if signal in ("WAIT", "AVOID", "NO_TRADE"):
        return actual == "FLAT"
    if signal == "EXIT":
        return True  # exit correctness is trade-relative; not scored here
    return False


def log_recommendation(instrument: str, ts: int, signal: str, confidence: float,
                       option_type: str | None, ref_price: float, *,
                       horizon_min: int | None = None,
                       st: ResearchStore | None = None) -> str:
    st = st or store()
    return st.insert_shadow({
        "instrument": instrument, "ts": ts, "signal": signal,
        "confidence": confidence, "option_type": option_type,
        "ref_price": ref_price,
        "horizon_min": horizon_min or settings.shadow_horizon_minutes,
    })


def resolve_due(instrument: str, now_ts: int, price_at, *,
                st: ResearchStore | None = None) -> int:
    """Resolve pending shadow rows whose horizon has elapsed. ``price_at`` maps a
    timestamp to the futures price at/after it (returns None if unavailable)."""
    st = st or store()
    resolved = 0
    for row in st.pending_shadow(instrument, now_ts):
        horizon = int(row["horizon_min"] or settings.shadow_horizon_minutes)
        eval_ts = int(row["ts"]) + horizon * 60
        if eval_ts > now_ts:
            continue
        later = price_at(eval_ts)
        if later is None:
            continue
        ref = float(row["ref_price"])
        actual = _actual_label(ref, later, _threshold(ref))
        expected = ("UP" if row["option_type"] == "CE" else
                    "DOWN" if row["option_type"] == "PE" else "FLAT")
        ok = _correct(row["signal"], row["option_type"], actual)
        st.resolve_shadow(row["id"], eval_ts, later, expected, actual, ok)
        resolved += 1
    return resolved


def run_over_stored(instrument: str, *, horizon_min: int | None = None,
                    start: int = 60, st: ResearchStore | None = None) -> dict:
    """Offline shadow pass over captured data: log the engine's recommendation
    at each bar, then resolve everything against the actual later prices."""
    st = st or store()
    horizon = horizon_min or settings.shadow_horizon_minutes
    fut, opt_by_ts = _load(instrument, st)
    if len(fut) <= start + 10:
        return {"ok": False, "reason": "insufficient_data", "rows": len(fut)}

    candles = [Candle(time=int(r["ts"]), open=r["open"], high=r["high"],
                      low=r["low"], close=r["close"], volume=r["volume"] or 0.0)
               for r in fut]
    price_by_ts = {int(r["ts"]): float(r["close"]) for r in fut}
    sorted_ts = sorted(price_by_ts)

    def price_at(ts: int):
        # first stored price at/after ts (handles missing exact bars)
        for t in sorted_ts:
            if t >= ts:
                return price_by_ts[t]
        return None

    logged = 0
    for i in range(start, len(candles)):
        window = candles[: i + 1]
        ts = int(candles[i].time)
        chain, _ = _chain_at(opt_by_ts.get(ts, []), instrument)
        pc = candles[i].close - candles[i - 1].close if i > 0 else 0.0
        snap = eng.compute_indicators(window, chain, price_change=pc)
        status = eng.classify_market(snap, False)
        decision, _ = eng.decide(window, chain, snap, 0.0, False, status, False,
                                 None, spot=candles[i].close)
        otype = decision.option_type.value if decision.option_type else None
        log_recommendation(instrument, ts, decision.signal.value, decision.confidence,
                           otype, candles[i].close, horizon_min=horizon, st=st)
        logged += 1

    now_ts = int(candles[-1].time) + horizon * 60 + 1
    resolved = resolve_due(instrument, now_ts, price_at, st=st)
    return {"ok": True, "instrument": instrument, "logged": logged,
            "resolved": resolved, "summary": summary(instrument, st=st)}


def summary(instrument: str, *, st: ResearchStore | None = None) -> dict:
    st = st or store()
    rows = [r for r in st.shadow_rows(instrument) if r.get("correct") is not None]
    if not rows:
        return {"resolved": 0, "accuracy": None, "by_signal": {}}
    agg: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        agg[str(r["signal"])].append(bool(r["correct"]))
    by_signal = {
        s: {"n": len(v), "accuracy": round(sum(v) / len(v) * 100, 1)}
        for s, v in agg.items()
    }
    correct = sum(1 for r in rows if r["correct"])
    return {
        "resolved": len(rows),
        "accuracy": round(correct / len(rows) * 100, 1),
        "by_signal": by_signal,
    }
