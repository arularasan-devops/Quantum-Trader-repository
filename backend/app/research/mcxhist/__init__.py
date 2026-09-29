"""MCX futures historical data layer — collection and audit only. RESEARCH ONLY.

This package exists because a four-instrument study (NIFTY, CRUDEOIL, GOLD,
SILVER) cannot be run on a two-instrument dataset, and the honest way to close
that gap is to collect the missing history and say exactly what was collected —
not to reason about the gap, and not to substitute an instrument that happens to
have data.

What the provider actually returns, measured before this package was written and
carried here as a declared property rather than an assumption:

* the scrip master and ``searchScrip`` list **live contracts only**. For GOLD and
  SILVER that is a handful of contracts whose earliest expiry is in the future,
  so **no expired contract token is resolvable**. Every per-contract inventory
  this package produces is therefore an inventory of *reachable* contracts, and
  the unreachable ones are recorded as ``EXPIRED_TOKEN_UNRESOLVABLE`` rather than
  omitted;
* exactly one token per root answers a pre-listing window with data — five years
  of 1-minute bars at correct price levels for the root — while every other
  token of the same root answers zero rows. The series under that token is
  therefore **not that contract's traded life**; it is a root-level series whose
  roll rule the provider does not disclose.

The second point is the load-bearing one, and it decides the shape of this
package. A roll cannot be *constructed* from data whose contract identity is not
exposed, so nothing here pretends to construct one. Instead:

* the series is stored under its token with the classification
  ``PROVIDER_CONTINUOUS_UNVERIFIED_ROLL``. The name is the finding;
* the rollover work is a **detection** pass, not a construction pass. Every
  expiry-cycle boundary is scanned for the artefacts a stitch leaves behind —
  overnight jumps far outside the instrument's own distribution, duplicated
  minutes, missing sessions — and what it finds is reported, never repaired;
* no price adjustment of any kind is applied. A back-adjusted series would hide
  exactly the discontinuity a reader needs to see, and an adjustment invented
  here would be indistinguishable from a fabrication.

Three rules the caller cannot switch off:

* **live-capture rows are not history.** ``data/history.db`` holds rows sampled
  by the live tick loop; for GOLD and SILVER those values are synthetic-feed
  values that disagree with the real market by tens of thousands of points. This
  package never reads that store and never writes to it, so the two cannot be
  merged by accident;
* **a failed window is recorded, not raised.** A five-year pull is hundreds of
  calls against a rate-limited endpoint. One exhausted window must not discard
  the windows that already landed, and the audit reports the hole;
* **the fetcher is injected.** The network call lives behind a callable, so every
  test in this package runs without credentials and nothing can quietly fall
  back to the simulated feed.

Nothing here computes an expectancy, ranks a mechanism, or writes to a
production path. The deliverable is a dataset and an audit of it.
"""
from __future__ import annotations

import hashlib
import json

VERSION = "mcxhist.v1"

# ---------------------------------------------------------------- declarations

#: Roots this layer collects, with the exchange they trade on.
ROOTS: dict[str, str] = {"GOLD": "MCX", "SILVER": "MCX"}

#: The four instruments the audit reports on. NIFTY and CRUDEOIL are already on
#: disk and are audited from their existing files, not re-collected.
AUDIT_INSTRUMENTS: tuple[str, ...] = ("NIFTY", "CRUDEOIL", "GOLD", "SILVER")

ONE_MINUTE = "ONE_MINUTE"

#: Calendar days per request window. The provider caps a single ONE_MINUTE
#: response near 8,000 rows; an MCX session is ~870 minutes, so five days stays
#: under the cap with margin. Windows are derived from the declared start date,
#: never from the run date, so a resumed run cannot leave a seam between two
#: differently aligned passes.
CHUNK_DAYS = 5

#: Minimum seconds between historical calls, attempts per window, and the first
#: backoff. The provider answers a burst with AB1021; patience here is the
#: difference between one pass and a hole in the history.
MIN_INTERVAL_SEC = 3.4
MAX_ATTEMPTS = 4
BACKOFF_START_SEC = 20.0

#: A session of the MCX evening cycle runs 09:00-23:30 IST.
SESSION_MINUTES_FULL = 870

