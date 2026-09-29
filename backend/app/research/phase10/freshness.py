"""Phase 10 §4 — signal freshness audit. RESEARCH ONLY, blocks nothing.

The question is narrow and uncomfortable: of the decisions production actually
made, how many were made on data that was already known to be old?

The engine already stamps every reconstructed signal with ``data_flag``
(FRESH / STALE / NO_DATA) and ``chain_age_sec``. Phase 9 used those to *reject*
trades inside a research qualifier. Phase 10 uses them to describe the production
board itself, split by what production decided, because the two facts land very
differently: a stale WAIT costs nothing, and a stale BUY is an order placed
against a price that no longer exists.

Nothing is blocked. No production gate learns about freshness from this module —
that would be a production change, and Phase 10 makes none.

One honest limit, stated here rather than in a footnote: exchange-to-receive
latency is not in the recorded schema. ``chain_age_sec`` is the available proxy
for how old the option view was, and it is reported as chain age, never as
latency. The live path measures true latency (``app.market.tick_quality``); the
replay cannot, and inventing it would be the same error this phase is correcting.
"""
from __future__ import annotations

from app.research.phase7.policies import median

FRESH = "FRESH"
STALE = "STALE"
NO_DATA = "NO_DATA"
UNKNOWN = "UNKNOWN"

# Chain-age bands, in seconds. The chain is captured on a ~60s cycle, so one
# missed cycle (<=120s) is the normal case and is not called old.
CHAIN_BANDS = ((0.0, 120.0), (120.0, 300.0), (300.0, 900.0), (900.0, 1e12))


def chain_band(age: float | None) -> str:
    if age is None:
        return UNKNOWN
    for lo, hi in CHAIN_BANDS:
        if lo <= age < hi:
            return "<2min" if lo == 0.0 else (
                f">{lo / 60:.0f}min" if hi >= 1e12 else f"{lo / 60:.0f}-{hi / 60:.0f}min")
    return UNKNOWN


def state_of(row: dict) -> str:
    """The freshness state production itself recorded for this decision."""
    flag = row.get("data_flag")
    if flag in (FRESH, STALE, NO_DATA):
        return str(flag)
    return UNKNOWN


def row_of(trade: dict) -> dict:
    """One audit row per production decision. §4's required fields, no more."""
    return {
        "signal_ts_ist": trade.get("ts_ist"),
        "session": trade.get("session"),
        "instrument": trade.get("instrument"),
        "side": trade.get("side"),
        "symbol": trade.get("symbol"),
        "ltp_at_signal": trade.get("entry"),
        "signal_score": trade.get("confidence"),
        "data_state": state_of(trade),
        "is_stale": state_of(trade) in (STALE, NO_DATA),
        "chain_age_sec": trade.get("chain_age_sec"),
        "chain_age_band": chain_band(trade.get("chain_age_sec")),
        # Both timestamps are required by §4 and neither is in this schema; saying
        # so per row is what stops a later reader assuming they were checked.
        "exchange_ts": None,
        "receive_ts": None,
        "source": "REPLAY_OF_RECORDED_HISTORY",
        "realised_r": trade.get("realised_r"),
        "net_r": trade.get("net_r"),
        "target_before_stop_hit": trade.get("target_before_stop_hit"),
    }


def _rates(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0, "fresh_pct": None, "stale_pct": None, "unknown_pct": None}
    n = len(rows)
    fresh = sum(1 for r in rows if r["data_state"] == FRESH)
    stale = sum(1 for r in rows if r["is_stale"])
    unknown = sum(1 for r in rows if r["data_state"] == UNKNOWN)
    return {
        "n": n,
        "fresh": fresh,
        "stale": stale,
        "unknown": unknown,
        "fresh_pct": round(100.0 * fresh / n, 1),
        "stale_pct": round(100.0 * stale / n, 1),
        "unknown_pct": round(100.0 * unknown / n, 1),
    }


def _outcome(rows: list[dict]) -> dict:
    nets = [r["net_r"] for r in rows if r["net_r"] is not None]
    grosses = [r["realised_r"] for r in rows if r["realised_r"] is not None]
    return {
        "n": len(rows),
        "target_before_stop_pct": round(
            100.0 * sum(1 for r in rows if r["target_before_stop_hit"]) / len(rows), 1)
        if rows else None,
        "gross_expectancy_r": round(sum(grosses) / len(grosses), 3) if grosses else None,
        "net_expectancy_r": round(sum(nets) / len(nets), 3) if nets else None,
        "median_chain_age_sec": median([r["chain_age_sec"] for r in rows
                                        if r["chain_age_sec"] is not None]),
    }


def study(rows: list[dict], sessions: int) -> dict:
    """The §4 audit. Every reconstructed row here is a production BUY that was
    taken, so the BUY rate and the overall rate are the same number on this
    dataset — and that identity is reported rather than papered over with a
    second denominator that does not exist in the data."""
    by_state = {state: _outcome([r for r in rows if r["data_state"] == state])
                for state in (FRESH, STALE, NO_DATA, UNKNOWN)}
    by_band: dict[str, dict] = {}
    for r in rows:
        by_band.setdefault(r["chain_age_band"], []).append(r)
    by_instrument: dict[str, dict] = {}
    for r in rows:
        by_instrument.setdefault(r["instrument"], []).append(r)

    overall = _rates(rows)
    fresh_rows = [r for r in rows if r["data_state"] == FRESH]
    stale_rows = [r for r in rows if r["is_stale"]]
    fresh_net = _outcome(fresh_rows).get("net_expectancy_r")
    stale_net = _outcome(stale_rows).get("net_expectancy_r")
    costs = (fresh_net is not None and stale_net is not None and stale_net < fresh_net)

    return {
        "decisions_audited": len(rows),
        "denominator": "reconstructed production BUYs that resolved. WAIT and "
                       "NO_TRADE decisions are not in the recorded schema, so a "
                       "fresh-WAIT rate cannot be computed from this dataset and is "
                       "not guessed at",
        "overall": overall,
        "fresh_buy_pct": overall["fresh_pct"],
        "stale_buy_pct": overall["stale_pct"],
        "by_state": by_state,
        "by_chain_age_band": {k: _outcome(v) for k, v in sorted(by_band.items())},
        "by_instrument": {k: {**_rates(v), **_outcome(v)}
                          for k, v in sorted(by_instrument.items())},
        "stale_costs_expectancy": bool(costs),
        "reading": (
            f"{overall['fresh_pct']}% of production BUYs fired on data the engine "
            f"itself labelled FRESH and {overall['stale_pct']}% on data it labelled "
            f"STALE or absent"
            + ("; stale decisions carried the worse net expectancy of the two"
               if costs else
               "; on this sample stale decisions did NOT carry a worse net "
               "expectancy, which is a sample-size statement and not a defence of "
               "trading on stale data")),
        "sessions": sessions,
        "guarantee": "freshness is measured here and enforced nowhere. No gate, "
                     "threshold or block in production reads this module",
    }
