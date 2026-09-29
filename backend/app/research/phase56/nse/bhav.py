"""Normalisation of the three published daily schemas into one raw row.

Two bhavcopy schemas cover the window and they disagree about everything except
the numbers: field names, date format, and whether the file contains one segment
or several. The UDiFF file carries futures and options rows in the same CSV, so
the equity filter is explicit (``FinInstrmTp == STK``) rather than implied — a
loader that forgot it would quietly mix derivative prints into a cash-equity
study.

Normalising is the only transformation applied here. No price is adjusted, no
row is repaired, no missing field is filled in with a neighbour's value. A row
that cannot be parsed into a complete OHLC print is *rejected and counted*, and
the count is part of the audit; dropping it silently would make a data problem
look like a quiet market.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, asdict
from datetime import date, datetime
from io import StringIO

from . import STORED_SERIES

SCHEMA_LEGACY = "LEGACY_CM_BHAVCOPY"
SCHEMA_UDIFF = "UDIFF_CM_BHAVCOPY"
SCHEMA_DELIVERY = "SEC_BHAVDATA_FULL"


@dataclass(frozen=True)
class RawRow:
    """One security's raw print for one session. Never mutated after ingest."""

    trade_date: str
    symbol: str
    series: str
    isin: str
    open: float
    high: float
    low: float
    close: float
    last: float
    prev_close: float
    volume: int
    turnover_inr: float
    trades: int
    delivery_qty: int | None
    delivery_pct: float | None
    schema: str

    def as_dict(self) -> dict:
        return asdict(self)


