"""Phase 33 §5 — overnight held separately, never blended into an intraday hold.

An intraday horizon and an overnight hold are different risks: one is bounded by
bars that actually traded, the other jumps a closed market. §5 asks for them
apart, so this module answers only two questions, both from the *session close*
onward — the point at which a trade becomes an overnight trade:

* what the next session's open paid;
* what the first 30 minutes of the next session paid.

A session with no following session in the data is dropped rather than resolved
against nothing, and the gap itself is reported next to the outcome so a "good
overnight" that was one favourable gap is visible as such.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import data
from app.research.phase31.excursion import LONG
from app.research.phase33.hold import _f
from app.research.phase33.path import Path

# Minutes into the next session for the second measurement.
NEXT_OPEN_PLUS = 30


def _session_edges(sess: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """First and last row index of every session, in chronological order."""
    change = np.flatnonzero(np.diff(sess)) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change - 1, [sess.size - 1]))
    return starts, ends


def measure(s: data.Series, p: Path, mask: np.ndarray) -> dict:
    """Overnight outcomes for the cohort's rows, resolved on the next session."""
    sess = p.session
    starts, ends = _session_edges(sess)
    sid_of_start = sess[starts]

    next_open = np.full(sess.size, np.nan)
    next_plus = np.full(sess.size, np.nan)
    close_gap = np.full(sess.size, np.nan)
    for i in range(sid_of_start.size):
        if i + 1 >= starts.size:
            continue
        rows = slice(starts[i], ends[i] + 1)
        nxt_start = starts[i + 1]
        nxt_end = ends[i + 1]
        op = float(s.open[nxt_start])
        plus_idx = min(nxt_start + NEXT_OPEN_PLUS, nxt_end)
        next_open[rows] = op
        next_plus[rows] = float(s.close[plus_idx])
        close_gap[rows] = op - float(s.close[ends[i]])

    sgn = 1.0 if p.side == LONG else -1.0
    entry = p.entry
    with np.errstate(invalid="ignore"):
        to_open = 100.0 * sgn * (next_open - entry) / entry
        to_plus = 100.0 * sgn * (next_plus - entry) / entry
        gap_pct = 100.0 * sgn * close_gap / entry

    use = mask & np.isfinite(to_open) & np.isfinite(to_plus)
    o, pl, g = to_open[use], to_plus[use], gap_pct[use]
    return {
        "n": int(use.sum()),
        "sessions": int(np.unique(sess[use]).size),
        "basis": "MEASURED",
        "to_next_open": {
            "mean_pct": _f(float(np.mean(o))) if o.size else None,
            "median_pct": _f(float(np.median(o))) if o.size else None,
            "positive_rate": _f(float(np.mean(o > 0))) if o.size else None,
        },
        f"to_next_open_plus_{NEXT_OPEN_PLUS}m": {
            "mean_pct": _f(float(np.mean(pl))) if pl.size else None,
            "median_pct": _f(float(np.median(pl))) if pl.size else None,
            "positive_rate": _f(float(np.mean(pl > 0))) if pl.size else None,
        },
        "overnight_gap": {
            "mean_pct": _f(float(np.mean(g))) if g.size else None,
            "favourable_rate": _f(float(np.mean(g > 0))) if g.size else None,
            "note": (
                "the gap is signed in the trade's favour, so a positive mean says "
                "the gap helped this side over the sample rather than that gaps "
                "are predictable"
            ),
        },
    }
