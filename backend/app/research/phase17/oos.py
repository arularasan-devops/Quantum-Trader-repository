"""Out-of-sample discipline for A+ — §29, §30, §31, §32.

The same machinery Phase 15B used, pointed at costed option outcomes instead of
underlying replay rows: chronological train/development/validation/holdout, no
shuffling, folds cut on session boundaries, thresholds frozen before the holdout
is read. Reusing the existing modules rather than writing a second implementation
is the point — two promotion gates with slightly different rules is how a rule
gets promoted by whichever one is kinder.

What is different here, and it is the whole reason this phase exists: ``r`` is
NET. A row whose cost was not measured is excluded, not estimated, so the sample
is smaller and means something. The honest expected verdict for a long while is
``REQUIRES_MORE_DATA`` — 100 development plus 100 holdout outcomes at a handful of
A+ candidates a day is weeks of sessions, and saying so is more useful than a
promotion computed on 48 rows.
"""
from __future__ import annotations

from app.research.phase15 import folds as folds_mod
from app.research.phase15 import promotion
from app.research.phase17 import aplus, capture as cap, schema

# §29 splits, chronological by session. Development fits, validation confirms,
# holdout is read once.
DEV_SHARE = 0.5
VAL_SHARE = 0.2
# Remaining 0.3 is holdout.

REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
IN_SAMPLE_ONLY = "IN_SAMPLE_ONLY"
FAILS = "FAILS"


def to_trades(rows: list[dict], *, require_net: bool = True) -> list[dict]:
    """Map paper/leg rows onto the shape the fold and promotion modules expect.

    ``exit_reason=TARGET`` is how those modules recognise a T1, so the mapping is
    explicit here rather than hidden in a rename: T1/T2/T3 all count as a target
    having been reached, everything else does not.
    """
    out: list[dict] = []
    for row in rows:
        if require_net and row.get("cost_status") != schema.COST_MEASURED:
            continue
        r = row.get("net_r") if require_net else (
            row.get("net_r") if row.get("net_r") is not None else row.get("gross_r")
        )
        if not isinstance(r, (int, float)):
            continue
        session = row.get("session")
        if not session:
            ts = row.get("signal_ts") or row.get("entry_ts")
            if not isinstance(ts, (int, float)):
                continue
            session = cap.ist_parts(float(ts))[0]
        outcome = row.get("outcome")
        out.append({
            "r": float(r),
            "exit_reason": "TARGET" if outcome in (
                schema.T1, schema.T2, schema.T3
            ) else str(outcome or "UNKNOWN"),
            "session": str(session),
            "entry_ts": row.get("entry_ts") or row.get("signal_ts"),
            "instrument": row.get("instrument"),
            "a_plus_label": row.get("a_plus_label"),
            "a_plus_score": row.get("a_plus_score"),
            "t1_rank": row.get("t1_rank"),
            "vehicle_class": row.get("vehicle_class"),
            "entry_quality": row.get("entry_quality"),
            "data_quality": row.get("data_quality"),
        })
    out.sort(key=lambda t: (t["session"], str(t.get("entry_ts") or "")))
    return out


def split(trades: list[dict], *, dev_share: float = DEV_SHARE,
          val_share: float = VAL_SHARE) -> dict:
    """Chronological development / validation / holdout by SESSION.

    Split on sessions rather than rows because candidates inside one session are
    the most correlated rows in the book; cutting through a session leaks its
    outcome across the boundary and makes every period look more alike than it is.
    """
    sessions = sorted({t["session"] for t in trades})
    n = len(sessions)
    if n < 3:
        return {
            "development": [], "validation": [], "holdout": [],
            "sessions": n,
            "status": REQUIRES_MORE_DATA,
            "note": f"{n} session(s) recorded; a chronological split needs 3+",
        }
    d_end = max(1, int(round(n * dev_share)))
    v_end = max(d_end + 1, int(round(n * (dev_share + val_share))))
    v_end = min(v_end, n - 1)
    dev_s = set(sessions[:d_end])
    val_s = set(sessions[d_end:v_end])
    hold_s = set(sessions[v_end:])
    return {
        "development": [t for t in trades if t["session"] in dev_s],
        "validation": [t for t in trades if t["session"] in val_s],
        "holdout": [t for t in trades if t["session"] in hold_s],
        "sessions": n,
        "session_bounds": {
            "development": sorted(dev_s),
            "validation": sorted(val_s),
            "holdout": sorted(hold_s),
        },
        "status": "SPLIT",
    }


