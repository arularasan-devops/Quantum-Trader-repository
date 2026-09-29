"""Holding a CAS leg to the next session — §16, §18.

A position bought at 15:20 and still open at 15:30 is not closed by the auction;
it is carried, and the next thing that happens to it is a gap. This module
resolves those legs against the following session's opening book and classifies
the gap that produced the result.

Deliberately separate from the intraday exit logic. The production engine has no
overnight mode and this must not become a route to one: everything here reads
recorded quotes and writes research rows.

The opening book matters more than the opening price. An option that "opened
40% up" on the mid but had no bid until 09:20 was not worth 40% to anyone, so
every overnight exit is priced at the bid like every other exit in this phase,
and a session that opens with no two-sided book resolves as ``UNKNOWN`` rather
than as a winner.
"""
from __future__ import annotations

from app.config import settings
from app.research.phase18 import execution, quality, schema

# Exits tested against the next session (§16).
AT_OPEN = "AT_OPEN"
OPEN_PLUS_5 = "OPEN_PLUS_5"
OPEN_PLUS_15 = "OPEN_PLUS_15"
OPEN_PLUS_30 = "OPEN_PLUS_30"
TARGET = "TARGET"
STOP = "STOP"
OVERNIGHT_EXITS: tuple[str, ...] = (
    AT_OPEN, OPEN_PLUS_5, OPEN_PLUS_15, OPEN_PLUS_30, TARGET, STOP,
)
_OFFSET_SECONDS: dict[str, int] = {
    AT_OPEN: 0, OPEN_PLUS_5: 300, OPEN_PLUS_15: 900, OPEN_PLUS_30: 1800,
}