#: Coverage bands for a single session, as a share of ``SESSION_MINUTES_FULL``.
#: A session below the floor is ``NOT_GRADED``: it is kept, it is counted, and it
#: is refused as a decision session by any study reading this dataset.
SESSION_COVERAGE_OK = 0.90
SESSION_COVERAGE_PARTIAL = 0.60

#: Data classification. This is candle data. It is not an executable book, and
#: no bid, ask or depth is ever derived from it.
DATA_CLASS = "HISTORICAL_CANDLE_DATA"

#: What the stored series is, named honestly.
SERIES_CLASS = "PROVIDER_CONTINUOUS_UNVERIFIED_ROLL"

#: Contract statuses used by the inventory.
CONTRACT_REACHABLE = "REACHABLE"
CONTRACT_NO_HISTORY = "LISTED_NO_PRE_LISTING_HISTORY"
CONTRACT_UNRESOLVABLE = "EXPIRED_TOKEN_UNRESOLVABLE"

#: An overnight return this many times the instrument's own median absolute
#: overnight return is flagged as a possible stitch artefact. It is a flag, not
#: a correction, and the threshold is declared here so it cannot be tuned after
#: seeing which boundaries trip it.
JUMP_FLAG_MULTIPLE = 8.0

#: Timeframes the audit reports answerability for.
TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "30m", "60m", "daily", "multi_day")

#: Minimum completed sessions for a timeframe to be answerable at all. Daily and
#: multi-day mechanisms in this project use a 20-30 session lookback and need a
#: chronological 60/20/20 split on top of it, so the floor is not cosmetic.
MIN_SESSIONS_INTRADAY = 250
MIN_SESSIONS_DAILY = 400

VALID_HISTORY_YES = "YES"
VALID_HISTORY_NO = "NO"

IST_OFFSET = 19_800  # +05:30 in seconds


def preregistration() -> dict:
    """Every declared value, as the artefact header and the fingerprint input."""
    return {
        "version": VERSION,
        "purpose": (
            "collect and audit MCX futures 1-minute history for GOLD and SILVER "
            "so a four-instrument study is possible; produce no expectancy"
        ),
        "roots": dict(ROOTS),
        "audit_instruments": list(AUDIT_INSTRUMENTS),
        "interval": ONE_MINUTE,
        "chunk_days": CHUNK_DAYS,
        "min_interval_sec": MIN_INTERVAL_SEC,
        "max_attempts": MAX_ATTEMPTS,
        "backoff_start_sec": BACKOFF_START_SEC,
        "session_minutes_full": SESSION_MINUTES_FULL,
        "session_coverage_ok": SESSION_COVERAGE_OK,
        "session_coverage_partial": SESSION_COVERAGE_PARTIAL,
        "data_class": DATA_CLASS,
        "series_class": SERIES_CLASS,
        "jump_flag_multiple": JUMP_FLAG_MULTIPLE,
        "timeframes": list(TIMEFRAMES),
        "min_sessions_intraday": MIN_SESSIONS_INTRADAY,
        "min_sessions_daily": MIN_SESSIONS_DAILY,
        "roll_policy": (
            "NONE_CONSTRUCTED. The provider exposes one token per root carrying "
            "a multi-year series whose contract identity it does not disclose, "
            "and no expired-contract token is resolvable from the scrip master "
            "or searchScrip. A deterministic roll therefore cannot be built "
            "from this source; the series is stored as "
            "PROVIDER_CONTINUOUS_UNVERIFIED_ROLL and every expiry-cycle "
            "boundary is scanned for stitch artefacts instead."
        ),
        "price_adjustment": (
            "NONE. No back-adjustment, no ratio adjustment, no gap repair. An "
            "adjustment would hide the discontinuity a reader must see."
        ),
        "look_ahead": (
            "a bar is stored under the timestamp the provider stamped it with "
            "and is never shifted. Nothing in this package reads a bar to decide "
            "anything about an earlier bar, because nothing in this package "
            "decides anything at all."
        ),
        "live_capture_boundary": (
            "data/history.db is never read or written here. Its GOLD/SILVER "
            "rows are simulated-feed values and are not history."
        ),
        "options": (
            "HISTORICAL_OPTION_EXECUTION_UNAVAILABLE. No historical option bid, "
            "ask or depth is collected, derived or implied for any root."
        ),
        "orders": "no order path exists in this package",
    }


def fingerprint() -> str:
    """Stable hash of the declaration. Changing any declared value changes it."""
    blob = json.dumps(preregistration(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
