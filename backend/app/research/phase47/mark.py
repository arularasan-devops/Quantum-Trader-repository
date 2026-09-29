"""Phase 47 — pricing one open paper call against the latest measured quote.

Pure: no I/O, no clock of its own, no store, no feed. Everything it needs is
passed in — the journalled call and the quote to mark it against — which is what
makes it testable against fabricated books without a market and identical in the
smoke and in the session.

The arithmetic is Phase 35's, imported rather than restated:
:func:`app.research.phase35.book.exit_fill` picks the executable side,
:func:`~app.research.phase35.book.gross_pct` signs the move, and
:func:`~app.research.phase35.book.net_pct` charges the round trip. A second copy
of those three formulas would agree with the originals only until one of them
was corrected, and the resolver already depends on them.
"""
from __future__ import annotations

from app.research.phase35 import book
from app.research.phase47 import (
    MARKED,
    MEASURED_COST,
    MODELLED_COST,
    NO_COST,
    STALE_MARK_SEC,
    UNMARKABLE_NO_BOOK,
    UNMARKABLE_NO_COST,
    UNMARKABLE_NO_ENTRY,
    UNMARKABLE_NO_LATER_QUOTE,
)


def mark(call: dict, quote: dict | None, *, now: float | None = None) -> dict:
    """One priced call: what the leg is worth on the quote it is marked against.

    ``call`` is a Phase 46 overlay row (or a Phase 45 event row — the fields
    used are common to both): vehicle, direction, entry price and the round trip
    already measured at the instant. ``quote`` is the latest measured quote for
    that same contract in that same session, or ``None`` when the capture holds
    none, which is a reported state and not a zero.

    The returned row always carries ``state``, and the caller must read it
    before reading ``net_pct``: an unmarkable call has no net, and treating it
    as flat is the one arithmetic that would quietly flatter the tally.
    """
    entry = _num(call.get("entry_price"))
    vehicle = str(call.get("vehicle") or "")
    direction = str(call.get("direction") or "")
    # The measured round trip when the instant had a two-sided book, the
    # modelled one otherwise — and the row says which, because Phase 42 was
    # poisoned precisely by a ratio whose denominator changed basis without
    # saying so. A tally mixing the two is reported, never silently pooled.
    cost = _num(call.get("measured_cost_points"))
    cost_basis = MEASURED_COST
    if cost is None:
        cost = _num(call.get("modelled_cost_points"))
        cost_basis = MODELLED_COST
    if cost is None:
        cost_basis = NO_COST
    base = {
        "event_id": call.get("event_id"),
        "overlay_id": call.get("overlay_id"),
        "obs_id": call.get("obs_id"),
        "session": call.get("session"),
        "decision_ts": call.get("decision_ts"),
        "instrument": call.get("instrument"),
        "vehicle": vehicle,
        "contract": call.get("contract"),
        "strike": call.get("strike"),
        "expiry": call.get("expiry"),
        "direction": direction,
        "production_signal": call.get("production_signal"),
        "overlay_state": call.get("overlay_state"),
        "research_candidate": call.get("research_candidate"),
        "entry_side": call.get("entry_side"),
        "entry_price": entry,
        "cost_points": cost,
        "cost_basis": cost_basis,
        "cost_evidence": call.get("cost_evidence"),
        # Carried through so a reader can see the claim the call was admitted
        # on beside what it is actually worth now.
        "expected_move_points": _num(call.get("expected_move_points")),
        "expected_move_over_measured_cost": _num(
            call.get("expected_move_over_measured_cost")
        ),
        "mark_ts": None,
        "mark_price": None,
        "mark_side": None,
        "mark_evidence": None,
        "mark_age_sec": None,
        "stale_mark": False,
        "gross_pct": None,
        "net_pct": None,
        "net_points": None,
        "state": MARKED,
        "reason": None,
    }
    if entry is None or entry <= 0:
        return {**base, "state": UNMARKABLE_NO_ENTRY,
                "reason": "the instant recorded no executable entry price, so "
                          "there is nothing to mark"}
    if not quote:
        return {**base, "state": UNMARKABLE_NO_LATER_QUOTE,
                "reason": "no later measured quote for this contract in this "
                          "session — a statement about the capture, not about "
                          "the call"}
    fill = book.exit_fill(quote, vehicle=vehicle, direction=direction)
    price = _num(fill.get("price"))
    # ``exit_fill`` will fall back to the traded price so a *path* is not lost,
    # and labels it MEASURED_TRADED_PRICE. This board refuses that fallback: an
    # LTP is a print somebody else got, not a price this leg could be closed
    # at, and a running net built on it would be the one substitution every
    # other cost decision in this project was made to avoid.
    if price is None or fill.get("evidence") != book.MEASURED_EXECUTABLE:
        return {**base, "state": UNMARKABLE_NO_BOOK,
                "reason": (
                    "the mark quote carried no executable exit side "
                    f"({fill.get('evidence')}"
                    f"{'/' + str(fill.get('reason')) if fill.get('reason') else ''})"
                    " — a traded print is not a price this leg could be closed at"
                )}
    gross = book.gross_pct(entry, price, vehicle=vehicle, direction=direction)
    net = book.net_pct(gross, cost, entry)
    if net is None:
        # A gross with no round trip to charge is exactly the number this
        # project has spent forty phases refusing to quote. It is reported as
        # unmarkable rather than shown, so it can be neither counted in the
        # tally nor mistaken for a flat leg.
        return {**base, "state": UNMARKABLE_NO_COST, "gross_pct": gross,
                "mark_ts": _num(quote.get("ts")), "mark_price": price,
                "mark_side": fill.get("side"),
                "mark_evidence": fill.get("evidence"),
                "reason": "the instant recorded no round trip for this leg, "
                          "and a gross figure is not a result"}
    mark_ts = _num(quote.get("ts"))
    age = None
    if mark_ts is not None and now is not None:
        age = round(max(0.0, float(now) - mark_ts), 1)
    return {
        **base,
        "mark_ts": mark_ts,
        "mark_price": price,
        "mark_side": fill.get("side"),
        "mark_evidence": fill.get("evidence"),
        "mark_age_sec": age,
        "stale_mark": bool(age is not None and age > STALE_MARK_SEC),
        "gross_pct": gross,
        "net_pct": net,
        "net_points": (
            None if net is None else round(net * entry / 100.0, 4)
        ),
        "state": MARKED,
        "reason": None,
    }


