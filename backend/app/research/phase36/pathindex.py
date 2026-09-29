"""Phase 36 — one read per contract-session, and horizon lookups by index.

Why this exists. A triple resolves three vehicles, and every vehicle walks its
contract's forward quotes to answer 12 horizons plus the close. Done per leg
that is quadratic in the size of a session twice over: the same session rows are
re-read from SQLite once per leg, and then re-walked once per leg. On a CRUDEOIL
session with ~13,700 eligible instants that is ~41,000 legs against ~14,000
quotes each, and the run stops finishing rather than being slow.

Two changes, both speed only:

* each contract-session is read **once** and kept as arrays, with the exit fill
  precomputed — the same thing :class:`app.research.phase35.path.ForwardCache`
  does for the Phase 35 rebuild;
* the per-leg walk is replaced by **index lookups**. A horizon is a binary search
  for the first quote at or after that many minutes, and the running MFE/MAE at
  that point is a range extreme over the entry-to-horizon window, answered in
  constant time from a sparse table built once per contract-session.

Nothing about the *measurement* changes. No quote is interpolated, no gap is
filled, no horizon without a quote is answered, and a path still never leaves
its own session. The extremes are taken over exactly the quotes the walk visited
— the ones with an executable exit fill, strictly after the entry instant.

One definition is stated rather than left to arithmetic: the peak (and the
trough) is the **first quote at the best price**. The walk previously compared
percentages rounded to 4dp, so two different prices that round to the same
percent made "first" depend on rounding. Comparing the price itself is the same
answer wherever the two differ at all, and it is the same answer in both
implementations here, which is what lets the smoke assert they agree row for row.
"""
from __future__ import annotations

import bisect

from app.research.phase35 import MEASURED_EXECUTABLE, book, path

# What both implementations return, so they can be compared directly:
#
#   forward         quotes after the entry instant, priced or not (the row's
#                   ``samples`` count, kept as-is for continuity)
#   priced          how many of those carried an executable exit price
#   executable_all  False if any priced quote was a traded-price exit
#   points          horizon key -> (price, best_price_so_far, worst_price_so_far, ts)
#   close           the same tuple at the last quote of the session
#   best / worst    (price, ts) of the first best/worst quote of the whole path
#
# Prices, not percentages: the caller owns the entry price and the sign, so it
# owns the conversion. That keeps this module free of the cost model.


def _empty(entry_ts: float, forward: int) -> dict:
    return {
        "forward": forward,
        "priced": 0,
        "executable_all": True,
        "points": {},
        "close": (None, None, None, entry_ts),
        "best": (None, entry_ts),
        "worst": (None, entry_ts),
    }


class _Extreme:
    """Range argmax over a fixed list, earliest index winning a tie.

    A sparse table rather than a segment tree because the values never change
    once the session is read, and every query is a range: O(n log n) once per
    contract-session, O(1) per horizon.
    """

    def __init__(self, vals: list[float], *, largest: bool) -> None:
        self._vals = vals
        self._largest = largest
        n = len(vals)
        self._tab: list[list[int]] = [list(range(n))]
        width = 1
        while width * 2 <= n:
            prev = self._tab[-1]
            self._tab.append([
                self._pick(prev[i], prev[i + width])
                for i in range(n - width * 2 + 1)
            ])
            width *= 2

    def _pick(self, a: int, b: int) -> int:
        va, vb = self._vals[a], self._vals[b]
        if va == vb:
            return a if a <= b else b
        better = va > vb if self._largest else va < vb
        return a if better else b

    def query(self, lo: int, hi: int) -> int:
        """Index of the first best value in ``[lo, hi]`` inclusive."""
        level = (hi - lo + 1).bit_length() - 1
        row = self._tab[level]
        return self._pick(row[lo], row[hi - (1 << level) + 1])


