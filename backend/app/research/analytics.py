"""Performance / trade / regime / time-of-day / rule-impact analytics.

Every statistic is computed from REAL recorded trades in the research store —
nothing is synthesised. Whether those trades came from a real broker feed or the
simulated feed is reported explicitly via ``data_source`` / ``is_real_data`` so
simulated-feed numbers are never presented as real trading performance.

Rule-impact here is **observational** (conditional performance: how trades did
when a rule's condition was present vs. absent), computed from stored context.
It does not re-run the engine with rules disabled, because the Phase 1.5 engine
is frozen — so we measure the frozen engine, we do not mutate it.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone

from app.config import settings
from app.research.store import ResearchStore, store


def _is_real_feed() -> bool:
    return settings.data_provider.lower() not in ("simulated", "sim", "demo")


def _basic(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    pnls = [float(t["pnl"]) for t in trades]
    wins = [p for p in pnls if p >= 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    mean = sum(pnls) / n
    var = sum((p - mean) ** 2 for p in pnls) / n
    std = math.sqrt(var)
    # equity curve + max drawdown
    eq = 0.0
    curve = [0.0]
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        eq += p
        curve.append(round(eq, 1))
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    # streaks
    best_win = best_loss = cur = 0
    last_win: bool | None = None
    for p in pnls:
        w = p >= 0
        if w == last_win:
            cur += 1
        else:
            cur = 1
            last_win = w
        if w:
            best_win = max(best_win, cur)
        else:
            best_loss = max(best_loss, cur)
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    holds = [float(t["duration_min"]) for t in trades if t.get("duration_min") is not None]
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 1),
        "loss_rate": round(len(losses) / n * 100, 1),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(mean, 1),
        "total_net_pnl": round(sum(pnls), 1),
        "avg_win": round(avg_win, 1),
        "avg_loss": round(-avg_loss, 1),
        "avg_risk_reward": round(avg_win / avg_loss, 2) if avg_loss > 0 else None,
        "sharpe": round(mean / std * math.sqrt(len(pnls)), 2) if std > 0 else None,
        "max_drawdown": round(max_dd, 1),
        "avg_hold_minutes": round(sum(holds) / len(holds), 1) if holds else None,
        "longest_win_streak": best_win,
        "longest_loss_streak": best_loss,
        "equity_curve": curve,
    }


def _group_perf(trades: list[dict], key) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[str(key(t))].append(t)
    out = {}
    for g, rows in groups.items():
        pnls = [float(r["pnl"]) for r in rows]
        wins = sum(1 for p in pnls if p >= 0)
        out[g] = {
            "trades": len(rows),
            "wins": wins,
            "win_rate": round(wins / len(rows) * 100, 1),
            "net_pnl": round(sum(pnls), 1),
        }
    return out


def by_regime(trades: list[dict]) -> dict:
    return _group_perf(trades, lambda t: t.get("market_regime") or "UNKNOWN")


def by_hour(trades: list[dict]) -> dict:
    return _group_perf(trades, lambda t: f"{int(t.get('entry_hour') or 0):02d}:00")


def by_month(trades: list[dict]) -> dict:
    def month(t):
        ts = t.get("entry_ts")
        if not ts:
            return "UNKNOWN"
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m")
    return _group_perf(trades, month)


def by_weekday(trades: list[dict]) -> dict:
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def wd(t):
        ts = t.get("entry_ts")
        if not ts:
            return "UNKNOWN"
        return names[datetime.fromtimestamp(int(ts), tz=timezone.utc).weekday()]
    return _group_perf(trades, wd)


def rule_impact(trades: list[dict]) -> dict:
    """Observational rule impact: win-rate / profit-factor for trades where a
    rule's condition held vs. where it did not (from stored trade context)."""
    def split(pred, label_true: str, label_false: str) -> dict:
        yes = [t for t in trades if pred(t)]
        no = [t for t in trades if not pred(t)]

        def stat(rows):
            if not rows:
                return {"trades": 0, "win_rate": None, "net_pnl": None}
            pnls = [float(r["pnl"]) for r in rows]
            wins = sum(1 for p in pnls if p >= 0)
            return {"trades": len(rows), "win_rate": round(wins / len(rows) * 100, 1),
                    "net_pnl": round(sum(pnls), 1)}
        return {label_true: stat(yes), label_false: stat(no)}

    def ctx(t, k):
        c = t.get("context") or {}
        return c.get(k)

    return {
        "pullback_entry": split(
            lambda t: (ctx(t, "entry_trigger") or "").upper().find("PULLBACK") >= 0,
            "pullback", "breakout_or_other"),
        "healthy_premium": split(
            lambda t: (ctx(t, "premium_health") == "HEALTHY"),
            "healthy", "neutral_or_dangerous"),
        "trending_regime": split(
            lambda t: (t.get("market_regime") in ("TRENDING", "BREAKOUT")),
            "trend_breakout", "range_other"),
        "high_trade_score": split(
            lambda t: (float(t.get("trade_score") or 0) >= 80),
            "score_ge_80", "score_lt_80"),
    }


