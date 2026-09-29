"""§2 — the instrument universe and the funnel's first three tiers.

The funnel starts wide and the honest part is what it says about the part it
cannot see. NSE lists thousands of cash securities and derivatives on a couple
of hundred underlyings plus the index family; this repository holds five years
of one-minute bars for **two** instruments. Everything else is either a few
weeks of captured candles or nothing at all.

So this module does two separate jobs and keeps them apart:

* **requested universe** — what a market-wide scan is being asked to cover,
  listed verbatim so the report can show the gap rather than quietly scanning
  two instruments and calling it the market;
* **screenable universe** — what has enough history to carry a chronological
  split, with the bar counts behind the verdict.

There is no network call anywhere in this package. An NSE securities list or a
bhavcopy is a download, and adding one on my own initiative would turn a
deliberately offline research layer into a scraper. The universe therefore
comes from the shipped series plus whatever has been imported through the
generic CSV importer, and anything else is reported ``NO_HISTORY``.

Tier 1 filters (liquidity, movement, cost feasibility) are computed **from the
history that exists** rather than from a live quote, and they are labelled as
such: a turnover figure derived from candle volume is not an executable spread,
and the difference decides whether a candidate can actually be traded.
"""
from __future__ import annotations

import numpy as np

from app.research.opportunity import (
    INSUFFICIENT_HISTORY,
    NO_HISTORY,
    UNMEASURED,
)
from app.research.phase24 import data as p24data

# What a market-wide scan is asked to cover. Kept verbatim; the point of the
# list is the distance between it and what can be screened.
REQUESTED_INDEX = ("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY")
REQUESTED_MCX = ("CRUDEOIL", "NATURALGAS", "GOLD", "SILVER", "COPPER")
REQUESTED_NOTE = (
    "NSE equity derivatives cover roughly 200+ underlyings plus the index "
    "family, and the cash list is far larger. None of them can be screened "
    "from this repository without a licensed dataset, so they are reported as "
    "an absence rather than enumerated to make the funnel look wide."
)

# A chronological train/validation/holdout split needs enough distinct sessions
# that each part is a period rather than a handful of days.
MIN_SESSIONS_TO_SPLIT = 250
MIN_BARS_TO_SPLIT = 50_000

# Tier 1 thresholds, declared before anything is measured.
MIN_MEDIAN_BAR_VOLUME = 0.0     # a candle feed may publish zero volume; see below
MIN_ATR_PCT = 0.02              # a bar range this small cannot pay a round trip
MODELLED_ROUND_TRIP_PCT = 0.06  # modelled, not measured: no book in candle data


def _sessions(series) -> int:
    return int(np.unique(series.ts // 86_400).size)


def _tier1(series) -> dict:
    """Liquidity, movement and cost feasibility from history, honestly labelled."""
    rng = series.high - series.low
    px = float(np.median(series.close))
    atr = float(np.median(rng)) if rng.size else 0.0
    atr_pct = 100.0 * atr / px if px > 0 else 0.0
    vol = float(np.median(series.volume)) if series.volume.size else 0.0
    return {
        "median_price": px,
        "median_bar_range_pct": atr_pct,
        "median_bar_volume": vol,
        "volume_basis": (
            "candle volume from the historical feed; an index series often "
            "publishes none, so a zero here is an absent field and not an "
            "illiquid instrument"
        ),
        "modelled_round_trip_pct": MODELLED_ROUND_TRIP_PCT,
        "movement_over_cost": (
            atr_pct / MODELLED_ROUND_TRIP_PCT if MODELLED_ROUND_TRIP_PCT else None
        ),
        "executable_spread": UNMEASURED,
        "spread_note": (
            "a candle carries no bid or ask. Nothing here is an executable "
            "spread, and the cost above is modelled"
        ),
        "passes_movement": atr_pct >= MIN_ATR_PCT,
    }


def screenable() -> list[dict]:
    """Instruments with enough one-minute history to carry a split."""
    out: list[dict] = []
    for inst in sorted(known_instruments()):
        s = p24data.load_series(inst)
        if s is None or len(s) == 0:
            continue
        sessions = _sessions(s)
        if len(s) < MIN_BARS_TO_SPLIT or sessions < MIN_SESSIONS_TO_SPLIT:
            continue
        row = {
            "instrument": inst,
            "bars": len(s),
            "sessions": sessions,
            "first_ts": int(s.ts[0]),
            "last_ts": int(s.ts[-1]),
        }
        row.update(_tier1(s))
        out.append(row)
    return out


def known_instruments() -> set[str]:
    """Everything with any series at all: shipped, imported, or requested."""
    names = set(REQUESTED_INDEX) | set(REQUESTED_MCX)
    names |= {k.upper() for k in p24data.BACKTEST_FILES}
    try:
        from app.research.historical_import import registry as hi_registry
    except ImportError:
        return names
    names |= {
        (r.get("instrument") or "").upper()
        for r in hi_registry.datasets()
        if r.get("dataset_id") and int(r.get("timeframe") or 0) == 1
    } - {""}
    return names


def funnel() -> dict:
    """The funnel with the honest counts, including what it could not look at."""
    ok = screenable()
    ok_names = {r["instrument"] for r in ok}
    absent: list[dict] = []
    for inst in sorted(known_instruments() - ok_names):
        s = p24data.load_series(inst)
        if s is None or len(s) == 0:
            absent.append({"instrument": inst, "status": NO_HISTORY, "bars": 0,
                           "reason": "no one-minute series on disk at all"})
            continue
        absent.append({
            "instrument": inst,
            "status": INSUFFICIENT_HISTORY,
            "bars": len(s),
            "sessions": _sessions(s),
            "reason": (
                f"has history but fewer than {MIN_SESSIONS_TO_SPLIT} sessions "
                f"or {MIN_BARS_TO_SPLIT} bars, so a chronological "
                f"train/validation/holdout split would be three handfuls of "
                f"days rather than three periods"
            ),
        })
    eligible = [r for r in ok if r["passes_movement"]]
    return {
        "requested": {
            "index": list(REQUESTED_INDEX),
            "mcx": list(REQUESTED_MCX),
            "equity_and_cash": REQUESTED_NOTE,
        },
        "tier_0_known_series": len(known_instruments()),
        "tier_1_screenable": len(ok),
        "tier_1_pass_movement": len(eligible),
        "screenable": ok,
        "not_screenable": absent,
        "no_history_count": sum(1 for r in absent if r["status"] == NO_HISTORY),
        "network": "NO_NETWORK_CALL_IN_THIS_PACKAGE",
        "how_to_widen": (
            "import a licensed one-minute CSV with "
            "`python -m app.research.historical_import.cli import` and the "
            "instrument enters this funnel on the next cycle. A network "
            "universe fetcher would be a separate, separately authorised "
            "module; this one cannot reach the internet."
        ),
    }