class _Contract:
    """One contract-session: rows, priced arrays, and range extremes."""

    def __init__(self, rows: list[dict], *, sign: float) -> None:
        self.rows = rows
        self.sign = sign
        self.ts = [float(r["ts"]) for r in rows]
        self.pts: list[float] = []
        self.ppx: list[float] = []
        self._signed: list[float] = []
        # Prefix count of priced quotes whose fill was *not* executable, so
        # "was every exit on this stretch a real bid/ask fill" is a subtraction.
        self.nonexec: list[int] = [0]
        self._exec_ts: list[float] = []
        self._exec_signed: list[float] = []
        self._exec_rows: list[dict] = []
        for r in rows:
            fill = r["fill"]
            px = fill["price"]
            if px is None:
                continue
            px = float(px)
            executable = fill["evidence"] == MEASURED_EXECUTABLE
            self.pts.append(float(r["ts"]))
            self.ppx.append(px)
            self._signed.append(sign * px)
            self.nonexec.append(self.nonexec[-1] + (0 if executable else 1))
            if executable:
                self._exec_ts.append(float(r["ts"]))
                self._exec_signed.append(sign * px)
                self._exec_rows.append(r)
        self._max: _Extreme | None = None
        self._min: _Extreme | None = None
        self._exec_max: _Extreme | None = None

    # -- lookups used to pick a delayed entry quote -------------------------
    def count_after(self, after_ts: float) -> int:
        return len(self.ts) - bisect.bisect_right(self.ts, after_ts)

    def rows_after(self, after_ts: float) -> list[dict]:
        return self.rows[bisect.bisect_right(self.ts, after_ts):]

    def first_after_minutes(
        self, after_ts: float, *, minutes: float,
    ) -> dict | None:
        """First quote at least ``minutes`` after the entry instant.

        Not a quote interpolated to that minute — that minute may never have
        been quoted. The boundary is settled on elapsed minutes, the same
        expression the walk used, rather than on the equivalent timestamp
        arithmetic, so the two cannot disagree in their last bits.
        """
        lo = bisect.bisect_right(self.ts, after_ts)
        n = len(self.ts)
        i = max(lo, bisect.bisect_left(self.ts, after_ts + minutes * 60.0))
        while i > lo and (self.ts[i - 1] - after_ts) / 60.0 >= minutes:
            i -= 1
        while i < n and (self.ts[i] - after_ts) / 60.0 < minutes:
            i += 1
        return self.rows[i] if i < n else None

    def first_above(self, after_ts: float, *, price: float) -> dict | None:
        """First executable quote strictly after ``after_ts`` above ``price``.

        Used by the confirmation entry, which wants the first later instant the
        leg was already showing a gross profit. Searched over the executable
        quotes only — a traded-price print is not a confirmation you could have
        acted on — and found by descending the sparse table instead of walking,
        because a leg that never goes green would otherwise walk its whole
        session and there are tens of thousands of legs.
        """
        if not self._exec_signed:
            return None
        if self._exec_max is None:
            self._exec_max = _Extreme(self._exec_signed, largest=True)
        lo = bisect.bisect_right(self._exec_ts, after_ts)
        hi = len(self._exec_signed) - 1
        if lo > hi:
            return None
        threshold = self.sign * price
        if self._exec_signed[self._exec_max.query(lo, hi)] <= threshold:
            return None
        # Leftmost index whose value exceeds the threshold: halve the window,
        # keeping the left half whenever it already contains such a value.
        while lo < hi:
            mid = (lo + hi) // 2
            if self._exec_signed[self._exec_max.query(lo, mid)] > threshold:
                hi = mid
            else:
                lo = mid + 1
        return self._exec_rows[lo]

    # -- the horizon table -------------------------------------------------
    def walk(self, *, entry_ts: float, horizons: tuple[float, ...]) -> dict:
        forward = self.count_after(entry_ts)
        start = bisect.bisect_right(self.pts, entry_ts)
        n = len(self.pts)
        if start >= n:
            out = _empty(entry_ts, forward)
            out["executable_all"] = True
            return out
        if self._max is None:
            self._max = _Extreme(self._signed, largest=True)
            self._min = _Extreme(self._signed, largest=False)
        assert self._min is not None  # noqa: S101 - built together, above

        points: dict[str, tuple] = {}
        j = start
        for h in horizons:
            j = max(j, bisect.bisect_left(self.pts, entry_ts + h * 60.0, j))
            # The walk compared elapsed minutes, so the boundary is settled the
            # same way here rather than trusting the two float expressions to
            # agree in their last bits.
            while j > start and (self.pts[j - 1] - entry_ts) / 60.0 >= h:
                j -= 1
            while j < n and (self.pts[j] - entry_ts) / 60.0 < h:
                j += 1
            if j >= n:
                # Horizons are ascending, so no later one is reachable either.
                break
            best = self._max.query(start, j)
            worst = self._min.query(start, j)
            points[str(h)] = (self.ppx[j], self.ppx[best], self.ppx[worst],
                              self.pts[j])

        best = self._max.query(start, n - 1)
        worst = self._min.query(start, n - 1)
        return {
            "forward": forward,
            "priced": n - start,
            "executable_all": self.nonexec[n] == self.nonexec[start],
            "points": points,
            "close": (self.ppx[n - 1], self.ppx[best], self.ppx[worst],
                      self.pts[n - 1]),
            "best": (self.ppx[best], self.pts[best]),
            "worst": (self.ppx[worst], self.pts[worst]),
        }


