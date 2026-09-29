"""What the window actually did — to the underlying (§6) and to premium (§7).

Both halves are computed from the stored observations rather than accumulated
live, so a restarted app loses nothing and a report can be re-derived from disk
at any time.

The two halves exist separately because they answer different questions and one
does not imply the other. §6 asks whether CAS is a repeatable directional event
in the index. §7 asks whether that event reaches the option — and the honest
comparison, ``option_move`` against ``underlying_move``, is where a 2,200-point
print stops being a 44x screenshot and becomes a number with a spread attached.

Every premium series is measured twice: on the mid (what the screenshot shows)
and on the bid (what a seller could have received). The gap between them is the
whole subject of this phase, so it is never collapsed into one column.
"""
from __future__ import annotations

from statistics import median

from app.research.phase18 import quality, schema, session

_DIR_UP = "UP"
_DIR_DOWN = "DOWN"
_DIR_FLAT = "FLAT"

# What counts as "the underlying went somewhere" when no ATR is available.
FLAT_PCT = 0.05


def _f(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if f != f else f


def _by_session(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        key = (str(r.get("session") or ""), str(r.get("instrument") or ""))
        if key[0] and key[1]:
            out.setdefault(key, []).append(r)
    for rows_ in out.values():
        rows_.sort(key=lambda r: _f(r.get("intent_ts")) or 0.0)
    return out


def _mark_prices(rows: list[dict]) -> dict[str, float]:
    """The underlying at each of the five §6 marks, when a sample stands for one.

    A sample only counts as a mark if it was taken within the tolerance in
    :func:`session.nearest_mark`; the nearest sample to 15:20 taken at 15:22 is
    not the 15:20 price.
    """
    out: dict[str, float] = {}
    for r in rows:
        mark = r.get("mark")
        px = _f(r.get("underlying"))
        if isinstance(mark, str) and px is not None and mark not in out:
            out[mark] = px
    return out


def underlying_move(rows: list[dict], *, atr: float | None = None) -> dict:
    """§6 for one instrument-session."""
    marks = _mark_prices(rows)
    prices = [(_f(r.get("intent_ts")) or 0.0, _f(r.get("underlying")))
              for r in rows]
    series = [(t, p) for t, p in prices if p is not None]
    ref = marks.get("T1510") or (series[0][1] if series else None)
    last = marks.get("T1530") or (series[-1][1] if series else None)

    out: dict[str, object] = {
        "session": str(rows[0].get("session")) if rows else None,
        "instrument": str(rows[0].get("instrument")) if rows else None,
        "samples": len(series),
        "marks": marks,
        "marks_present": sorted(marks),
        "marks_missing": [m for m in session.MARK_LABELS if m not in marks],
        "reference": ref,
        "close": last,
    }
    if ref is None or last is None or ref <= 0:
        out["status"] = schema.REQUIRES_MORE_DATA
        return out

    hi = max(p for _, p in series)
    lo = min(p for _, p in series)
    move = last - ref
    pct = 100.0 * move / ref
    up = hi - ref
    down = ref - lo

    direction = _DIR_FLAT
    if abs(pct) > FLAT_PCT:
        direction = _DIR_UP if move > 0 else _DIR_DOWN
    # A reversal is an excursion that the close gave back: the window travelled
    # meaningfully one way and finished the other.
    excursion = max(up, down)
    reversed_ = bool(excursion > 0 and abs(move) < 0.5 * excursion)

    out.update({
        "move_points": round(move, 2),
        "move_pct": round(pct, 4),
        "move_atr": round(move / atr, 3) if atr else None,
        "max_favourable_up": round(up, 2),
        "max_favourable_down": round(down, 2),
        "high": hi,
        "low": lo,
        "range_points": round(hi - lo, 2),
        "direction": direction,
        "reversed": reversed_,
        "leg_moves": _leg_moves(marks),
        "status": "MEASURED",
    })
    return out


def _leg_moves(marks: dict[str, float]) -> dict[str, float | None]:
    """Move within each five-minute sub-window, when both ends exist."""
    out: dict[str, float | None] = {}
    for a, b, label in (
        ("T1510", "T1515", "W_1510_1515"),
        ("T1515", "T1520", "W_1515_1520"),
        ("T1520", "T1525", "W_1520_1525"),
        ("T1525", "T1530", "W_1525_1530"),
    ):
        pa, pb = marks.get(a), marks.get(b)
        out[label] = None if pa is None or pb is None else round(pb - pa, 2)
    return out


def underlying_report(observations: list[dict]) -> dict:
    """§6 across every captured instrument-session."""
    per: list[dict] = []
    for (sess, inst), rows in sorted(_by_session(observations).items()):
        m = underlying_move(rows)
        m["session"], m["instrument"] = sess, inst
        per.append(m)

    measured = [m for m in per if m.get("status") == "MEASURED"]
    moves = [abs(float(m["move_pct"])) for m in measured
             if isinstance(m.get("move_pct"), (int, float))]
    dirs: dict[str, int] = {}
    for m in measured:
        d = str(m.get("direction"))
        dirs[d] = dirs.get(d, 0) + 1
    reversals = sum(1 for m in measured if m.get("reversed"))

    return {
        "instrument_sessions": len(per),
        "measured": len(measured),
        "median_abs_move_pct": round(median(moves), 4) if moves else None,
        "max_abs_move_pct": round(max(moves), 4) if moves else None,
        "directions": dirs,
        "reversal_pct": (
            round(100.0 * reversals / len(measured), 1) if measured else None
        ),
        "per_session": per,
        "question": (
            "Does CAS create a repeatable directional event in the underlying?"
        ),
        "answer": _repeatability(measured, dirs),
    }


def _repeatability(measured: list[dict], dirs: dict[str, int]) -> str:
    if len(measured) < 20:
        return (
            f"{schema.REQUIRES_MORE_DATA} — {len(measured)} instrument-session(s); "
            "§26 asks for at least 20 distinct CAS sessions before this question "
            "is answered at all."
        )
    top = max(dirs.values()) if dirs else 0
    share = 100.0 * top / len(measured)
    if share >= 70.0:
        return (
            f"One direction in {share:.0f}% of sessions. Directional persistence "
            "is worth testing out-of-sample; it is not yet an edge."
        )
    return (
        f"No dominant direction ({share:.0f}% in the most common). CAS moves the "
        "underlying, but the direction is not repeatable from these sessions."
    )


# --------------------------------------------------------------- §7 premium
def _series(rows: list[dict], rung: str, side: str) -> list[dict]:
    """The ordered quote series for one ladder position across the window."""
    out: list[dict] = []
    for r in rows:
        for lr in r.get("ladder") or []:
            if lr.get("rung") != rung:
                continue
            q = lr.get("ce" if side == schema.CE else "pe")
            if not q:
                continue
            out.append({
                "ts": _f(r.get("intent_ts")),
                "state": r.get("cas_state"),
                "mark": r.get("mark"),
                "quality": q.get("data_quality"),
                "bid": _f(q.get("bid")),
                "ask": _f(q.get("ask")),
                "mid": _f(q.get("mid")),
                "premium": _f(q.get("premium")),
                "spread": _f(q.get("spread")),
                "spread_pct": _f(q.get("spread_pct")),
                "strike": _f(q.get("strike")),
                "underlying": _f(r.get("underlying")),
            })
    return out


def premium_path(rows: list[dict], rung: str, side: str) -> dict | None:
    """§7 for one ladder position in one instrument-session.

    The three columns the spec demands are computed here and never merged:

    * ``mid_*`` — what a screenshot of the chain would have shown;
    * ``bid_*`` — what a holder could actually have sold into;
    * the difference, reported as ``theoretical_over_executable``.
    """
    ser = _series(rows, rung, side)
    if not ser:
        return None
    usable = [s for s in ser if s["quality"] in quality.USABLE]
    if not usable:
        return {
            "rung": rung, "side": side, "samples": len(ser), "usable": 0,
            "status": quality.INSUFFICIENT,
        }

    entry_ask = next((s["ask"] for s in usable if s["ask"]), None)
    mids = [s["mid"] for s in usable if s["mid"] is not None]
    bids = [s["bid"] for s in usable if s["bid"] is not None]
    first_mid = mids[0] if mids else None
    last_bid = bids[-1] if bids else None
    peak_mid = max(mids) if mids else None
    peak_bid = max(bids) if bids else None
    trough_bid = min(bids) if bids else None

    def pct(a: float | None, b: float | None) -> float | None:
        if a is None or b is None or b <= 0:
            return None
        return round(100.0 * (a - b) / b, 2)

    spreads = [s["spread_pct"] for s in usable if s["spread_pct"] is not None]
    return {
        "rung": rung,
        "side": side,
        "samples": len(ser),
        "usable": len(usable),
        "entry_ask": entry_ask,
        "first_mid": first_mid,
        "peak_mid": peak_mid,
        "peak_bid": peak_bid,
        "close_bid": last_bid,
        "trough_bid": trough_bid,
        # Marked (mid, no cost) vs executable (bought at ask, sold at bid).
        "theoretical_return_pct": pct(peak_mid, first_mid),
        "executable_return_pct": pct(peak_bid, entry_ask),
        "close_return_pct": pct(last_bid, entry_ask),
        "mfe_pct": pct(peak_bid, entry_ask),
        "mae_pct": pct(trough_bid, entry_ask),
        "median_spread_pct": (
            round(median(spreads), 2) if spreads else None
        ),
        "max_spread_pct": round(max(spreads), 2) if spreads else None,
        "theoretical_over_executable": (
            None
            if pct(peak_mid, first_mid) is None or not pct(peak_bid, entry_ask)
            else round(pct(peak_mid, first_mid) / pct(peak_bid, entry_ask), 2)
        ),
        "status": "MEASURED",
    }


def premium_report(observations: list[dict]) -> dict:
    """§7 across the ladder, both sides, every captured session."""
    per: list[dict] = []
    for (sess, inst), rows in sorted(_by_session(observations).items()):
        und = underlying_move(rows)
        for rung in schema.RUNGS:
            for side in schema.SIDES:
                p = premium_path(rows, rung, side)
                if p is None:
                    continue
                p["session"], p["instrument"] = sess, inst
                p["underlying_move_pct"] = und.get("move_pct")
                p["underlying_direction"] = und.get("direction")
                exec_ret = p.get("executable_return_pct")
                um = und.get("move_pct")
                p["premium_per_underlying_pct"] = (
                    None
                    if not isinstance(exec_ret, (int, float))
                    or not isinstance(um, (int, float)) or abs(um) < 1e-9
                    else round(exec_ret / abs(um), 1)
                )
                per.append(p)

    measured = [p for p in per if p.get("status") == "MEASURED"]
    by_rung: dict[str, dict] = {}
    for rung in schema.RUNGS:
        sub = [p for p in measured if p["rung"] == rung]
        if not sub:
            continue
        theo = [p["theoretical_return_pct"] for p in sub
                if isinstance(p.get("theoretical_return_pct"), (int, float))]
        exe = [p["executable_return_pct"] for p in sub
               if isinstance(p.get("executable_return_pct"), (int, float))]
        spr = [p["median_spread_pct"] for p in sub
               if isinstance(p.get("median_spread_pct"), (int, float))]
        by_rung[rung] = {
            "n": len(sub),
            "median_theoretical_return_pct": (
                round(median(theo), 2) if theo else None
            ),
            "median_executable_return_pct": (
                round(median(exe), 2) if exe else None
            ),
            "median_spread_pct": round(median(spr), 2) if spr else None,
        }

    return {
        "paths": len(per),
        "measured": len(measured),
        "by_rung": by_rung,
        "per_path": per[-400:],
        "note": (
            "The theoretical column is the mid-to-mid mark a screenshot would "
            "show. The executable column buys at the ask and sells at the bid. "
            "Where the first is much larger than the second, the difference was "
            "never available to anyone."
        ),
    }
