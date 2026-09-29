"""§4 — every anomaly a candle file can carry, counted and never repaired.

The rule this module enforces is the one that matters: **nothing is filled.**
No interpolation, no nearest timestamp, no carry-forward, no synthetic bar, no
midpoint. A missing bar stays missing and is counted, because a filled series
cannot be told from a complete one two modules later, and every statistic
computed on it is then a statistic about the filler.

Two kinds of finding come out of here:

* *anomalies*, which are reported and kept. A gap, a zero volume, a duplicate
  timestamp carrying identical prices — the dataset is still usable and the
  reader is entitled to the count;
* *fatal* findings, which reject the dataset. Impossible OHLC beyond a trivial
  share, non-positive prices, two prices for one timestamp, more than one
  symbol or contract. These are not qualities of a usable dataset with caveats;
  they mean the file is not what it claims to be.
"""
from __future__ import annotations

from app.research.historical_import import (
    BAD_PRICES,
    DUPLICATE_CONFLICT,
    MIXED_CONTRACTS,
    MIXED_SYMBOLS,
    QUALITY_ANOMALIES,
    QUALITY_CLEAN,
    QUALITY_REJECTED,
)

# A handful of bad bars in five years is a vendor artefact; a tenth of the file
# is a different instrument or a broken export. The line is drawn low because
# these rows cannot be repaired, only dropped, and dropping a large share
# silently changes the series the study runs on.
MAX_BAD_PRICE_SHARE = 0.005
MAX_BAD_PRICE_ROWS = 50


def check_rows(rows: list[dict]) -> dict:
    """Count every anomaly in already-parsed rows. Repairs nothing.

    ``rows`` are normalised dicts that still carry their source line number, so
    a finding can name the line in the operator's own file.
    """
    n = len(rows)
    out: dict = {
        "rows": n,
        "anomalies": {},
        "fatal": [],
        "dropped_rows": [],
        "first_examples": {},
    }

    def note(kind: str, line: int | None = None) -> None:
        out["anomalies"][kind] = out["anomalies"].get(kind, 0) + 1
        if kind not in out["first_examples"] and line is not None:
            out["first_examples"][kind] = line

    bad_price: list[int] = []
    for i, r in enumerate(rows):
        line = r.get("_line")
        o, h, low, c = r["open"], r["high"], r["low"], r["close"]
        if min(o, h, low, c) <= 0:
            note("NON_POSITIVE_PRICE", line)
            bad_price.append(i)
            continue
        if h < low or h < max(o, c) or low > min(o, c):
            note("IMPOSSIBLE_OHLC_RELATIONSHIP", line)
            bad_price.append(i)
            continue
        vol = r.get("volume")
        if vol is not None and vol < 0:
            note("NEGATIVE_VOLUME", line)
        if vol == 0:
            note("ZERO_VOLUME_BAR", line)

    # Duplicate timestamps. Identical duplicates are a paging artefact and the
    # later copy is dropped; conflicting ones mean two series in one file.
    by_ts: dict[int, dict] = {}
    conflicts = 0
    for i, r in enumerate(rows):
        if i in set(bad_price):
            continue
        t = r["time"]
        prev = by_ts.get(t)
        if prev is None:
            by_ts[t] = r
            continue
        same = all(
            abs(float(prev[k]) - float(r[k])) < 1e-9
            for k in ("open", "high", "low", "close")
        )
        if same:
            note("DUPLICATE_TIMESTAMP_IDENTICAL_PRICES", r.get("_line"))
        else:
            conflicts += 1
            note("DUPLICATE_TIMESTAMP_DIFFERENT_PRICES", r.get("_line"))

    if not monotonic([r["time"] for r in rows]):
        out["anomalies"]["TIMESTAMPS_NOT_ASCENDING_IN_THE_SOURCE"] = 1

    symbols = {
        (r.get("symbol") or "").strip().upper() for r in rows
    } - {""}
    contracts = {
        (str(r.get("contract") or "") + "|" + str(r.get("expiry") or "")).strip("|")
        for r in rows
    } - {""}
    if len(symbols) > 1:
        out["fatal"].append(f"{MIXED_SYMBOLS}:{','.join(sorted(symbols)[:5])}")
    if len(contracts) > 1:
        out["fatal"].append(f"{MIXED_CONTRACTS}:{','.join(sorted(contracts)[:5])}")
    if conflicts:
        out["fatal"].append(f"{DUPLICATE_CONFLICT}:{conflicts}_TIMESTAMPS")

    share = (len(bad_price) / float(n)) if n else 0.0
    if bad_price and (share > MAX_BAD_PRICE_SHARE
                      and len(bad_price) > MAX_BAD_PRICE_ROWS):
        out["fatal"].append(
            f"{BAD_PRICES}:{len(bad_price)}_OF_{n}_ROWS_{share:.2%}"
        )
    out["dropped_rows"] = sorted(bad_price)
    out["bad_price_share"] = round(share, 6)
    out["symbols"] = sorted(symbols)
    out["contracts"] = sorted(contracts)
    out["status"] = (
        QUALITY_REJECTED if out["fatal"]
        else (QUALITY_ANOMALIES if out["anomalies"] else QUALITY_CLEAN)
    )
    return out


def monotonic(ts: list[int]) -> bool:
    return all(b >= a for a, b in zip(ts, ts[1:]))


def missing_value_findings(raw_rows: int, parsed_rows: int,
                           unparsed_lines: list[int]) -> dict:
    """Rows the parser could not turn into a bar, kept as a count.

    They are not written as bars with holes in them and they are not
    interpolated. If a file is mostly these, the caller rejects it.
    """
    return {
        "source_rows": raw_rows,
        "parsed_rows": parsed_rows,
        "unparsed_rows": raw_rows - parsed_rows,
        "unparsed_first_lines": unparsed_lines[:20],
        "repair_policy": (
            "NOTHING_IS_INTERPOLATED_CARRIED_FORWARD_OR_SYNTHESIZED"
        ),
    }