def trade_explanations(trades: list[dict], limit: int = 50) -> list[dict]:
    """Rule-based (not LLM) explanation of why each trade won or lost, derived
    from its stored decision context and outcome."""
    out = []
    for t in trades[-limit:]:
        ctx = t.get("context") or {}
        win = bool(t.get("win"))
        factors = []
        if (ctx.get("entry_trigger") or "").upper().find("PULLBACK") >= 0:
            factors.append("pullback entry (not chasing)")
        if ctx.get("htf_trend"):
            factors.append(f"{ctx['htf_trend']} 5-min trend")
        if ctx.get("premium_health"):
            factors.append(f"{ctx['premium_health'].lower()} premium")
        if ctx.get("opportunity"):
            factors.append(f"opportunity {ctx['opportunity']}")
        if ctx.get("risk"):
            factors.append(f"risk {ctx['risk']}")
        reason = t.get("exit_reason")
        mfe = float(t.get("mfe") or 0.0)
        pnl = float(t.get("pnl") or 0.0)
        if win:
            headline = f"Won (+{pnl:.0f}) via {reason}"
        else:
            missed = ""
            if mfe > 0:
                missed = f"; went +{mfe:.0f} in favour first (possible early/late management)"
            headline = f"Lost ({pnl:.0f}) via {reason}{missed}"
        out.append({
            "entry_ts": t.get("entry_ts"), "option": t.get("option_symbol"),
            "pnl": pnl, "exit_reason": reason, "mfe": mfe,
            "mae": float(t.get("mae") or 0.0),
            "win": win, "why": headline, "factors": factors,
        })
    return out


def distributions(instrument: str, source: str, st: ResearchStore) -> dict:
    sig = st.signal_distribution(instrument, source)
    rows = st.db.query(
        "SELECT confidence, quantum_score, trade_score FROM signal_snapshots "
        "WHERE instrument=? AND source=?",
        (instrument, source),
    )

    def bucketize(vals, edges):
        out = {f"{lo}-{hi}": 0 for lo, hi in edges}
        for v in vals:
            if v is None:
                continue
            for lo, hi in edges:
                if lo <= v < hi or (hi == 100 and v == 100):
                    out[f"{lo}-{hi}"] += 1
                    break
        return out

    edges = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
    return {
        "signal": sig,
        "confidence": bucketize([r["confidence"] for r in rows], edges),
        "quantum_score": bucketize([r["quantum_score"] for r in rows], edges),
        "trade_score": bucketize([r["trade_score"] for r in rows], edges),
    }


def setup_library(instrument: str, *, source: str = "paper_live",
                  outcome: str = "all", regime: str | None = None,
                  min_confidence: float | None = None,
                  min_trade_score: float | None = None,
                  limit: int = 500, st: ResearchStore | None = None) -> dict:
    """Searchable list of completed trades ("setups") for the Setup Library.

    Read-only view over stored trades with simple filters. Never estimates or
    fabricates — if nothing has been recorded yet it returns an empty list with
    an explicit note, so the UI shows "no setups yet" rather than fake rows.
    """
    st = st or store()
    trades = st.trades(instrument, source)

    def conf(t: dict) -> float | None:
        c = (t.get("context") or {}).get("confidence")
        return float(c) if c is not None else None

    rows = []
    for t in trades:
        if outcome == "win" and not t.get("win"):
            continue
        if outcome == "loss" and t.get("win"):
            continue
        if regime and (t.get("market_regime") or "").upper() != regime.upper():
            continue
        c = conf(t)
        if min_confidence is not None and (c is None or c < min_confidence):
            continue
        if min_trade_score is not None and (
            t.get("trade_score") is None or float(t["trade_score"]) < min_trade_score
        ):
            continue
        rows.append({
            "id": t.get("id"),
            "entry_ts": t.get("entry_ts"),
            "exit_ts": t.get("exit_ts"),
            "option_symbol": t.get("option_symbol"),
            "option_type": t.get("option_type"),
            "entry": t.get("entry"),
            "exit": t.get("exit"),
            "pnl": t.get("pnl"),
            "mfe": t.get("mfe"),
            "mae": t.get("mae"),
            "duration_min": t.get("duration_min"),
            "market_regime": t.get("market_regime"),
            "trade_score": t.get("trade_score"),
            "confidence": c,
            "exit_reason": t.get("exit_reason"),
            "win": bool(t.get("win")),
        })
    rows.sort(key=lambda r: r.get("exit_ts") or r.get("entry_ts") or 0, reverse=True)
    is_real = _is_real_feed()
    return {
        "instrument": instrument,
        "source": source,
        "is_real_data": is_real,
        "data_source": "real_broker_feed" if is_real else "simulated_feed",
        "total": len(trades),
        "matched": len(rows),
        "setups": rows[:limit],
        "note": (
            "No completed trades recorded yet — the Setup Library fills as you "
            "close paper trades." if not trades else None
        ),
    }


def report(instrument: str, *, source: str = "replay",
           st: ResearchStore | None = None) -> dict:
    st = st or store()
    trades = st.trades(instrument, source)
    is_real = _is_real_feed()
    data_source = "real_broker_feed" if is_real else "simulated_feed"
    note = (
        "Metrics computed from REAL recorded trades." if is_real else
        "These are LOGIC-VALIDATION metrics computed on SIMULATED-feed replay — "
        "they verify the engine's behaviour and the analytics pipeline, and must "
        "NOT be read as real trading performance. Real metrics require a real "
        "broker feed and collected market history."
    )
    return {
        "instrument": instrument,
        "source": source,
        "backend": st.backend,
        "is_real_data": is_real,
        "data_source": data_source,
        "note": note,
        "trades_recorded": len(trades),
        "performance": _basic(trades),
        "by_regime": by_regime(trades),
        "by_hour": by_hour(trades),
        "by_weekday": by_weekday(trades),
        "by_month": by_month(trades),
        "rule_impact": rule_impact(trades),
        "trade_explanations": trade_explanations(trades),
        "distributions": distributions(instrument, source, st),
    }
