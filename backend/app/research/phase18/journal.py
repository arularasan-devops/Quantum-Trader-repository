"""The CAS journal and the reconciliation count — §22, §28.

Every CAS row carries ``strategy = CAS``. Normal rows stay ``INTRADAY``, and the
two are never merged into one figure: a research book that buys far-OTM options
into a closing auction has nothing in common with an intraday trend leg, and an
average across both would describe neither.

Reconciliation exists so nothing can disappear quietly. Observations, candidates,
CE and PE captures, exact matches, missing books, signals, entries, exits,
resolved and unresolved are all counted from the same rows, and the report prints
the arithmetic rather than a single summary number. The previous study lost 98.6%
of its legs to timestamp drift and only found out afterwards; the count is how
that gets noticed on day one instead.
"""
from __future__ import annotations

import csv
import io

from app.research.phase18 import quality, schema

# The journal's column order. Written once, here, so the CSV, the API and the
# reports cannot drift apart.
COLUMNS: tuple[str, ...] = (
    "strategy", "global_signal_id", "market_signal_id", "episode_id",
    "timestamp", "session", "instrument", "exchange",
    "expiry", "days_to_expiry", "expiry_class",
    "direction", "option_type", "strike", "rung", "strike_distance_steps",
    "underlying", "premium", "bid", "ask", "spread", "spread_pct",
    "iv", "delta", "gamma", "theta", "oi", "volume",
    "cas_state", "sub_window", "entry_timing", "strategy_variant",
    "entry_price", "exit_price", "stop", "t1", "t2", "t3",
    "gross_rupees", "spread_cost_points", "slippage_points",
    "brokerage_rupees", "tax_rupees", "net_rupees", "net_r",
    "theoretical_rupees", "bid_executable_rupees", "vanished_rupees",
    "mfe_points", "mae_points", "mfe_capture_pct", "giveback_points",
    "hold_seconds", "seconds_to_t1", "seconds_to_peak", "seconds_to_sl",
    "overnight", "next_day_open", "gap_direction", "gap_pct",
    "outcome", "status", "executability", "data_quality", "paper_only",
)


def _g(d: dict, *path: str) -> object:
    cur: object = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def row(
    leg_row: dict,
    *,
    global_signal_id: str | None = None,
    market_signal_id: str | None = None,
    overnight_row: dict | None = None,
) -> dict:
    """One journal row from one settled paper leg."""
    ex = leg_row.get("execution") if isinstance(leg_row.get("execution"), dict) else {}
    entry_q = leg_row.get("entry_quote") if isinstance(
        leg_row.get("entry_quote"), dict) else {}
    on = overnight_row or {}
    return {
        "strategy": schema.STRATEGY,
        "global_signal_id": global_signal_id,
        "market_signal_id": market_signal_id,
        "episode_id": leg_row.get("episode_id"),
        "timestamp": leg_row.get("signal_ts"),
        "session": leg_row.get("session"),
        "instrument": leg_row.get("instrument"),
        "exchange": leg_row.get("exchange"),
        "expiry": leg_row.get("expiry"),
        "days_to_expiry": leg_row.get("days_to_expiry"),
        "expiry_class": leg_row.get("expiry_class"),
        "direction": leg_row.get("direction"),
        "option_type": leg_row.get("option_type"),
        "strike": leg_row.get("strike"),
        "rung": leg_row.get("rung"),
        "strike_distance_steps": schema.RUNG_STEPS.get(
            str(leg_row.get("rung") or ""), None,
        ),
        "underlying": leg_row.get("underlying"),
        "premium": entry_q.get("premium"),
        "bid": entry_q.get("bid"),
        "ask": entry_q.get("ask"),
        "spread": entry_q.get("spread"),
        "spread_pct": entry_q.get("spread_pct"),
        "iv": entry_q.get("iv"),
        "delta": entry_q.get("delta"),
        "gamma": entry_q.get("gamma"),
        "theta": entry_q.get("theta"),
        "oi": entry_q.get("oi"),
        "volume": entry_q.get("volume"),
        "cas_state": leg_row.get("cas_state"),
        "sub_window": leg_row.get("sub_window"),
        "entry_timing": leg_row.get("entry_clock"),
        "strategy_variant": leg_row.get("strategy_variant"),
        "entry_price": leg_row.get("entry_price"),
        "exit_price": ex.get("exit_price"),
        "stop": leg_row.get("stop"),
        "t1": leg_row.get("t1"),
        "t2": leg_row.get("t2"),
        "t3": leg_row.get("t3"),
        "gross_rupees": leg_row.get("gross_rupees"),
        "spread_cost_points": ex.get("spread_cost_points"),
        "slippage_points": ex.get("slippage_points"),
        "brokerage_rupees": ex.get("brokerage_rupees"),
        "tax_rupees": ex.get("tax_rupees"),
        "net_rupees": leg_row.get("net_rupees"),
        "net_r": leg_row.get("net_r"),
        "theoretical_rupees": leg_row.get("theoretical_rupees"),
        "bid_executable_rupees": leg_row.get("bid_executable_rupees"),
        "vanished_rupees": leg_row.get("vanished_rupees"),
        "mfe_points": leg_row.get("mfe_points"),
        "mae_points": leg_row.get("mae_points"),
        "mfe_capture_pct": leg_row.get("mfe_capture_pct"),
        "giveback_points": leg_row.get("giveback_points"),
        "hold_seconds": leg_row.get("hold_seconds"),
        "seconds_to_t1": leg_row.get("seconds_to_t1"),
        "seconds_to_peak": leg_row.get("seconds_to_peak"),
        "seconds_to_sl": leg_row.get("seconds_to_sl"),
        "overnight": bool(leg_row.get("overnight")),
        "next_day_open": on.get("next_open_underlying"),
        "gap_direction": on.get("gap_direction"),
        "gap_pct": on.get("gap_pct"),
        "outcome": leg_row.get("outcome"),
        "status": leg_row.get("status"),
        "executability": leg_row.get("executability"),
        "data_quality": leg_row.get("data_quality"),
        "paper_only": True,
    }