class PathIndex:
    """Contract-sessions for one pass, keyed by contract, side and session.

    The exit side is part of the key because it is part of the fill: a long and
    a short futures leg on the same contract exit on opposite sides of the book.
    Only one session is held at a time — a path never crosses a session boundary
    and triples are resolved in time order, so an earlier day cannot be needed
    again and keeping it would grow the cache to the size of the store.
    """

    def __init__(self) -> None:
        self._contracts: dict[tuple, _Contract] = {}
        self._day: str | None = None

    def contract(
        self, con, *, symbol: str | None, vehicle: str, direction: str,
        at_ts: float,
    ) -> _Contract | None:
        if not symbol:
            return None
        at = float(at_ts)
        day = path.session_date(at)
        if day != self._day:
            self._contracts.clear()
            self._day = day
        key = (symbol, vehicle, direction, day)
        found = self._contracts.get(key)
        if found is None:
            start, end = path.session_bounds(at)
            rows = []
            for r in con.execute(
                "SELECT ts, bid, ask, traded, evidence FROM raw_quote "
                "WHERE symbol = ? AND vehicle = ? AND ts >= ? AND ts < ? "
                "ORDER BY ts",
                (symbol, vehicle, start, end),
            ).fetchall():
                row = dict(r)
                row["fill"] = book.exit_fill(
                    row, vehicle=vehicle, direction=direction,
                )
                rows.append(row)
            found = _Contract(rows, sign=book.gross_sign(vehicle, direction))
            self._contracts[key] = found
        return found


def walk_scan(
    samples: list[dict], *, entry_ts: float, sign: float,
    horizons: tuple[float, ...],
) -> dict:
    """The same walk, one quote at a time — the reference implementation.

    Kept because it is the readable statement of what a horizon means, and
    because the smoke compares it against the indexed answer row for row on
    generated paths. It is not used on a real store: it is the cost this module
    exists to remove.
    """
    priced = 0
    executable_all = True
    best_px: float | None = None
    worst_px: float | None = None
    best_ts = worst_ts = entry_ts
    last_px: float | None = None
    last_ts = entry_ts
    pending = list(horizons)
    points: dict[str, tuple] = {}
    for s in samples:
        fill = s["fill"]
        px = fill["price"]
        if px is None:
            continue
        px = float(px)
        priced += 1
        if fill["evidence"] != MEASURED_EXECUTABLE:
            executable_all = False
        ts = float(s["ts"])
        if best_px is None or sign * px > sign * best_px:
            best_px, best_ts = px, ts
        if worst_px is None or sign * px < sign * worst_px:
            worst_px, worst_ts = px, ts
        last_px, last_ts = px, ts
        elapsed = (ts - entry_ts) / 60.0
        while pending and elapsed >= pending[0]:
            points[str(pending.pop(0))] = (px, best_px, worst_px, ts)
    if not priced:
        return _empty(entry_ts, len(samples))
    return {
        "forward": len(samples),
        "priced": priced,
        "executable_all": executable_all,
        "points": points,
        "close": (last_px, best_px, worst_px, last_ts),
        "best": (best_px, best_ts),
        "worst": (worst_px, worst_ts),
    }
