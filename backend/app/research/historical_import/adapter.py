"""§9 — the adapter: imported bars presented as the series research already reads.

The existing candle studies take a :class:`app.research.phase24.data.Series`:
parallel numpy arrays of time/open/high/low/close/volume. So that is what this
returns. No study definition changes, no threshold moves, and nothing in Phase
41/42/43/44 is touched — a study that ran on the repository's five-year NIFTY
file runs unmodified on an imported BANKNIFTY one.

Three rules the adapter enforces so an imported dataset cannot quietly become
something it is not:

* **the repository's own five-year files always win.** If an instrument has a
  shipped series, an import never shadows it. Otherwise a small, clean-looking
  export could silently replace the series every earlier result was measured
  on, and no report would show the substitution;
* **a rejected dataset is never served.** Eligibility is read from the registry,
  not re-derived here on friendlier terms;
* **volume absent stays absent.** ``Series`` defaults a missing volume to zero,
  which is correct for its arrays but wrong as a fact, so the adapter reports
  ``volume_present`` alongside and any study quoting volume on a dataset
  without it is quoting the default, not the market.

What the adapter cannot give a study is a fill. There is no bid, ask or spread
in a candle, so :func:`execution_status` answers
:data:`EXECUTION_UNMEASURED` for every dataset whose source had no two-sided
quote, and that label is what belongs in the study's own output.
"""
from __future__ import annotations

from app.research.historical_import import (
    BID_ASK_PRESENT,
    ELIGIBLE_NONE,
    EXECUTION_UNMEASURED,
    HISTORICAL_CANDLE_DATA,
)
from app.research.historical_import import registry, store
from app.research.phase24 import data as p24data


def shipped_instruments() -> set[str]:
    """Instruments with a repository five-year series, which imports never shadow."""
    return {k.upper() for k in p24data.BACKTEST_FILES}


def datasets_for(instrument: str, *, timeframe: int = 1,
                 include_ineligible: bool = False) -> list[dict]:
    rows = [
        r for r in registry.datasets()
        if (r.get("instrument") or "").upper() == instrument.upper()
        and int(r.get("timeframe") or 0) == int(timeframe)
        and r.get("dataset_id")
    ]
    if not include_ineligible:
        rows = [r for r in rows
                if r.get("research_eligibility") not in (None, ELIGIBLE_NONE)]
    # Longest first: given two imports of the same instrument and timeframe,
    # the one covering more sessions is the one a chronological split needs.
    return sorted(rows, key=lambda r: (int(r.get("sessions") or 0),
                                       int(r.get("rows") or 0)), reverse=True)


def best_dataset(instrument: str, *, timeframe: int = 1) -> dict | None:
    rows = datasets_for(instrument, timeframe=timeframe)
    return rows[0] if rows else None


def load_series(instrument: str, *, timeframe: int = 1,
                dataset_id: str | None = None) -> p24data.Series | None:
    """An imported dataset as a ``Series``, or ``None`` when there isn't one."""
    if dataset_id:
        rec = registry.get(dataset_id)
        if not rec or rec.get("research_eligibility") == ELIGIBLE_NONE:
            return None
    else:
        rec = best_dataset(instrument, timeframe=timeframe)
        if not rec:
            return None
        dataset_id = rec["dataset_id"]
    bars = store.read_bars(dataset_id)
    if not bars:
        return None
    return p24data.Series((rec.get("instrument") or instrument).upper(), bars)


def series_source(instrument: str, *, timeframe: int = 1) -> dict:
    """Which file a study would be reading, and under what caveats."""
    if instrument.upper() in shipped_instruments() and int(timeframe) == 1:
        return {
            "instrument": instrument.upper(),
            "origin": "REPOSITORY_FIVE_YEAR_SERIES",
            "dataset_id": None,
            "classification": HISTORICAL_CANDLE_DATA,
            "note": "an import never shadows a shipped series",
        }
    rec = best_dataset(instrument, timeframe=timeframe)
    if not rec:
        return {"instrument": instrument.upper(), "origin": None,
                "dataset_id": None, "note": "no series for this instrument"}
    bars = store.read_bars(rec["dataset_id"])
    return {
        "instrument": instrument.upper(),
        "origin": "IMPORTED_HISTORICAL_CSV",
        "dataset_id": rec["dataset_id"],
        "source": rec.get("source"),
        "file_hash": rec.get("file_hash"),
        "dataset_fingerprint": (rec.get("detail") or {}).get("dataset_fingerprint"),
        "timeframe": rec.get("timeframe"),
        "sessions": rec.get("sessions"),
        "rows": rec.get("rows"),
        "classification": HISTORICAL_CANDLE_DATA,
        "research_eligibility": rec.get("research_eligibility"),
        "bid_ask_status": rec.get("bid_ask_status"),
        "execution_status": execution_status(rec),
        "volume_present": any("volume" in b for b in bars[:100]),
    }


def execution_status(record: dict) -> str:
    """The only honest answer a candle dataset can give about execution."""
    if record.get("bid_ask_status") == BID_ASK_PRESENT:
        return "EXECUTION_MEASURABLE_THE_SOURCE_CARRIES_TWO_SIDED_QUOTES"
    return EXECUTION_UNMEASURED


def available() -> dict:
    """Every instrument a candle study could now run on, and from where."""
    out: dict[str, dict] = {}
    for inst in sorted(shipped_instruments()):
        out[inst] = series_source(inst)
    for inst in sorted(registry.by_instrument()):
        if inst in out:
            out[inst]["also_imported"] = [
                r["dataset_id"] for r in datasets_for(inst, include_ineligible=True)
            ]
            continue
        rows = registry.by_instrument()[inst]
        tfs = sorted({int(r.get("timeframe") or 0) for r in rows if r.get("dataset_id")})
        out[inst] = series_source(inst, timeframe=tfs[0] if tfs else 1)
        out[inst]["timeframes"] = tfs
    return out
