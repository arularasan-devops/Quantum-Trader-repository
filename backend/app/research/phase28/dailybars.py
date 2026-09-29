"""Phase 28 §1 — the daily series, and where each one actually came from.

Three sources are accepted, in this order of preference, and the one that was used
is recorded on every series so a reader never has to guess:

1. ``data/backtest/<INST>_ONE_DAY.jsonl`` — a native daily download. Preferred
   because it is the exchange's own daily bar, including the days an intraday feed
   dropped;
2. ``data/backtest/<INST>_ONE_MINUTE.jsonl`` — the five-year 1-minute series
   Phase 24/27 used, aggregated per IST session. Same data as those studies, so
   the swing result is comparable to the intraday one rather than to a new feed;
3. ``spot_candles`` in the research store — whatever the Phase 14 collector has
   fetched, aggregated per session if it is intraday. This is how equities enter
   the study at all, and until the collector has been run for them they are
   reported as ``INSUFFICIENT_HISTORY`` rather than studied on six weeks of data.

Rules that make the aggregation boring and correct:

* a daily bar is one IST calendar session: open of its first bar, the true high
  and low of all of them, close of its last, volume summed. Nothing is
  interpolated and no missing session is filled;
* the bar is timestamped at 15:30 IST of its own session, because that is the
  instant the decision can be taken. Timestamping it at the session's open and
  then deciding on its close is a one-day look-ahead and it is invisible in the
  output;
* a session with a single traded minute is still one bar and is counted as a thin
  session, not discarded silently;
* eligibility is a length test on the daily series itself: without four years there
  is no honest three-year development window, one validation year and one untouched
  holdout year, so the instrument is excluded and says why.
"""
from __future__ import annotations

import json
import os
import sqlite3

import numpy as np

from app.research.phase24.data import Series
from app.research.phase28 import INSUFFICIENT_HISTORY

IST_OFFSET = 19_800          # +05:30 in seconds
CLOSE_SECONDS = 15 * 3600 + 30 * 60   # 15:30 IST, the decision instant

BACKTEST_DIR = "data/backtest"
HISTORY_DB = "data/history.db"
RESEARCH_DB = "data/research.db"

# A five-year chronological split needs three development years, one validation
# year and one untouched holdout year. Below four years of sessions the split is
# not possible, so the instrument is excluded rather than reported on a shorter
# window next to instruments that have the full history.
MIN_DAILY_BARS = 1_000
MIN_YEARS = 4.0

SOURCE_NATIVE_DAILY = "NATIVE_DAILY_FILE"
SOURCE_MINUTE_AGGREGATE = "ONE_MINUTE_AGGREGATE"
SOURCE_SPOT_STORE = "SPOT_CANDLE_STORE"

# The requested universe. Indices and MCX trade as futures; the rest are cash
# equities, which is a different cost model and a long-only constraint.
INDEX = ("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY", "BANKEX")
MCX = (
    "CRUDEOIL", "NATURALGAS", "GOLD", "SILVER", "COPPER", "ZINC", "ALUMINIUM",
)
COMMODITY_EXCHANGE = "MCX"


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))


