"""Phase 11 §4–5 — building the vehicle comparison over a replay. RESEARCH ONLY.

Takes the BUY events Phase 7's path reconstruction already produced (production
engine, production levels, no lookahead) and, for each one, evaluates the futures
contract and the option leg over the same window and writes the comparison.

Reusing those events rather than re-detecting signals is the point: the comparison
is only meaningful if both vehicles are judged on the *same* market event that the
production engine actually called.
"""
from __future__ import annotations

from app.models import Candle
from app.research.phase7.dataset import ChainSeries, session_of

from . import futures11, options11, vehicles


def build(*, per: dict[str, dict], sessions: int = 0,
          holdout_sessions: int = 0,
          write_log: bool = True) -> dict:
    """Compare vehicles for every replayed BUY event, by instrument.

    ``per`` is Phase 9's per-instrument replay block: ``{name: {"events": [...],
    "candles": [...], "series": ChainSeries}}``.
    """
    rows: list[dict] = []
    skipped: dict[str, int] = {}

    for name, block in per.items():
        candles: list[Candle] = block.get("candles") or []
        series: ChainSeries | None = block.get("series")
        if series is None:
            skipped["no_chain_series"] = skipped.get("no_chain_series", 0) + 1
            continue
        for ev in block.get("events") or []:
            side = futures11.side_for(ev.side)
            fut = futures11.evaluate(name, candles, ev.bar, side) if side else None
            opt = options11.evaluate(
                name, series, int(ev.ts), symbol=ev.symbol, option_type=ev.side,
                stop=ev.stop, targets=(ev.target1, ev.target2, ev.target3),
                spot=ev.spot)
            if fut is not None and not fut.get("measurable"):
                key = f"futures:{fut.get('reason', 'unknown')}"
                skipped[key] = skipped.get(key, 0) + 1
            if not opt.get("measurable"):
                key = f"options:{opt.get('reason', 'unknown')}"
                skipped[key] = skipped.get(key, 0) + 1
            rows.append(vehicles.compare(
                instrument=name, session=session_of(int(ev.ts)),
                signal_ts=int(ev.ts), direction=side,
                futures_row=fut, options_result=opt,
                production_action="BUY",
                production_score=float(getattr(ev, "trade_score", 0.0) or 0.0),
            ))

    written = vehicles.write(rows) if (write_log and rows) else None
    rep = vehicles.report(rows, sessions=sessions,
                          holdout_sessions=holdout_sessions)
    rep["events_compared"] = len(rows)
    rep["not_measurable"] = skipped
    rep["log_written_to"] = written
    return {"report": rep, "rows": rows}
