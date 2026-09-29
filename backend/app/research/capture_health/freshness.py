"""Was a session's captured book live, or the same closed-market book repeated?

Read-only, like the rest of this package: it opens the observation journal the
capture already wrote and returns dictionaries.

The question this answers came from a real row set. 2026-09-12 is a Saturday —
both exchanges shut — and the journal holds thousands of observations for it.
Two very different things produce that, and a row count cannot tell them apart:

* the provider served its **last close** on every poll, so the capture wrote
  the same frozen bid/ask over and over. Those rows are real records of what
  the feed said, and they are NOT executable prices at those timestamps;
* the timestamps are wrong, and the rows belong to a trading day.

So the discriminator is movement, not volume: for each symbol, how many
distinct ``(bid, ask, premium)`` triples appeared across the day. A live book
moves. A book that never moved across hundreds of polls was not being quoted.

Nothing here excludes, deletes or rewrites a row. A frozen weekend book is
evidence about the feed, and the fix is a decision about admission, not a
silent edit to raw evidence — which is the one thing this project never does.
"""
from __future__ import annotations

import json
import os

from app.research.capture_health import coverage
from app.research.phase17 import store as p17store

# Per-symbol distinct-quote counts below this, over at least MIN_POLLS polls,
# mean the book did not move. One distinct triple is frozen outright; the
# threshold is 1 rather than a ratio because a partially moving book is a
# different finding and must not be absorbed into this one.
FROZEN_DISTINCT = 1
MIN_POLLS = 20

FROZEN = "THE_BOOK_NEVER_MOVED_THE_FEED_REPLAYED_ONE_QUOTE_NOT_EXECUTABLE"
LIVE = "THE_BOOK_MOVED_ACROSS_POLLS_CONSISTENT_WITH_A_QUOTING_MARKET"
TOO_FEW = "TOO_FEW_POLLS_PER_SYMBOL_TO_SAY_WHETHER_THE_BOOK_MOVED"
NO_ROWS = "NO_OBSERVATION_ROW_FOR_THIS_SESSION"

# Both at once, and the label exists because the first version of this module
# could not say it. A verdict taken per instrument reads LIVE as soon as one of
# its symbols moves, which on 2026-09-15 hid a frozen strike inside eight
# otherwise-live instruments — and a frozen strike is precisely what a paper
# entry would have been priced off. The decision is per symbol; the instrument
# only summarises it.
MIXED = "SOME_SYMBOLS_MOVED_AND_SOME_NEVER_MOVED_SEE_THE_FROZEN_SYMBOL_LIST"

# A day can be closed and still hold rows; the two facts are reported side by
# side rather than one overriding the other.
CLOSED = "SATURDAY_OR_SUNDAY_THE_EXCHANGE_WAS_SHUT"
OPEN = "A_WEEKDAY_NO_HOLIDAY_LIST_IS_CONSULTED"


def _blank() -> dict:
    return {
        "rows": 0,
        "symbols": {},
        "data_quality": {},
        "source": {},
        "book_age_ms_absent": 0,
        "book_age_ms_max": None,
        "first_ts": None,
        "last_ts": None,
    }


def _note(bucket: dict, row: dict, ts: float) -> None:
    quote = row.get("selected") or {}
    bucket["rows"] += 1
    symbol = str(quote.get("symbol") or "UNKNOWN_SYMBOL")
    seen = bucket["symbols"].setdefault(
        symbol, {"polls": 0, "quotes": set(), "first_ts": ts, "last_ts": ts})
    seen["polls"] += 1
    seen["quotes"].add((quote.get("bid"), quote.get("ask"),
                        quote.get("premium")))
    seen["first_ts"] = min(seen["first_ts"], ts)
    seen["last_ts"] = max(seen["last_ts"], ts)

    dq = str(quote.get("data_quality") or "ABSENT")
    bucket["data_quality"][dq] = bucket["data_quality"].get(dq, 0) + 1
    src = str(quote.get("source") or "ABSENT")
    bucket["source"][src] = bucket["source"].get(src, 0) + 1

    age = quote.get("book_age_ms")
    if isinstance(age, (int, float)):
        top = bucket["book_age_ms_max"]
        bucket["book_age_ms_max"] = age if top is None else max(top, age)
    else:
        bucket["book_age_ms_absent"] += 1

    if bucket["first_ts"] is None or ts < bucket["first_ts"]:
        bucket["first_ts"] = ts
    if bucket["last_ts"] is None or ts > bucket["last_ts"]:
        bucket["last_ts"] = ts


def _symbol_verdict(seen: dict) -> str:
    """One label per SYMBOL. This is where the decision actually belongs."""
    if seen["polls"] < MIN_POLLS:
        return TOO_FEW
    return FROZEN if len(seen["quotes"]) <= FROZEN_DISTINCT else LIVE


def _verdict(labels: list[str]) -> str:
    """An instrument's summary of its own symbols, which may be both."""
    decided = [label for label in labels if label != TOO_FEW]
    if not decided:
        return TOO_FEW
    if FROZEN in decided and LIVE in decided:
        return MIXED
    return FROZEN if FROZEN in decided else LIVE


