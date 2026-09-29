"""Timeframe bars for the opportunity screen, and why the screen needs them.

The first real cycle rejected 74 of 82 screenable candidates for a negative net
in training, and the decomposition of those rejections is the reason this module
exists: 47 of the 82 had a **positive gross** edge, but the best gross edge on a
usable sample was +0.0194% per trade against a modelled round trip of 0.0600%.
The cost was not shaving those candidates. It was three times everything they
earned.

That is an arithmetic problem, and it has an arithmetic shape. The round trip is
charged **per trade**, not per minute held — it is the same 0.06% whether the
position lives for three minutes or three sessions. So the ratio of edge to cost
cannot be improved by entering better; a 38% hit rate at 1.5:1 says there is no
direction there to sharpen. It can only be improved by the move being bigger,
which means the bar being bigger.

Hence the timeframes. Nothing else about the screen changes: same mechanisms,
same pre-registered kill rules, same chronological split, same false-discovery
correction over the whole family, same modelled cost. One variable moves.

Three properties of the aggregation matter enough to state here:

* **It is not a second data source.** Every bar is built from the same stored
  one-minute series by :mod:`app.research.phase27.bars`, which anchors buckets
  per session, labels each bar with its *closing* minute so a decision on an
  aggregated close is not a look-ahead, and takes the true extremes of the
  constituent minutes. Reusing it rather than writing a second resampler is
  deliberate: two resamplers eventually disagree, and the one that disagrees
  quietly is the one in the newer file.
* **A wider bar is a wider ambiguity, not a looser one.** A bar containing both
  the target and the stop is still scored as the stop. At one minute that
  convention costs a little; at a daily bar it costs a lot, and it stays,
  because the alternative is a screen whose returns come from a modelling
  choice.
* **Daily is a session, not 1440 traded minutes.** :data:`DAILY` buckets by
  minutes-into-session, and no session runs 24 hours, so every session collapses
  to exactly one bar. The resampler's ``short_bars`` count therefore reads 100%
  at this timeframe: that is the yardstick being 1440 minutes long, not a fault
  in the data, and :func:`resample_stats` says so on the row.
"""
from __future__ import annotations

from app.research.phase24 import data as p24data
from app.research.phase27 import bars as p27bars

# One minute is included so the timeframe comparison has a controlled base
# member generated under the identical rule, rather than being read across from
# the existing one-minute candidates, whose time stop is a different length.
TIMEFRAMES: tuple[int, ...] = (1, 5, 15, 60, 1440)

DAILY = 1440

_SERIES: dict[tuple[str, int], object] = {}
_STATS: dict[tuple[str, int], dict] = {}


def label(timeframe: int) -> str:
    """A short human label: ``1m``, ``15m``, ``1d``."""
    tf = int(timeframe)
    return "1d" if tf == DAILY else f"{tf}m"


def clear_cache() -> None:
    _SERIES.clear()
    _STATS.clear()


def series_at(instrument: str, timeframe: int = 1):
    """The instrument's series at ``timeframe``, or ``None`` if absent.

    ``None`` means there is nothing on disk, which is an absence and not a
    negative result. One minute is the stored series itself — never a
    round-trip through the resampler, so the base member of a timeframe
    comparison is bit-for-bit the series every earlier phase measured.
    """
    tf = int(timeframe)
    if tf not in TIMEFRAMES:
        raise ValueError(f"undeclared timeframe: {tf}")
    key = (str(instrument), tf)
    if key in _SERIES:
        return _SERIES[key]
    base = p24data.load_series(str(instrument))
    if base is None or len(base) == 0:
        _SERIES[key] = None
        return None
    if tf == 1:
        _SERIES[key] = base
        _STATS[key] = {
            "timeframe_minutes": 1,
            "bars": int(len(base)),
            "source": "THE_STORED_ONE_MINUTE_SERIES_UNAGGREGATED",
        }
        return base
    out, st = p27bars.resample(base, tf)
    if tf == DAILY:
        st = dict(st, short_bar_note=(
            "every bar reads short because the yardstick is 1440 minutes and "
            "no session is that long; one bar is one session"
        ))
    _SERIES[key] = out
    _STATS[key] = st
    return out


def resample_stats(instrument: str, timeframe: int) -> dict | None:
    """The counts behind a resampled series, loading it if needed."""
    key = (str(instrument), int(timeframe))
    if key not in _STATS:
        series_at(instrument, timeframe)
    return _STATS.get(key)


def coverage() -> dict:
    """Which instruments carry which timeframes, measured rather than assumed."""
    screenable = p24data.coverage()
    rows: list[dict] = []
    absent: list[dict] = []
    for row in screenable.get("usable", []):
        inst = row["instrument"]
        for tf in TIMEFRAMES:
            s = series_at(inst, tf)
            if s is None:
                absent.append({"instrument": inst, "timeframe": label(tf),
                               "absence": "NO_HISTORY"})
                continue
            rows.append({"instrument": inst, "timeframe": label(tf),
                         "timeframe_minutes": tf, "bars": int(len(s))})
    return {
        "timeframes": [label(t) for t in TIMEFRAMES],
        "series": rows,
        "absent": absent,
        "source": "ONE_MINUTE_OHLCV_AGGREGATED_PER_SESSION_LABELLED_ON_THE_CLOSE",
    }