def _f(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if f != f else f


def gap_threshold_pct() -> float:
    return float(settings.phase18_gap_flat_pct)


def classify_gap(prev_close: float | None, next_open: float | None) -> dict:
    """UP / DOWN / FLAT against a configurable, printed threshold."""
    pc, no = _f(prev_close), _f(next_open)
    if pc is None or no is None or pc <= 0:
        return {"gap_direction": None, "gap_pct": None,
                "threshold_pct": gap_threshold_pct(),
                "reason": "missing close or open"}
    pct = 100.0 * (no - pc) / pc
    thr = gap_threshold_pct()
    if abs(pct) <= thr:
        d = schema.GAP_FLAT
    else:
        d = schema.GAP_UP if pct > 0 else schema.GAP_DOWN
    return {
        "gap_direction": d,
        "gap_pct": round(pct, 3),
        "threshold_pct": thr,
        "reason": f"|{pct:.2f}%| against a {thr:.2f}% flat band",
    }


def resolve(
    *,
    leg_row: dict,
    next_session: str,
    next_open_underlying: float | None,
    prev_close_underlying: float | None,
    opening_quotes: list[dict],
    lots: int = 1,
) -> dict:
    """Resolve one carried leg against the next session's recorded quotes.

    ``opening_quotes`` are the stored quotes for the same option symbol, ordered
    by timestamp, starting at the open. Every requested exit clock is priced from
    the first quote at or after that offset — no interpolation, because an
    interpolated bid is not a bid.
    """
    out: dict[str, object] = {
        "episode_id": leg_row.get("episode_id"),
        "instrument": leg_row.get("instrument"),
        "carried_from": leg_row.get("session"),
        "next_session": next_session,
        "overnight": True,
        "strategy": schema.STRATEGY,
        "paper_only": True,
    }
    out.update(classify_gap(prev_close_underlying, next_open_underlying))

    usable = [
        q for q in opening_quotes
        if str(q.get("data_quality") or quality.MISSING) in quality.USABLE
    ]
    if not usable:
        out.update({
            "status": schema.EXECUTABILITY_UNKNOWN,
            "reason": "no usable opening book — leg left unresolved, not scored",
            "exits": {},
        })
        return out

    open_ts = _f(usable[0].get("ts")) or 0.0
    first = usable[0]
    bids = [(_f(q.get("ts")) or 0.0, _f(q.get("bid")), q) for q in usable]
    bids = [(t, b, q) for t, b, q in bids if b is not None]

    out.update({
        "next_open_underlying": next_open_underlying,
        "opening_bid": _f(first.get("bid")),
        "opening_ask": _f(first.get("ask")),
        "opening_spread": _f(first.get("spread")),
        "opening_spread_pct": _f(first.get("spread_pct")),
        "option_open_premium": _f(first.get("premium")) or _f(first.get("mid")),
    })

    entry_price = _f(leg_row.get("entry_price"))
    if bids and entry_price:
        peak = max(b for _, b, _ in bids)
        trough = min(b for _, b, _ in bids)
        out["overnight_mfe_points"] = round(peak - entry_price, 2)
        out["overnight_mae_points"] = round(trough - entry_price, 2)

    exits: dict[str, dict] = {}
    for name in (AT_OPEN, OPEN_PLUS_5, OPEN_PLUS_15, OPEN_PLUS_30):
        cutoff = open_ts + _OFFSET_SECONDS[name]
        pick = next((q for t, _b, q in bids if t >= cutoff), None)
        exits[name] = _settle(leg_row, pick, lots=lots)

    t1, stop = _f(leg_row.get("t1")), _f(leg_row.get("stop"))
    tgt = next((q for _t, b, q in bids if t1 is not None and b >= t1), None)
    exits[TARGET] = _settle(leg_row, tgt, lots=lots) if tgt else {
        "executability": schema.EXECUTABILITY_UNKNOWN,
        "reason": "target never bid for in the next session",
    }
    sl = next((q for _t, b, q in bids if stop is not None and b <= stop), None)
    exits[STOP] = _settle(leg_row, sl, lots=lots) if sl else {
        "executability": schema.EXECUTABILITY_UNKNOWN,
        "reason": "stop never touched in the next session",
    }

    out["exits"] = exits
    out["status"] = "RESOLVED"
    return out


def _settle(leg_row: dict, quote: dict | None, *, lots: int) -> dict:
    if quote is None:
        return {"executability": schema.EXECUTABILITY_UNKNOWN,
                "reason": "no quote at this clock"}
    s = execution.settle(
        instrument=str(leg_row.get("instrument") or ""),
        entry_quote=leg_row.get("entry_quote"),
        exit_quote=quote,
        lots=lots,
    )
    return {
        "executability": s.get("executability"),
        "exit_price": s.get("exit_price"),
        "net_rupees": s.get("net_rupees"),
        "net_points": s.get("net_points"),
        "reason": s.get("reason"),
    }


def summarise(resolutions: list[dict]) -> dict:
    """§18: does carrying the leg overnight help, and how often is the gap kind?"""
    resolved = [r for r in resolutions if r.get("status") == "RESOLVED"]
    by_gap: dict[str, dict] = {}
    for g in (schema.GAP_UP, schema.GAP_DOWN, schema.GAP_FLAT):
        rows = [r for r in resolved if r.get("gap_direction") == g]
        nets = [
            _f((r.get("exits") or {}).get(AT_OPEN, {}).get("net_rupees"))
            for r in rows
        ]
        nets = [n for n in nets if n is not None]
        by_gap[g] = {
            "n": len(rows),
            "priced": len(nets),
            "profitable_pct": (
                round(100.0 * sum(1 for n in nets if n > 0) / len(nets), 1)
                if nets else None
            ),
            "mean_net_rupees": (
                round(sum(nets) / len(nets), 2) if nets else None
            ),
        }

    per_exit: dict[str, dict] = {}
    for name in OVERNIGHT_EXITS:
        nets = []
        for r in resolved:
            n = _f((r.get("exits") or {}).get(name, {}).get("net_rupees"))
            if n is not None:
                nets.append(n)
        per_exit[name] = {
            "priced": len(nets),
            "mean_net_rupees": round(sum(nets) / len(nets), 2) if nets else None,
            "win_pct": (
                round(100.0 * sum(1 for n in nets if n > 0) / len(nets), 1)
                if nets else None
            ),
        }

    unresolved = len(resolutions) - len(resolved)
    return {
        "carried": len(resolutions),
        "resolved": len(resolved),
        "unresolved": unresolved,
        "by_gap": by_gap,
        "by_exit": per_exit,
        "gap_threshold_pct": gap_threshold_pct(),
        "verdict": (
            schema.REQUIRES_MORE_DATA if len(resolved) < 30
            else "MEASURED"
        ),
        "note": (
            "Every overnight exit is priced at the bid. A next session that opens "
            "without a two-sided book leaves the leg unresolved rather than "
            "counting it as a result."
        ),
    }