def summarise(trades: list[dict]) -> dict:
    """Expectancy, PF, T1 rate and drawdown over a period."""
    if not trades:
        return {"trades": 0, "expectancy_r": None, "profit_factor": None,
                "t1_pct": None, "net_r": 0.0, "max_drawdown_r": 0.0}
    rs = [float(t["r"]) for t in trades]
    gain = sum(r for r in rs if r > 0)
    loss = -sum(r for r in rs if r < 0)
    t1 = sum(1 for t in trades if t.get("exit_reason") == "TARGET")
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return {
        "trades": len(rs),
        "expectancy_r": round(sum(rs) / len(rs), 4),
        "profit_factor": round(gain / loss, 3) if loss > 0 else None,
        "t1_pct": round(100.0 * t1 / len(rs), 2),
        "net_r": round(sum(rs), 3),
        "max_drawdown_r": round(worst, 3),
    }


def evaluate(paper_rows: list[dict], *, baseline_rows: list[dict] | None = None,
             folds: int = folds_mod.DEFAULT_FOLDS) -> dict:
    """§30/§31. Grade the A+ selector against the promotion gate.

    ``baseline_rows`` is the unchanged book over the same period — every
    recorded candidate, not only the A+ ones. Beating it is a separate
    requirement from being positive, and without it a selector that simply trades
    less can look like an improvement.
    """
    trades = to_trades(paper_rows)
    base = to_trades(baseline_rows or [], require_net=True)
    parts = split(trades)
    dev = summarise(parts["development"])
    val = summarise(parts["validation"])
    hold = summarise(parts["holdout"])
    base_parts = split(base) if base else None
    base_hold = summarise(base_parts["holdout"]) if base_parts else summarise([])

    wf = folds_mod.walk_forward(trades, lambda _t: True, folds=folds)
    excluded = len(paper_rows) - len(trades)

    verdict = promotion.evaluate(
        name="A_PLUS_PAPER",
        dev=dev,
        holdout=hold,
        baseline_holdout=base_hold,
        walk_forward=wf,
        attribution_={"missed_winners": 0, "selected": len(trades),
                      "selected_t1_pct": hold.get("t1_pct") or 0.0},
        integrity={"data_errors": 0},
        costed=True,
    )
    status = _status(dev, val, hold, verdict)
    return {
        "status": status,
        "verdict": verdict,
        "development": dev,
        "validation": val,
        "holdout": hold,
        "baseline_holdout": base_hold,
        "walk_forward": wf,
        "split": {k: v for k, v in parts.items()
                  if k in ("sessions", "session_bounds", "status", "note")},
        "rows_considered": len(paper_rows),
        "rows_excluded_uncosted": excluded,
        "basis": "NET_OF_MEASURED_COSTS",
        "note": (
            "Rows without a measured book are excluded rather than estimated, so "
            "this sample is smaller than the recorded one by "
            f"{excluded} row(s)."
        ),
    }


def _status(dev: dict, val: dict, hold: dict, verdict: dict) -> str:
    """§32 A+ lifecycle status from the evidence actually present."""
    dev_n = int(dev.get("trades") or 0)
    hold_n = int(hold.get("trades") or 0)
    if dev_n == 0 and hold_n == 0:
        return aplus.NOT_READY
    if verdict.get("verdict") == promotion.PRODUCTION_CANDIDATE:
        return aplus.PRODUCTION_CANDIDATE
    if (
        dev_n >= promotion.MIN_DEV_TRADES
        and hold_n >= promotion.MIN_HOLDOUT_TRADES
        and isinstance(hold.get("expectancy_r"), (int, float))
        and float(hold["expectancy_r"]) > 0
    ):
        return aplus.VALIDATED
    if hold_n > 0 or dev_n > 0:
        return aplus.PAPER
    return aplus.RESEARCH