def tally(rows: list[dict]) -> dict:
    """The running result of a set of marked calls.

    Only ``MARKED`` rows with a net contribute. Unmarkable calls are counted
    separately and reported beside the tally, because a board that showed 12
    winners out of 12 marked while 40 calls could not be priced would be
    describing the capture and calling it a result.

    ``net_pct_total`` is a **sum of per-leg percentages**, not a portfolio
    return: the legs are unsized and overlapping. It is labelled as such
    wherever it is shown.
    """
    priced = [r for r in rows if r.get("state") == MARKED
              and _num(r.get("net_pct")) is not None]
    unmarkable = [r for r in rows if r.get("state") != MARKED]
    nets = [float(r["net_pct"]) for r in priced]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    gains = sum(wins)
    pain = -sum(losses)
    costs = [c for c in (_num(r.get("cost_points")) for r in priced)
             if c is not None]
    return {
        "calls": len(rows),
        "marked": len(priced),
        "unmarkable": len(unmarkable),
        "unmarkable_states": _counts(unmarkable),
        "stale_marks": sum(1 for r in priced if r.get("stale_mark")),
        "net_pct_total": round(sum(nets), 4) if nets else None,
        "net_pct_mean": round(sum(nets) / len(nets), 4) if nets else None,
        "winners": len(wins),
        "losers": len(losses),
        "flat": len(nets) - len(wins) - len(losses),
        "win_rate_pct": (
            round(100.0 * len(wins) / len(nets), 2) if nets else None
        ),
        # None rather than infinity when nothing lost yet: a profit factor on
        # zero losses is not a large number, it is an unmeasured one.
        "profit_factor": round(gains / pain, 3) if pain > 0 else None,
        "best_pct": round(max(nets), 4) if nets else None,
        "worst_pct": round(min(nets), 4) if nets else None,
        "avg_cost_points": (
            round(sum(costs) / len(costs), 4) if costs else None
        ),
        "cost_bases": _bases(priced),
        "drawdown_pct": drawdown(nets),
        "net_is": "SUM_OF_PER_LEG_NET_PERCENTAGES_UNSIZED_AND_OVERLAPPING",
    }


def drawdown(nets: list[float]) -> float | None:
    """Deepest peak-to-trough fall of the cumulative net, in the same unit.

    Taken in the order given, which the caller keeps as decision order: a
    drawdown computed on a set rather than a path is not a drawdown.
    """
    if not nets:
        return None
    peak = running = 0.0
    worst = 0.0
    for net in nets:
        running += net
        peak = max(peak, running)
        worst = min(worst, running - peak)
    return round(worst, 4)


def _bases(rows: list[dict]) -> dict:
    """How many priced legs were charged on which cost basis.

    Reported rather than reconciled: a column built half on a measured round
    trip and half on a modelled one is two measurements, and the only honest
    thing a tally can do is say so.
    """
    out: dict[str, int] = {}
    for row in rows:
        key = str(row.get("cost_basis") or NO_COST)
        out[key] = out.get(key, 0) + 1
    return out


def _counts(rows: list[dict]) -> dict:
    out: dict[str, int] = {}
    for row in rows:
        key = str(row.get("state"))
        out[key] = out.get(key, 0) + 1
    return out


def _num(value: object) -> float | None:
    """A float, or None. A missing quantity is missing, never zero."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
