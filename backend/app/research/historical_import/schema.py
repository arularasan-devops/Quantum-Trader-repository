"""§2/§5/§6 — reading a CSV header without guessing, and the timezone rule.

Three things here are deliberately stricter than they need to be:

* a header name maps to a field only if it is in a fixed alias table. A column
  called ``px`` or ``last`` is not silently taken as the close, because the one
  time that guess is wrong every number downstream is wrong and nothing says so;
* two columns claiming the same field is a rejection, not a first-wins. A vendor
  export with both ``close`` and ``Close`` is usually two different series;
* a timestamp without an offset has no timezone. Not UTC, not IST. The
  difference between those two is five and a half hours, which turns an MCX
  evening session into a morning one and still produces a plausible-looking
  chart.
"""
from __future__ import annotations

import csv
import datetime as dt
import re
from collections import Counter

from app.research.historical_import import (
    AMBIGUOUS_COLUMNS,
    MISSING_TIMEZONE,
    MIXED_TIMEFRAMES,
    UNKNOWN_TIMEFRAME,
    UNMAPPED_COLUMNS,
    UNPARSEABLE_TIMESTAMPS,
)

# The alias table. Every accepted spelling of every field, lowercased and
# stripped of non-alphanumerics before lookup.
ALIASES: dict[str, tuple[str, ...]] = {
    "time": ("time", "timestamp", "datetime", "date", "datetimeist", "opentime",
             "bartime", "unix", "epoch", "unixtime", "timeutc", "dateutc"),
    "open": ("open", "o", "openprice", "priceopen"),
    "high": ("high", "h", "highprice", "pricehigh"),
    "low": ("low", "l", "lowprice", "pricelow"),
    "close": ("close", "c", "closeprice", "priceclose", "adjclose"),
    "volume": ("volume", "vol", "v", "totalvolume", "qty", "quantity"),
    "open_interest": ("openinterest", "oi", "opnint", "openint"),
    "symbol": ("symbol", "ticker", "instrument", "name", "scrip", "tradingsymbol"),
    "exchange": ("exchange", "exch", "venue", "segment"),
    "contract": ("contract", "contractsymbol", "series", "futcontract"),
    "expiry": ("expiry", "expirydate", "expiration", "maturity"),
    "strike": ("strike", "strikeprice"),
    "option_type": ("optiontype", "opttype", "cepe", "side", "callput", "type"),
    "bid": ("bid", "bidprice", "bestbid"),
    "ask": ("ask", "askprice", "bestask", "offer", "offerprice"),
    "bid_size": ("bidsize", "bidqty", "bidquantity"),
    "ask_size": ("asksize", "askqty", "askquantity"),
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Bar spacings a dataset may claim, in minutes. A file whose modal spacing is
# not one of these has no timeframe this layer will name for it.
KNOWN_TIMEFRAMES = (1, 2, 3, 5, 10, 15, 30, 60, 120, 240, 1440)

# Real feeds skip bars. The timeframe is the modal spacing, and the file is
# accepted as one timeframe only if the mode is this dominant among spacings
# that are exact multiples of it — a 1m file with hourly session breaks still
# reads as 1m, a file that is half 1m and half 5m does not read as either.
TIMEFRAME_MODE_SHARE = 0.55


def canon(name: str) -> str:
    return _NON_ALNUM.sub("", (name or "").strip().lower())


def map_columns(header: list[str], override: dict[str, str] | None = None) -> dict:
    """Map header names to fields, or say which field could not be mapped.

    Returns ``{"mapping": {field: column}, "unmapped": [...], "error": str|None}``.
    An override is an operator saying "this column is the close", which is the
    supported way to import a file whose header this table does not know. It is
    never inferred.
    """
    override = {k: v for k, v in (override or {}).items()}
    claims: dict[str, list[str]] = {}
    for col in header:
        key = canon(col)
        for field, names in ALIASES.items():
            if key in names:
                claims.setdefault(field, []).append(col)
    for field, col in override.items():
        if col not in header:
            return {
                "mapping": {}, "unmapped": [], "extra": [],
                "error": f"{UNMAPPED_COLUMNS}:{field}->{col}_NOT_IN_HEADER",
            }
        claims[field] = [col]

    doubled = {f: cols for f, cols in claims.items() if len(cols) > 1}
    if doubled:
        which = ",".join(f"{f}={'|'.join(c)}" for f, c in sorted(doubled.items()))
        return {"mapping": {}, "unmapped": [], "extra": [],
                "error": f"{AMBIGUOUS_COLUMNS}:{which}"}

    mapping = {f: cols[0] for f, cols in claims.items()}
    needed = ("time", "open", "high", "low", "close")
    missing = [f for f in needed if f not in mapping]
    if missing:
        return {"mapping": mapping, "unmapped": missing, "extra": [],
                "error": f"{UNMAPPED_COLUMNS}:{','.join(missing)}"}
    used = set(mapping.values())
    return {
        "mapping": mapping,
        "unmapped": [],
        "extra": [c for c in header if c not in used],
        "error": None,
    }


def sniff(path: str, *, rows: int = 50) -> dict:
    """Read the header and a few rows, without loading the file."""
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        sample = fh.read(64_000)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            return {"delimiter": delimiter, "header": [], "sample": []}
        sample_rows = []
        for i, row in enumerate(reader):
            if i >= rows:
                break
            sample_rows.append(row)
    return {
        "delimiter": delimiter,
        "header": [h.strip() for h in header],
        "sample": sample_rows,
    }


_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M", "%Y%m%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
)