def rows(leg_rows: list[dict]) -> list[dict]:
    return [row(r) for r in leg_rows]


def to_csv(journal_rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(COLUMNS), extrasaction="ignore")
    w.writeheader()
    for r in journal_rows:
        w.writerow(r)
    return buf.getvalue()


def reconcile(
    *,
    observations: list[dict],
    cards: list[dict],
    paper_rows: list[dict],
) -> dict:
    """§28: every stage counted, with the drop-off between them visible."""
    ce = pe = both = exact = near = missing = 0
    for o in observations:
        for lr in o.get("ladder") or []:
            has_ce = isinstance(lr.get("ce"), dict)
            has_pe = isinstance(lr.get("pe"), dict)
            ce += 1 if has_ce else 0
            pe += 1 if has_pe else 0
            both += 1 if has_ce and has_pe else 0
            for key in ("ce", "pe"):
                q = lr.get(key)
                if not isinstance(q, dict):
                    missing += 1
                    continue
                state = str(q.get("data_quality") or quality.MISSING)
                if state == quality.EXACT:
                    exact += 1
                elif state == quality.NEAR_EXACT:
                    near += 1
                elif state == quality.MISSING:
                    missing += 1

    quoted = ce + pe
    signals = [c for c in cards
               if c.get("signal") in (schema.CAS_BUY_CE, schema.CAS_BUY_PE)]
    entries = [r for r in paper_rows if r.get("entry_price") is not None]
    exits = [r for r in paper_rows if r.get("exit_ts") is not None]
    resolved = [r for r in paper_rows
                if r.get("executability") == schema.EXECUTABLE
                and r.get("net_rupees") is not None]
    unresolved = [r for r in paper_rows if r not in resolved]

    match_pct = (
        round(100.0 * (exact + near) / quoted, 1) if quoted else None
    )
    return {
        "strategy": schema.STRATEGY,
        "observations": len(observations),
        "candidates": len(cards),
        "ce_captures": ce,
        "pe_captures": pe,
        "both_side_rungs": both,
        "exact_timestamp_matches": exact,
        "near_exact_matches": near,
        "missing_books": missing,
        "exact_or_near_pct": match_pct,
        "quality_gate": (
            quality.SUFFICIENT
            if match_pct is not None and match_pct >= quality.MATCH_TARGET_PCT
            else quality.INSUFFICIENT
        ),
        "paper_signals": len(signals),
        "paper_entries": len(entries),
        "paper_exits": len(exits),
        "resolved": len(resolved),
        "unresolved": len(unresolved),
        "unresolved_reasons": _reasons(unresolved),
        "arithmetic": {
            "signals_to_entries_lost": len(signals) - len(entries),
            "entries_to_exits_lost": len(entries) - len(exits),
            "exits_to_resolved_lost": len(exits) - len(resolved),
        },
        "note": (
            "Every stage is counted from the same rows. A leg that could not be "
            "priced stays in 'unresolved' with a reason rather than being dropped "
            "from the sample."
        ),
    }


def _reasons(unresolved: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in unresolved:
        reason = _g(r, "execution", "reason")
        key = str(reason) if isinstance(reason, str) else str(
            r.get("status") or "OPEN",
        )
        out[key] = out.get(key, 0) + 1
    return out