class ParseReport:
    def __init__(self, schema: str):
        self.schema = schema
        self.rows: list[RawRow] = []
        self.rejected = 0
        self.reject_reasons: dict[str, int] = {}
        self.skipped_other_segment = 0
        self.skipped_other_series = 0

    def reject(self, reason: str) -> None:
        self.rejected += 1
        self.reject_reasons[reason] = self.reject_reasons.get(reason, 0) + 1


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _number(value: str | None) -> float | None:
    text = _clean(value).replace(",", "")
    if text in ("", "-", "NA", "N/A"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


#: The legacy bhavcopy's ``TIMESTAMP`` is two-digit in parts of 2020
#: (``13-Jul-20``) and four-digit elsewhere (``13-JUL-2020``). Both are the same
#: session; accepting only one silently discards a whole day of prints.
LEGACY_DATE_FORMATS = ("%d-%b-%Y", "%d-%b-%y")


def _legacy_date(text: str):
    for fmt in LEGACY_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def sanitize(text: str) -> str:
    """Drop NUL bytes before the csv module sees them.

    Some published ``sec_bhavdata_full`` files are NUL-padded, and Python's csv
    reader raises ``line contains NUL`` on them. The bytes carry no data — dropping
    them changes no field value — but refusing to read the file would silently cost
    a day of delivery volume, so they are stripped and nothing else is altered.
    """
    return text.replace("\x00", "")


def _positive_ohlc(row: dict[str, float | None]) -> bool:
    values = [row["open"], row["high"], row["low"], row["close"]]
    if any(v is None or v <= 0 for v in values):
        return False
    return row["low"] <= min(row["open"], row["close"]) and row["high"] >= max(row["open"], row["close"])


def parse_legacy(text: str) -> ParseReport:
    report = ParseReport(SCHEMA_LEGACY)
    for raw in csv.DictReader(StringIO(sanitize(text))):
        series = _clean(raw.get("SERIES")).upper()
        if series not in STORED_SERIES:
            report.skipped_other_series += 1
            continue
        values = {
            "open": _number(raw.get("OPEN")),
            "high": _number(raw.get("HIGH")),
            "low": _number(raw.get("LOW")),
            "close": _number(raw.get("CLOSE")),
        }
        if not _positive_ohlc(values):
            report.reject("INVALID_OHLC")
            continue
        trade_date = _legacy_date(_clean(raw.get("TIMESTAMP")))
        if trade_date is None:
            report.reject("BAD_DATE")
            continue
        report.rows.append(
            RawRow(
                trade_date=trade_date.isoformat(),
                symbol=_clean(raw.get("SYMBOL")).upper(),
                series=series,
                isin=_clean(raw.get("ISIN")).upper(),
                open=values["open"],
                high=values["high"],
                low=values["low"],
                close=values["close"],
                last=_number(raw.get("LAST")) or values["close"],
                prev_close=_number(raw.get("PREVCLOSE")) or 0.0,
                volume=int(_number(raw.get("TOTTRDQTY")) or 0),
                turnover_inr=_number(raw.get("TOTTRDVAL")) or 0.0,
                trades=int(_number(raw.get("TOTALTRADES")) or 0),
                delivery_qty=None,
                delivery_pct=None,
                schema=SCHEMA_LEGACY,
            )
        )
    return report


def parse_udiff(text: str) -> ParseReport:
    report = ParseReport(SCHEMA_UDIFF)
    for raw in csv.DictReader(StringIO(sanitize(text))):
        if _clean(raw.get("FinInstrmTp")).upper() not in ("STK", "EQ"):
            report.skipped_other_segment += 1
            continue
        series = _clean(raw.get("SctySrs")).upper()
        if series not in STORED_SERIES:
            report.skipped_other_series += 1
            continue
        values = {
            "open": _number(raw.get("OpnPric")),
            "high": _number(raw.get("HghPric")),
            "low": _number(raw.get("LwPric")),
            "close": _number(raw.get("ClsPric")),
        }
        if not _positive_ohlc(values):
            report.reject("INVALID_OHLC")
            continue
        try:
            trade_date = datetime.strptime(_clean(raw.get("TradDt")), "%Y-%m-%d").date()
        except ValueError:
            report.reject("BAD_DATE")
            continue
        report.rows.append(
            RawRow(
                trade_date=trade_date.isoformat(),
                symbol=_clean(raw.get("TckrSymb")).upper(),
                series=series,
                isin=_clean(raw.get("ISIN")).upper(),
                open=values["open"],
                high=values["high"],
                low=values["low"],
                close=values["close"],
                last=_number(raw.get("LastPric")) or values["close"],
                prev_close=_number(raw.get("PrvsClsgPric")) or 0.0,
                volume=int(_number(raw.get("TtlTradgVol")) or 0),
                turnover_inr=_number(raw.get("TtlTrfVal")) or 0.0,
                trades=int(_number(raw.get("TtlNbOfTxsExctd")) or 0),
                delivery_qty=None,
                delivery_pct=None,
                schema=SCHEMA_UDIFF,
            )
        )
    return report


def parse_bhavcopy(text: str) -> ParseReport:
    """Dispatch on the header actually present, not on the date."""
    text = sanitize(text)
    head = text.splitlines()[0] if text else ""
    if "TckrSymb" in head:
        return parse_udiff(text)
    if "SYMBOL" in head and "TIMESTAMP" in head:
        return parse_legacy(text)
    raise ValueError("unrecognised bhavcopy schema")


def parse_delivery(text: str) -> dict[tuple[str, str], tuple[int | None, float | None]]:
    """``(symbol, series) -> (delivered qty, delivery %)`` — enrichment only.

    The header of this file is space-padded (``" SERIES"``), which is why keys are
    stripped rather than matched literally. Rows whose delivery cells are blank
    (the file uses ``-`` for securities where it does not apply) yield ``None``,
    never zero: zero delivery and unknown delivery are different facts.
    """
    out: dict[tuple[str, str], tuple[int | None, float | None]] = {}
    reader = csv.reader(StringIO(sanitize(text)))
    try:
        header = [_clean(cell).upper() for cell in next(reader)]
    except StopIteration:
        return out
    index = {name: position for position, name in enumerate(header)}
    required = ("SYMBOL", "SERIES", "DELIV_QTY", "DELIV_PER")
    if not all(name in index for name in required):
        return out
    for row in reader:
        if len(row) < len(header):
            continue
        symbol = _clean(row[index["SYMBOL"]]).upper()
        series = _clean(row[index["SERIES"]]).upper()
        if not symbol or series not in STORED_SERIES:
            continue
        qty = _number(row[index["DELIV_QTY"]])
        pct = _number(row[index["DELIV_PER"]])
        out[(symbol, series)] = (None if qty is None else int(qty), pct)
    return out


def enrich_with_delivery(
    rows: list[RawRow], delivery: dict[tuple[str, str], tuple[int | None, float | None]]
) -> list[RawRow]:
    """Attach delivery fields where published; leave them ``None`` where not."""
    if not delivery:
        return rows
    out = []
    for row in rows:
        found = delivery.get((row.symbol, row.series))
        if found is None:
            out.append(row)
            continue
        qty, pct = found
        out.append(
            RawRow(**{**row.as_dict(), "delivery_qty": qty, "delivery_pct": pct})
        )
    return out


def session_date_of(rows: list[RawRow]) -> date | None:
    dates = {row.trade_date for row in rows}
    if len(dates) != 1:
        return None
    return date.fromisoformat(next(iter(dates)))