_OFFSET = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def has_offset(raw: str) -> bool:
    """Does this timestamp carry its own UTC offset?"""
    return bool(_OFFSET.search((raw or "").strip()))


def parse_timestamp(raw: str, tz: dt.tzinfo | None) -> int | None:
    """One timestamp to epoch seconds, or ``None`` if it does not parse.

    An epoch integer is treated as UTC because that is what an epoch is. Any
    other form needs ``tz`` unless it carries an offset, and the caller has
    already refused the file if neither is available.
    """
    s = (raw or "").strip()
    if not s:
        return None
    if re.fullmatch(r"-?\d{9,13}", s):
        n = int(s)
        return n // 1000 if abs(n) > 10**11 else n
    try:
        parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for fmt in _FORMATS:
            try:
                parsed = dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        if tz is None:
            return None
        parsed = parsed.replace(tzinfo=tz)
    return int(parsed.timestamp())


def timezone_decision(samples: list[str], tz_arg: str | None) -> dict:
    """Whether this file's timezone is established, and by what.

    Precedence is deliberate: an offset in the data beats an operator's claim,
    because the data is evidence and the claim is a memory. When both exist and
    disagree, the file's own offsets win and the disagreement is reported.
    """
    carried = [s for s in samples if has_offset(s)]
    epochs = [
        s for s in samples if re.fullmatch(r"-?\d{9,13}", (s or "").strip())
    ]
    if epochs and len(epochs) == len(samples):
        return {"tz": dt.timezone.utc, "source": "EPOCH_SECONDS_ARE_UTC_BY_DEFINITION",
                "declared": tz_arg, "error": None}
    if carried and len(carried) == len(samples):
        return {"tz": None, "source": "THE_FILE_CARRIES_ITS_OWN_UTC_OFFSETS",
                "declared": tz_arg, "error": None}
    if carried:
        return {"tz": None, "source": None, "declared": tz_arg,
                "error": f"{UNPARSEABLE_TIMESTAMPS}:SOME_ROWS_CARRY_AN_OFFSET_AND_SOME_DO_NOT"}
    if not tz_arg:
        return {"tz": None, "source": None, "declared": None,
                "error": MISSING_TIMEZONE}
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_arg)
    except Exception:
        return {"tz": None, "source": None, "declared": tz_arg,
                "error": f"{MISSING_TIMEZONE}:UNKNOWN_ZONE_{tz_arg}"}
    return {"tz": tz, "source": f"OPERATOR_DECLARED_{tz_arg}", "declared": tz_arg,
            "error": None}


def infer_timeframe(ts: list[int], declared: int | None = None) -> dict:
    """The bar spacing of the file, in minutes.

    Gaps are expected — a session ends, a feed drops — so the timeframe is the
    modal spacing rather than the minimum or the mean. A declared timeframe is
    checked against the data instead of being trusted: the mismatch is a real
    vendor error and it silently halves or doubles every rate in a study.
    """
    if len(ts) < 3:
        return {"timeframe": declared, "error": None if declared else UNKNOWN_TIMEFRAME,
                "mode_share": None, "declared": declared}
    deltas = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    if not deltas:
        return {"timeframe": declared, "error": None if declared else UNKNOWN_TIMEFRAME,
                "mode_share": None, "declared": declared}
    counts = Counter(deltas)
    modal_sec, modal_n = counts.most_common(1)[0]
    if modal_sec % 60:
        return {"timeframe": None, "error": f"{UNKNOWN_TIMEFRAME}:MODAL_SPACING_{modal_sec}S",
                "mode_share": None, "declared": declared}
    minutes = modal_sec // 60
    if minutes not in KNOWN_TIMEFRAMES:
        return {"timeframe": None, "error": f"{UNKNOWN_TIMEFRAME}:MODAL_SPACING_{minutes}M",
                "mode_share": None, "declared": declared}
    aligned = sum(n for d, n in counts.items() if d % modal_sec == 0)
    share = aligned / float(len(deltas))
    if share < TIMEFRAME_MODE_SHARE:
        return {"timeframe": None, "mode_share": round(share, 4), "declared": declared,
                "error": f"{MIXED_TIMEFRAMES}:ONLY_{share:.0%}_OF_SPACINGS_ARE_MULTIPLES_OF_{minutes}M"}
    if declared and int(declared) != int(minutes):
        return {"timeframe": None, "mode_share": round(share, 4), "declared": declared,
                "error": f"{MIXED_TIMEFRAMES}:DECLARED_{declared}M_MEASURED_{minutes}M"}
    return {"timeframe": int(minutes), "mode_share": round(share, 4),
            "declared": declared, "error": None}