def _abs(rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(_root(), rel)


def backtest_dir() -> str:
    """Where the daily and 1-minute history files live."""
    return _abs(BACKTEST_DIR)


def _sessions(ts: np.ndarray) -> np.ndarray:
    """IST calendar day index per bar."""
    return (ts + IST_OFFSET) // 86_400


def _session_ts(day_index: np.ndarray) -> np.ndarray:
    """15:30 IST of each session, as a UTC epoch second."""
    return day_index * 86_400 - IST_OFFSET + CLOSE_SECONDS


def aggregate(series: Series) -> tuple[Series, dict]:
    """Collapse an intraday series into one bar per IST session."""
    ts = series.ts
    sess = _sessions(ts)
    n = ts.size
    if n == 0:
        raise ValueError("cannot aggregate an empty series")
    new = np.empty(n, dtype=bool)
    new[0] = True
    new[1:] = sess[1:] != sess[:-1]
    starts = np.flatnonzero(new)
    ends = np.append(starts[1:], n)
    last = ends - 1

    out = Series.__new__(Series)
    out.instrument = series.instrument
    out.ts = _session_ts(sess[starts])
    out.open = series.open[starts]
    out.close = series.close[last]
    out.high = np.maximum.reduceat(series.high, starts)
    out.low = np.minimum.reduceat(series.low, starts)
    out.volume = np.add.reduceat(series.volume, starts)

    per_session = (ends - starts).astype(np.int64)
    stats = {
        "source_bars": int(n),
        "daily_bars": int(out.ts.size),
        "median_source_bars_per_session": float(np.median(per_session)),
        "thin_sessions": int((per_session < 10).sum()),
        "aggregated": True,
    }
    return out, stats


def _already_daily(ts: np.ndarray) -> bool:
    """Whether a series has at most one bar per session already."""
    if ts.size < 2:
        return True
    return bool(np.unique(_sessions(ts)).size == ts.size)


def _normalise_daily(series: Series) -> tuple[Series, dict]:
    """Re-timestamp a native daily series to 15:30 IST of its own session."""
    out = Series.__new__(Series)
    out.instrument = series.instrument
    out.ts = _session_ts(_sessions(series.ts))
    out.open = series.open
    out.high = series.high
    out.low = series.low
    out.close = series.close
    out.volume = series.volume
    return out, {
        "source_bars": int(series.ts.size),
        "daily_bars": int(out.ts.size),
        "median_source_bars_per_session": 1.0,
        "thin_sessions": 0,
        "aggregated": False,
    }


def _rows_from_jsonl(path: str) -> list[dict]:
    """Candle rows from a JSONL file, duplicate timestamps collapsed."""
    seen: set[int] = set()
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                t = int(r["time"])
            except Exception:
                continue
            if t in seen:
                continue
            seen.add(t)
            rows.append(r)
    return rows


def _connect_ro(path: str) -> sqlite3.Connection:
    """Read-only handle that tolerates the live engine holding the store."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _rows_from_store(instrument: str) -> list[dict]:
    """Candle rows for one instrument from either store, whichever has them.

    Read-only, and a store that cannot be opened yields nothing rather than
    raising: an equity with no collected history is an eligibility answer, not an
    error, and the sqlite message is never surfaced because it can name paths.
    """
    out: list[dict] = []
    for rel, table in ((RESEARCH_DB, "spot_candles"), (HISTORY_DB, "candles")):
        path = _abs(rel)
        if not os.path.exists(path):
            continue
        try:
            conn = _connect_ro(path)
        except sqlite3.Error:
            continue
        try:
            cur = conn.execute(
                f"SELECT ts, open, high, low, close, volume FROM {table} "
                "WHERE instrument = ? ORDER BY ts",
                (instrument.upper(),),
            )
            rows = cur.fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            conn.close()
        if not rows:
            continue
        for ts, o, h, low, c, v in rows:
            if o is None or h is None or low is None or c is None:
                continue
            t = int(ts)
            # Some stores keep milliseconds. Normalise to seconds so sessions are
            # not all collapsed into 1970.
            if t > 20_000_000_000:
                t //= 1000
            out.append({
                "time": t, "open": o, "high": h, "low": low, "close": c,
                "volume": v or 0.0,
            })
        if out:
            return out
    return out


def _from_file(instrument: str, suffix: str) -> Series | None:
    path = _abs(os.path.join(BACKTEST_DIR, f"{instrument.upper()}_{suffix}.jsonl"))
    if not os.path.exists(path):
        return None
    rows = _rows_from_jsonl(path)
    return Series(instrument.upper(), rows) if rows else None


def load(instrument: str) -> tuple[Series, dict] | None:
    """The instrument's daily series and its provenance, or ``None``.

    Preference order is stated in the module docstring and is deliberate: a native
    daily file beats an aggregate of the same period because it includes sessions
    an intraday feed missed.
    """
    inst = instrument.upper()
    native = _from_file(inst, "ONE_DAY")
    if native is not None and len(native) > 0:
        series, stats = _normalise_daily(native)
        stats["source"] = SOURCE_NATIVE_DAILY
        return _finish(series, stats)

    minute = _from_file(inst, "ONE_MINUTE")
    if minute is not None and len(minute) > 0:
        series, stats = aggregate(minute)
        stats["source"] = SOURCE_MINUTE_AGGREGATE
        return _finish(series, stats)

    rows = _rows_from_store(inst)
    if rows:
        raw = Series(inst, rows)
        if _already_daily(raw.ts):
            series, stats = _normalise_daily(raw)
        else:
            series, stats = aggregate(raw)
        stats["source"] = SOURCE_SPOT_STORE
        return _finish(series, stats)
    return None


def _finish(series: Series, stats: dict) -> tuple[Series, dict]:
    """Attach the span facts every eligibility decision is made on."""
    ts = series.ts
    span_days = (int(ts[-1]) - int(ts[0])) / 86_400.0 if ts.size > 1 else 0.0
    stats = dict(stats)
    stats.update({
        "instrument": series.instrument,
        "daily_bars": int(ts.size),
        "first_ts": int(ts[0]) if ts.size else 0,
        "last_ts": int(ts[-1]) if ts.size else 0,
        "span_years": round(span_days / 365.25, 2),
        "spans_a_session": False,
        "note": (
            "one bar per IST session, timestamped 15:30 IST of its own session; "
            "nothing interpolated and no missing session filled"
        ),
    })
    return series, stats


def eligible(stats: dict) -> tuple[bool, str]:
    """Whether a daily series can carry a chronological five-year split."""
    bars_ = int(stats.get("daily_bars") or 0)
    years = float(stats.get("span_years") or 0.0)
    if bars_ < MIN_DAILY_BARS:
        return False, (
            f"{bars_} daily bars, below the {MIN_DAILY_BARS} a three-year "
            "development window plus a validation year plus an untouched holdout "
            "year needs"
        )
    if years < MIN_YEARS:
        return False, (
            f"history spans {years} years, below the {MIN_YEARS} needed for a "
            "chronological split"
        )
    return True, ""


def universe() -> tuple[str, ...]:
    """Every instrument that could be asked about, registry order preserved."""
    from app.market.instruments import REGISTRY
    return tuple(sorted(REGISTRY.keys()))


def is_equity(instrument: str) -> bool:
    """Cash equity rather than an index or a commodity.

    The registry's exchange decides it, not a name list: a commodity mis-typed as
    cash equity would be priced with STT and a delivery charge it never pays, and
    would be allowed a long-only constraint it does not have.
    """
    from app.market.instruments import REGISTRY

    inst = instrument.upper()
    if inst in INDEX:
        return False
    spec = REGISTRY.get(inst)
    if spec is not None:
        return str(spec.exchange).upper() != COMMODITY_EXCHANGE
    return inst not in MCX


def coverage(instruments: tuple[str, ...] | None = None) -> dict:
    """What Phase 28 can study, what it cannot, and why — before anything runs."""
    names = instruments or universe()
    usable: list[dict] = []
    excluded: list[dict] = []
    for inst in names:
        got = load(inst)
        if got is None:
            excluded.append({
                "instrument": inst,
                "status": INSUFFICIENT_HISTORY,
                "reason": (
                    "no daily file, no five-year 1-minute file and nothing in the "
                    "collected spot store; run "
                    "`python -m app.research.phase28.cli collect` to fetch it"
                ),
            })
            continue
        _series, stats = got
        ok, why = eligible(stats)
        row = {
            **stats,
            "vehicle": "EQUITY_DELIVERY" if is_equity(inst) else "FUTURES",
        }
        if ok:
            usable.append(row)
        else:
            excluded.append({**row, "status": INSUFFICIENT_HISTORY, "reason": why})
    return {
        "requested": list(names),
        "usable": sorted(usable, key=lambda r: -int(r.get("daily_bars") or 0)),
        "excluded": sorted(excluded, key=lambda r: str(r.get("instrument"))),
        "min_daily_bars": MIN_DAILY_BARS,
        "min_years": MIN_YEARS,
        "note": (
            "eligibility is a length test on the daily series itself. An "
            "instrument with only live-captured weeks is excluded from the "
            "evidence rather than counted as a failed strategy"
        ),
    }


def rules() -> dict:
    """The aggregation contract, carried in the artefact rather than assumed."""
    return {
        "source_preference": [
            SOURCE_NATIVE_DAILY, SOURCE_MINUTE_AGGREGATE, SOURCE_SPOT_STORE
        ],
        "bar": (
            "one IST session: open of its first source bar, max high, min low, "
            "close of its last, volume summed"
        ),
        "timestamp": (
            "15:30 IST of the session the bar describes, because that is the "
            "instant the decision can be taken; timestamping at the open and "
            "deciding on the close would be a one-day look-ahead"
        ),
        "interpolation": "none; a missing session is missing, not filled",
        "thin_sessions": "counted and reported, never silently dropped",
        "eligibility": {
            "min_daily_bars": MIN_DAILY_BARS,
            "min_years": MIN_YEARS,
            "on_failure": INSUFFICIENT_HISTORY,
        },
        "vehicle_split": {
            "futures": (
                "indices and anything on the MCX, taken from the registry's own "
                "exchange field rather than a hand-kept name list"
            ),
            "equity_delivery": "everything else in the registry",
            "resolved_now": universe_by_vehicle(),
            "why": (
                "futures carry rollover and contract economics, cash delivery "
                "carries STT and depository charges and cannot be held short "
                "overnight; pooling them would average two different cost models"
            ),
        },
    }


def universe_by_vehicle() -> dict[str, tuple[str, ...]]:
    """The registry split the way the study treats it."""
    names = universe()
    return {
        "FUTURES": tuple(n for n in names if not is_equity(n)),
        "EQUITY_DELIVERY": tuple(n for n in names if is_equity(n)),
    }