def audit(session: str, *, paths: list[str] | None = None) -> dict:
    """What the feed was doing on one session, per instrument.

    Only lines whose own ``signal_ts`` falls on ``session`` in IST are parsed,
    so the cost is one timestamp regex per line over the series and a full
    parse only for the day asked about.
    """
    files = (
        [p for p in p17store.series_paths(p17store.OBSERVATIONS)
         if os.path.exists(p)]
        if paths is None else list(paths)
    )
    per_instrument: dict[str, dict] = {}
    matched = 0
    unparsed = 0

    for path in files:
        try:
            handle = p17store.open_series(path)
        except OSError:
            continue
        with handle as fh:
            for line in fh:
                found = coverage.TS_RE.search(line)
                if found is None:
                    continue
                ts = float(found.group(1))
                if coverage.session_of(ts) != session:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    unparsed += 1
                    continue
                matched += 1
                name = str(row.get("instrument") or "UNKNOWN_INSTRUMENT")
                _note(per_instrument.setdefault(name, _blank()), row, ts)

    instruments = []
    frozen_total = 0
    duplicate_total = 0
    for name in sorted(per_instrument):
        bucket = per_instrument[name]
        symbols = bucket.pop("symbols")
        labels = {sym: _symbol_verdict(seen) for sym, seen in symbols.items()}
        frozen_symbols = [
            {
                "symbol": sym,
                "polls": symbols[sym]["polls"],
                "distinct_quotes": len(symbols[sym]["quotes"]),
                "first_seen_ist": coverage.ist(
                    symbols[sym]["first_ts"]).strftime("%H:%M"),
                "last_seen_ist": coverage.ist(
                    symbols[sym]["last_ts"]).strftime("%H:%M"),
            }
            for sym in sorted(symbols)
            if labels[sym] == FROZEN
        ]
        frozen_total += len(frozen_symbols)
        # Rows that re-recorded a quote already on disk. Not a fault in itself,
        # and the reason the journal is tens of GB: the same book written again.
        duplicates = sum(
            seen["polls"] - len(seen["quotes"]) for seen in symbols.values())
        duplicate_total += duplicates
        instruments.append({
            "instrument": name,
            "rows": bucket["rows"],
            "symbols": len(symbols),
            "distinct_quotes_max": max(
                (len(s["quotes"]) for s in symbols.values()), default=0),
            "distinct_quotes_min": min(
                (len(s["quotes"]) for s in symbols.values()), default=0),
            "polls_max": max((s["polls"] for s in symbols.values()), default=0),
            "data_quality": bucket["data_quality"],
            "source": bucket["source"],
            "book_age_ms_absent": bucket["book_age_ms_absent"],
            "book_age_ms_max": bucket["book_age_ms_max"],
            "first_seen_ist": (
                coverage.ist(bucket["first_ts"]).strftime("%H:%M")
                if bucket["first_ts"] is not None else None),
            "last_seen_ist": (
                coverage.ist(bucket["last_ts"]).strftime("%H:%M")
                if bucket["last_ts"] is not None else None),
            "frozen_symbols": frozen_symbols,
            "duplicate_rows": duplicates,
            "verdict": _verdict(list(labels.values())),
        })

    verdicts = {row["verdict"] for row in instruments}
    return {
        "phase": "CAPTURE_HEALTH_FEED_FRESHNESS",
        "session": session,
        "calendar": CLOSED if coverage.is_weekend(session) else OPEN,
        "rows_examined": matched,
        "rows_unparsable": unparsed,
        "instruments": instruments,
        "frozen_symbols": frozen_total,
        "duplicate_rows": duplicate_total,
        "verdict": (
            NO_ROWS if not instruments
            else FROZEN if verdicts == {FROZEN}
            else MIXED if frozen_total
            else LIVE if LIVE in verdicts
            else TOO_FEW
        ),
        "note": (
            "Movement of the recorded bid/ask across polls, per symbol. A book "
            "that never moved was replayed by the provider, so those rows are "
            "evidence of what the feed said and not executable prices. No row "
            "is excluded or altered by this measurement."
        ),
        "research_only": True,
        "paper_only": True,
    }


def render(state: dict) -> str:
    lines = [
        f"FEED FRESHNESS {state['session']} — {state['verdict']}",
        f"  calendar: {state['calendar']}  rows examined: "
        f"{state['rows_examined']}",
    ]
    for row in state["instruments"]:
        lines.append(
            f"  {row['instrument']}: {row['rows']} rows over "
            f"{row['symbols']} symbols, {row['first_seen_ist']}–"
            f"{row['last_seen_ist']} IST"
        )
        lines.append(
            f"    distinct quotes per symbol {row['distinct_quotes_min']}–"
            f"{row['distinct_quotes_max']} over up to {row['polls_max']} "
            f"polls, {row['duplicate_rows']} rows re-recorded an unchanged "
            f"book — {row['verdict']}"
        )
        for frozen in row["frozen_symbols"]:
            lines.append(
                f"    FROZEN {frozen['symbol']}: one quote across "
                f"{frozen['polls']} polls, {frozen['first_seen_ist']}–"
                f"{frozen['last_seen_ist']} IST — not an executable price"
            )
    if state["instruments"]:
        lines.append(
            f"  {state['frozen_symbols']} frozen symbol(s) in total; "
            f"{state['duplicate_rows']} of {state['rows_examined']} rows "
            f"re-recorded a book already on disk"
        )
    if state["rows_unparsable"]:
        lines.append(
            f"  {state['rows_unparsable']} lines could not be parsed and are "
            f"reported, not skipped silently"
        )
    lines.append("  " + state["note"])
    return "\n".join(lines)
