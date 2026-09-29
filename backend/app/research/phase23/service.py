"""Phase 23 wiring: one observation call, one resolution call, read-only reads.

The thinnest hook that can work. It runs AFTER the live auto-trader has had its
turn on the same tick, so what it records is what the engine actually did rather
than what it might do, it returns nothing the caller reads, and it swallows its
own failures into a health counter.
"""
from __future__ import annotations

from app.models import Decision, OptionQuote
from app.research.phase23 import hurdle as hurdle_mod, report, shadow, verdict


def observe(instrument: str, decision: Decision, chain: list[OptionQuote], *,
            lot_size: int, lots: int, engine_bought: bool,
            engine_fill: float | None, in_position_before: bool,
            now: int | None = None) -> str | None:
    return shadow.observe(
        instrument, decision, chain, lot_size=lot_size, lots=lots,
        engine_bought=engine_bought, engine_fill=engine_fill,
        in_position_before=in_position_before, now=now,
    )


def resolve(instrument: str, trade_row: dict) -> None:
    shadow.resolve(instrument, trade_row)


def health() -> dict:
    return shadow.health()


def rows(limit: int = 200) -> list[dict]:
    """Newest-first opportunity rows for the panel."""
    data = report.store.rows()
    data.sort(key=lambda r: int(r.get("ts") or 0), reverse=True)
    if limit and limit > 0:
        data = data[:limit]
    return [
        {
            "ts": r.get("ts"),
            "date": report.ist_date(r.get("ts")),
            "time_bucket": report.time_bucket(r.get("ts")),
            "instrument": r.get("instrument"),
            "feed": r.get("feed"),
            "real_feed": r.get("real_feed"),
            "option": r.get("option"),
            "option_type": r.get("option_type"),
            "strike": r.get("strike"),
            "premium": r.get("entry_premium_ask") or r.get("premium_last"),
            "bid": r.get("bid"),
            "ask": r.get("ask"),
            "measured_spread_pct": r.get("measured_spread_pct"),
            "round_trip_cost_rupees": r.get("round_trip_cost_rupees"),
            "hurdle_pct": r.get("hurdle_pct"),
            "hurdle_status": r.get("hurdle_status"),
            "hurdle_reason": r.get("hurdle_reason"),
            "signal": r.get("signal"),
            "confidence": r.get("confidence"),
            "engine_would_buy": r.get("engine_would_buy"),
            "gate_3_allows": r.get("arm_shadow_3"),
            "gate_5_allows": r.get("arm_shadow_5"),
            "outcome": r.get("outcome"),
            "exit_reason": r.get("exit_reason"),
            "net_pnl": r.get("net_pnl"),
            "net_r": r.get("net_r"),
            "hold_minutes": r.get("hold_minutes"),
            "resolved": r.get("resolved"),
        }
        for r in data
    ]


def summary() -> dict:
    """The report plus the verdict, in one read-only payload."""
    built = report.build()
    return {
        "gates": {"strict_pct": hurdle_mod.GATE_STRICT_PCT,
                  "loose_pct": hurdle_mod.GATE_LOOSE_PCT},
        "report": built,
        "verdict": verdict.evaluate(),
        "health": shadow.health(),
    }
