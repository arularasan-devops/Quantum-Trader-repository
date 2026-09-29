"""Phase 56 stage-2 smoke: the NSE archive loader, offline.

Every test here runs with a fake opener, so the suite proves the no-network
property instead of asserting it in a comment: the archive object counts its
calls, and the ingest tests assert the second ingest of the same window makes
zero.

The tests that matter most are the ones that would have caught the previous
source's defect, and they are written as properties rather than as golden
numbers: a published split must leave a visible discontinuity in the raw store, a
raw print must survive adjustment untouched, an action with no matching price
move must be labelled pre-adjusted, a price jump with no action must be labelled
unexplained rather than smoothed, and a universe screen must refuse to look at
the decision day.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.research.phase56.nse import (  # noqa: E402
    ACTION_BONUS,
    ACTION_DIVIDEND,
    ACTION_MERGER,
    ACTION_NO_PRICE_EFFECT,
    ACTION_RIGHTS,
    ACTION_SPLIT,
    ACTION_CONSOLIDATION,
    ACTION_UNCLASSIFIED,
    CA_RATIO_MISMATCH,
    PREREG,
    classify_purpose,
)
from app.research.phase56.nse import adjust as p56adjust  # noqa: E402
from app.research.phase56.nse import archive as p56archive  # noqa: E402
from app.research.phase56.nse import audit as p56audit  # noqa: E402
from app.research.phase56.nse import bhav as p56bhav  # noqa: E402
from app.research.phase56.nse import corpact as p56corpact  # noqa: E402
from app.research.phase56.nse import ingest as p56ingest  # noqa: E402
from app.research.phase56.nse import universe as p56universe  # noqa: E402
from app.research.phase56.nse.store import CONFLICT, IDEMPOTENT, RawStore, WRITTEN  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
    else:
        FAILED += 1
        print(f"FAIL {label} {detail}")


def close(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


# ---------------------------------------------------------------- fixtures

LEGACY = """SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,
TATASTEEL,EQ,951.00,961.55,943.60,959.40,960.40,949.50,1000,959400.00,27-JUL-2022,10,INE081A01020,
ACME,EQ,100.00,102.00,99.00,101.00,101.00,100.00,500,50500.00,27-JUL-2022,5,INE000A01000,
GOVSEC,GS,100.00,100.00,100.00,100.00,100.00,100.00,10,1000.00,27-JUL-2022,1,IN0020010081,
BROKEN,EQ,0,0,0,0,0,0,0,0,27-JUL-2022,0,INE111A01011,
"""

LEGACY_NEXT = """SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,
TATASTEEL,EQ,98.10,102.00,97.15,100.20,99.48,959.40,10000,1002000.00,28-JUL-2022,20,INE081A01020,
ACME,EQ,101.00,103.00,100.50,102.00,102.00,101.00,500,51000.00,28-JUL-2022,5,INE000A01000,
"""

UDIFF = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
    "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
    "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
    "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
    "2025-09-15,2025-09-15,CM,NSE,STK,1,INE000A01000,ACME,EQ,,,,,ACME LTD,110.00,112.00,"
    "109.00,111.00,111.00,110.00,,,,,700,77700.00,7,F,1,,,,,\n"
    "2025-09-15,2025-09-15,CM,NSE,FUTSTK,2,INE000A01000,ACME,EQ,2025-09-25,,,,ACME FUT,"
    "110.00,112.00,109.00,111.00,111.00,110.00,,,,,700,77700.00,7,F,1,,,,,\n"
)

DELIVERY = (
    "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, "
    "CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
    "TATASTEEL, EQ, 27-Jul-2022, 949.50, 951.00, 961.55, 943.60, 960.40, 959.40, 950.00, 1000, 9.59, 10, 400, 40.00\n"
    "ACME, EQ, 27-Jul-2022, 100.00, 100.00, 102.00, 99.00, 101.00, 101.00, 100.50, 500, 0.50, 5, -, -\n"
)

BC = (
    "SERIES,SYMBOL,SECURITY,RECORD_DT,BC_STRT_DT,BC_END_DT,EX_DT,ND_STRT_DT,ND_END_DT,PURPOSE\n"
    "EQ,TATASTEEL,Tata Steel Limited,29/07/2022, , ,28/07/2022, , ,FVSPLT FRM RS 10 TO RE 1\n"
    "BE,TATASTEEL,Tata Steel Limited,29/07/2022, , ,28/07/2022, , ,FVSPLT FRM RS 10 TO RE 1\n"
    "EQ,ACME,Acme Limited,29/07/2022, , ,28/07/2022, , ,AGM/DIV - RS 2 PER SH\n"
    "EQ,ZOMBIE,Zombie Limited,29/07/2022, , ,28/07/2022, , ,MERGER\n"
)


def zipped(name: str, text: str) -> bytes:
    import zipfile
    from io import BytesIO

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr(name, text)
    return buffer.getvalue()


class FakeOpener:
    """Serves canned bytes; records every URL asked for."""

    def __init__(self, mapping: dict[str, bytes]):
        self.mapping = mapping
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        for suffix, payload in self.mapping.items():
            if url.endswith(suffix):
                return payload
        import urllib.error

        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


def canned() -> dict[str, bytes]:
    return {
        "cm27JUL2022bhav.csv.zip": zipped("cm27JUL2022bhav.csv", LEGACY),
        "cm28JUL2022bhav.csv.zip": zipped("cm28JUL2022bhav.csv", LEGACY_NEXT),
        "sec_bhavdata_full_27072022.csv": DELIVERY.encode(),
        "PR270722.zip": zipped("Bc270722.csv", BC),
        "PR280722.zip": zipped("Bc280722.csv", BC),
    }


# ------------------------------------------------------- §1 preregistration

check("prereg fingerprint stable", PREREG.fingerprint() == PREREG.fingerprint())
check("prereg fingerprint 16 hex", len(PREREG.fingerprint()) == 16)
check("execution model is cash equity", PREREG.execution_model == "CASH_EQUITY_LONG_ONLY")
for banned in ("FUTURES", "OPTIONS", "LEVERAGE", "MARGIN", "SHORT_SELLING", "REAL_BROKER_ORDERS"):
    check(f"prohibits {banned}", banned in PREREG.prohibited)
check(
    "index membership declared missing",
    any("INDEX_MEMBERSHIP" in item for item in PREREG.declared_limitations),
)
check("universe not called an index", "INDEX" not in PREREG.universe_label)
check("dividends declared non-adjusting", PREREG.dividend_adjusts_price is False)

# ------------------------------------------------------ §4 purpose classifier

cases = [
    ("FVSPLT FRM RS 10 TO RE 1", ACTION_SPLIT, 0.10),
    ("FV SPLT FRM RS 10 TO RS 2", ACTION_SPLIT, 0.20),
    ("FVSPLT FRM RS 5 TO RE 1", ACTION_SPLIT, 0.20),
    ("FVSPLTFRM RS 100 TO RE 1", ACTION_SPLIT, 0.01),
    ("BONUS 1:1", ACTION_BONUS, 0.50),
    ("BONUS 1:2", ACTION_BONUS, 2 / 3),
    ("BONUS 2:1", ACTION_BONUS, 1 / 3),
    ("BONUS 3:1", ACTION_BONUS, 0.25),
]
for purpose, expected_type, expected_factor in cases:
    kind, factor, _ = classify_purpose(purpose)
    check(f"classify {purpose!r} type", kind == expected_type, f"got {kind}")
    check(f"classify {purpose!r} factor", factor is not None and close(factor, expected_factor, 1e-9), f"got {factor}")

kind, factor, _ = classify_purpose("CONSOLIDATION OF SHARES FRM RE 1 TO RS 10")
check("consolidation is upward factor", kind == ACTION_CONSOLIDATION and factor is not None and close(factor, 10.0))

for purpose, expected in (
    ("RIGHTS 1:5 @ PRM RS 12.5", ACTION_RIGHTS),
    ("MERGER", ACTION_MERGER),
    ("DEMERGER", "DEMERGER"),
    ("CAPITAL REDUCTION", "CAPITAL_REDUCTION"),
    ("SUSPENSION DUE TO SCHEME", "SCHEME_SUSPENSION"),
    ("ANNUAL GENERAL MEETING", ACTION_NO_PRICE_EFFECT),
    ("INTEREST PAYMENT", ACTION_NO_PRICE_EFFECT),
    ("REDEMPTION", ACTION_NO_PRICE_EFFECT),
    ("BUY BACK", "BUYBACK"),
):
    kind, factor, _ = classify_purpose(purpose)
    check(f"classify {purpose!r}", kind == expected, f"got {kind}")
    check(f"{purpose!r} has no invented factor", factor is None, f"got {factor}")

kind, _, dividend = classify_purpose("AGM/DIV - RS 2.50 PER SH")
check("dividend amount parsed", kind == ACTION_DIVIDEND and dividend is not None and close(dividend, 2.50))
kind, factor, _ = classify_purpose("SOMETHING NOBODY HAS SEEN")
check("unknown purpose is UNCLASSIFIED not harmless", kind == ACTION_UNCLASSIFIED and factor is None)
kind, _, _ = classify_purpose("")
check("empty purpose is UNCLASSIFIED", kind == ACTION_UNCLASSIFIED)

# --------------------------------------------------------- schema normalising

legacy = p56bhav.parse_legacy(LEGACY)
check("legacy parses EQ rows", len(legacy.rows) == 2, f"got {len(legacy.rows)}")
check("legacy skips non-equity series", legacy.skipped_other_series == 1)
check("legacy rejects bad OHLC", legacy.rejected == 1 and legacy.reject_reasons.get("INVALID_OHLC") == 1)
row = [r for r in legacy.rows if r.symbol == "TATASTEEL"][0]
check("legacy date parsed", row.trade_date == "2022-07-27")
check("legacy isin kept", row.isin == "INE081A01020")
check("legacy prev_close kept raw", close(row.prev_close, 949.50))
check("legacy delivery unknown not zero", row.delivery_qty is None and row.delivery_pct is None)

# Parts of 2020 publish TIMESTAMP as 27-Jul-22, elsewhere as 27-JUL-2022.
two_digit = p56bhav.parse_legacy(LEGACY.replace("-2022", "-22"))
check("two-digit legacy year parsed", len(two_digit.rows) == 2 and two_digit.rejected == 1)
check(
    "two-digit year resolves to the same session",
    [r for r in two_digit.rows if r.symbol == "TATASTEEL"][0].trade_date == "2022-07-27",
)

udiff = p56bhav.parse_udiff(UDIFF)
check("udiff keeps only STK rows", len(udiff.rows) == 1 and udiff.skipped_other_segment == 1)
check("udiff date parsed", udiff.rows[0].trade_date == "2025-09-15")
check("udiff fields mapped", close(udiff.rows[0].close, 111.00) and udiff.rows[0].volume == 700)
check("dispatch picks udiff", p56bhav.parse_bhavcopy(UDIFF).schema == p56bhav.SCHEMA_UDIFF)
check("dispatch picks legacy", p56bhav.parse_bhavcopy(LEGACY).schema == p56bhav.SCHEMA_LEGACY)
try:
    p56bhav.parse_bhavcopy("not,a,bhavcopy\n1,2,3\n")
    check("malformed schema raises", False)
except ValueError:
    check("malformed schema raises", True)

delivery = p56bhav.parse_delivery(DELIVERY)
check("delivery parsed for one name", delivery[("TATASTEEL", "EQ")] == (400, 40.0))
check("delivery dash is unknown not zero", delivery[("ACME", "EQ")] == (None, None))

# Some published sec_bhavdata_full files are NUL-padded; csv raises on them.
nul_padded = DELIVERY.replace("\n", "\n\x00", 1)
check("NUL-padded delivery file still parses", p56bhav.parse_delivery(nul_padded) == delivery)
check(
    "NUL-padded bhavcopy still parses",
    len(p56bhav.parse_bhavcopy(LEGACY.replace("\n", "\x00\n", 1)).rows) == len(legacy.rows),
)
enriched = p56bhav.enrich_with_delivery(legacy.rows, delivery)
target = [r for r in enriched if r.symbol == "TATASTEEL"][0]
check("enrichment attaches delivery", target.delivery_qty == 400)
check("enrichment does not alter prices", close(target.close, 959.40) and close(target.open, 951.00))

# ---------------------------------------------------------------- action file

actions, counters = p56corpact.parse_bc(BC, source_file="Bc270722.csv")
check("bc rows parsed", counters["rows"] == 4)
check("bc keeps both series rows before dedupe", len(actions) == 4)
deduped = p56corpact.dedupe(actions)
check("dedupe collapses EQ/BE duplicates", len(deduped) == 3, f"got {len(deduped)}")
split = [a for a in deduped if a.symbol == "TATASTEEL"][0]
check("bc ex-date parsed", split.ex_date == date(2022, 7, 28))
check("bc split quantified", split.quantified and close(split.factor, 0.10))
merger = [a for a in deduped if a.symbol == "ZOMBIE"][0]
check("bc merger unquantified", merger.action_type == ACTION_MERGER and merger.factor is None)

factor, unquantified = p56corpact.combined_factor(
    [a for a in deduped if a.symbol == "TATASTEEL"]
)
check("combined factor of one split", close(factor, 0.10) and not unquantified)
factor, unquantified = p56corpact.combined_factor(deduped)
check("combined factor reports unquantified types", "MERGER" in unquantified)
factor, unquantified = p56corpact.combined_factor([a for a in deduped if a.symbol == "ACME"])
check("dividend contributes no factor", factor is None and not unquantified)

# ----------------------------------------------------- raw store idempotency

with TemporaryDirectory() as tmp:
    store = RawStore(Path(tmp))
    first = store.write_session(date(2022, 7, 27), enriched)
    check("first write is WRITTEN", first.status == WRITTEN)
    again = store.write_session(date(2022, 7, 27), enriched)
    check("second identical write is IDEMPOTENT", again.status == IDEMPOTENT)
    check("digest stable across identical writes", first.digest == again.digest)
    tampered = [
        p56bhav.RawRow(**{**enriched[0].as_dict(), "close": enriched[0].close * 2})
    ] + enriched[1:]
    conflict = store.write_session(date(2022, 7, 27), tampered)
    check("differing rewrite is CONFLICT", conflict.status == CONFLICT)
    reread = store.read_session(date(2022, 7, 27))
    check("raw store not overwritten by conflict", any(close(r.close, 959.40) for r in reread))
    check("round-trip preserves row count", len(reread) == len(enriched))
    check(
        "round-trip preserves delivery None",
        [r for r in reread if r.symbol == "ACME"][0].delivery_qty is None,
    )

# -------------------------------------------------------- ingest, no network

with TemporaryDirectory() as tmp:
    opener = FakeOpener(canned())
    archive = p56archive.Archive(Path(tmp) / "cache", opener=opener, min_interval_sec=0.0)
    store = RawStore(Path(tmp) / "store")
    summary = p56ingest.ingest_range(
        date(2022, 7, 27), date(2022, 7, 28), archive, store
    )
    check("ingest stored two sessions", summary.ok == 2, f"got {summary.ok}")
    check("ingest counted rejected rows", summary.rejected_rows == 1)
    check("ingest recorded delivery session", summary.delivery_sessions == 1)
    check("ingest wrote action table", summary.actions_parsed == 3, f"got {summary.actions_parsed}")
    check("ingest saw no conflicts", summary.conflicts == 0)
    calls_first = archive.calls

    opener2 = FakeOpener(canned())
    archive2 = p56archive.Archive(Path(tmp) / "cache", opener=opener2, min_interval_sec=0.0)
    summary2 = p56ingest.ingest_range(date(2022, 7, 27), date(2022, 7, 28), archive2, store)
    check("re-ingest is idempotent", summary2.ok == 2 and summary2.conflicts == 0)
    check("re-ingest makes no network call", len(opener2.urls) == 0, f"got {len(opener2.urls)}")
    check("first ingest did make calls", calls_first > 0)

    # a holiday and an unreachable day are different facts
    missing = archive2.bhavcopy(date(2022, 7, 29))
    check("absent file is NOT_PUBLISHED", missing.status == p56archive.NOT_PUBLISHED)

    class Broken:
        def __call__(self, url):
            raise TimeoutError("read timed out")

    broken = p56archive.Archive(Path(tmp) / "cache2", opener=Broken(), min_interval_sec=0.0)
    result = broken.bhavcopy(date(2022, 7, 29))
    check("transport failure is UNAVAILABLE not absent", result.status == p56archive.UNAVAILABLE)
    store3 = RawStore(Path(tmp) / "store3")
    status, _ = p56ingest.ingest_day(date(2022, 7, 29), broken, store3, with_actions=False)
    check("unreachable day is unfinished work", status == p56ingest.SESSION_UNAVAILABLE)
    check("unreachable day stores no rows", store3.read_session(date(2022, 7, 29)) == [])

    # A file whose every row is rejected is a schema the parser does not know,
    # not an empty trading day, and the day must stay repairable afterwards.
    unreadable = LEGACY.replace("27-JUL-2022", "27/07/2022")
    store4 = RawStore(Path(tmp) / "store4")
    dud = p56archive.Archive(
        Path(tmp) / "cache4",
        opener=FakeOpener({"cm27JUL2022bhav.csv.zip": zipped("cm27JUL2022bhav.csv", unreadable)}),
        min_interval_sec=0.0,
    )
    status, _ = p56ingest.ingest_day(date(2022, 7, 27), dud, store4, with_actions=False, with_delivery=False)
    check("all-rejected archive is PARSE_FAILED not OK", status == p56ingest.SESSION_PARSE_FAILED)
    check("all-rejected archive stores no rows", store4.read_session(date(2022, 7, 27)) == [])
    good = p56archive.Archive(Path(tmp) / "cache5", opener=FakeOpener(canned()), min_interval_sec=0.0)
    status, _ = p56ingest.ingest_day(date(2022, 7, 27), good, store4, with_actions=False, with_delivery=False)
    check("a failed day can be repaired later", status == p56ingest.SESSION_OK)
    check("repaired day holds the prints", len(store4.read_session(date(2022, 7, 27))) == 2)

    raw_rows = store.symbol_series(series="EQ")
    check("symbol view has TATASTEEL both sessions", len(raw_rows.get("TATASTEEL", [])) == 2)
    check(
        "raw split discontinuity preserved in store",
        close(raw_rows["TATASTEEL"][0].close, 959.40) and close(raw_rows["TATASTEEL"][1].open, 98.10),
    )

    # ------------------------------------------------ adjustment behaviour
    stored_actions = store.read_actions()
    series, ledger = p56adjust.build_all(raw_rows, stored_actions)
    tata = series["TATASTEEL"]
    check("adjusted keeps last print equal to raw", close(tata[-1].close, tata[-1].raw_close))
    check("adjusted scales pre-split price by factor", close(tata[0].close, 95.94, 1e-6), f"got {tata[0].close}")
    check("adjusted scales volume the other way", tata[0].volume == 10000, f"got {tata[0].volume}")
    check("turnover untouched by split", close(tata[0].turnover_inr, 959400.00))
    check("cum factor recorded", close(tata[0].cum_factor, 0.10))
    explained = [item for item in ledger if item.symbol == "TATASTEEL"]
    check("split break explained", len(explained) == 1 and explained[0].status == p56adjust.EXPLAINED)
    check("acme has no break", not any(item.symbol == "ACME" for item in ledger))
    check("raw store unchanged by build", close(store.read_session(date(2022, 7, 27))[-1].close, 959.40))

# --------------------------------------- missing / conflicting CA metadata

base = p56bhav.RawRow(
    trade_date="2022-07-27", symbol="X", series="EQ", isin="I", open=100.0, high=101.0,
    low=99.0, close=100.0, last=100.0, prev_close=100.0, volume=100, turnover_inr=10000.0,
    trades=5, delivery_qty=None, delivery_pct=None, schema="T",
)


def next_row(close_price: float, prev: float, day: str = "2022-07-28") -> p56bhav.RawRow:
    return p56bhav.RawRow(**{
        **base.as_dict(), "trade_date": day, "open": close_price, "high": close_price,
        "low": close_price, "close": close_price, "last": close_price, "prev_close": prev,
    })


def action(symbol: str, purpose: str, ex: date = date(2022, 7, 28)):
    kind, factor, dividend = classify_purpose(purpose)
    from app.research.phase56.nse import CorporateAction

    return CorporateAction(symbol, "EQ", ex, purpose, kind, factor, dividend, factor is not None, "t")


rows = [base, next_row(10.0, 100.0)]
index = p56corpact.index_by_symbol_date([action("X", "FVSPLT FRM RS 10 TO RE 1")])
_, breaks = p56adjust.build_adjusted("X", rows, index)
check("matching ratio is EXPLAINED", breaks[0].status == p56adjust.EXPLAINED)

index = p56corpact.index_by_symbol_date([action("X", "BONUS 1:1")])
_, breaks = p56adjust.build_adjusted("X", rows, index)
check("wrong published ratio is CA_RATIO_MISMATCH", breaks[0].status == "CA_RATIO_MISMATCH")

# HNDFDS 2022-07-21: opened on its 1:5 basis then rallied 20% intraday, so the
# close-based ratio alone would have called a correct action table wrong.
rallied = p56bhav.RawRow(**{
    **base.as_dict(), "trade_date": "2022-07-28", "open": 20.3, "high": 25.0,
    "low": 20.0, "close": 24.0, "last": 24.0, "prev_close": 100.0,
})
index = p56corpact.index_by_symbol_date([action("X", "FVSPLT FRM RS 10 TO RS 2")])
_, breaks = p56adjust.build_adjusted("X", [base, rallied], index)
check("open-based ratio rescues an intraday rally", breaks[0].status == p56adjust.EXPLAINED)
check("both ratios reported", close(breaks[0].observed_ratio, 0.24) and close(breaks[0].observed_open_ratio, 0.203))

_, breaks = p56adjust.build_adjusted("X", rows, {})
check("jump with no action is UNEXPLAINED", breaks[0].status == "UNEXPLAINED_DISCONTINUITY")
check("unexplained break is unresolved", not breaks[0].resolved)

index = p56corpact.index_by_symbol_date([action("X", "RIGHTS 1:2 @ PRM RE 1/-")])
adjusted, breaks = p56adjust.build_adjusted("X", rows, index)
check("rights jump is CA_UNQUANTIFIED", breaks[0].status == "CA_UNQUANTIFIED")
check("rights gets no invented factor", close(adjusted[0].cum_factor, 1.0))
check("unresolved break sets lookback barrier", adjusted[-1].lookback_valid_from == "2022-07-28")
check(
    "lookback across barrier refused",
    not p56adjust.lookback_allowed(adjusted[-1], date(2022, 7, 27)),
)
check(
    "lookback after barrier allowed",
    p56adjust.lookback_allowed(adjusted[-1], date(2022, 7, 28)),
)

flat = [base, next_row(100.0, 100.0)]
index = p56corpact.index_by_symbol_date([action("X", "FVSPLT FRM RS 10 TO RE 1")])
_, breaks = p56adjust.build_adjusted("X", flat, index)
check(
    "action with no price move is CA_WITHOUT_DISCONTINUITY",
    breaks and breaks[0].status == p56adjust.CA_WITHOUT_DISCONTINUITY,
)
_, breaks = p56adjust.build_adjusted("X", flat, {})
check("quiet session produces no break", breaks == [])

# PFC's 1:4 bonus: a 20% move sits inside the discontinuity band, so requiring a
# jump before comparing factors made every small action unexplainable.
small = [base, next_row(80.1, 100.0)]
index = p56corpact.index_by_symbol_date([action("X", "BONUS 1:4")])
_, breaks = p56adjust.build_adjusted("X", small, index)
check(
    "action inside the band is EXPLAINED on its factor",
    breaks and breaks[0].status == p56adjust.EXPLAINED,
    f"got {breaks[0].status if breaks else 'no break'}",
)
index = p56corpact.index_by_symbol_date([action("X", "BONUS 1:1")])
_, breaks = p56adjust.build_adjusted("X", small, index)
check(
    "a moved-but-wrong ratio inside the band is MISMATCH not pre-adjusted",
    breaks and breaks[0].status == "CA_RATIO_MISMATCH",
    f"got {breaks[0].status if breaks else 'no break'}",
)

# SETFGOLD 2022-01-06: the 1:100 split is plain against the prior close (4259.95
# -> 42.35) but NSE published an already-divided PREVCLOSE, so that basis alone
# reads flat and would indict a correct action table.
prevclose_adjusted = p56bhav.RawRow(**{
    **base.as_dict(), "trade_date": "2022-07-28", "open": 1.0, "high": 1.05,
    "low": 0.98, "close": 1.0, "last": 1.0, "prev_close": 1.012,
})
index = p56corpact.index_by_symbol_date([action("X", "FVSPLTFRM RS 100 TO RE 1")])
_, breaks = p56adjust.build_adjusted("X", [base, prevclose_adjusted], index)
check(
    "pre-adjusted PREVCLOSE does not hide a real split",
    breaks and breaks[0].status == p56adjust.EXPLAINED,
    f"got {breaks[0].status if breaks else 'no break'}",
)
check(
    "the basis that explained the break is recorded",
    breaks[0].matched_basis == "PRIOR_CLOSE_TO_CLOSE",
    f"got {breaks[0].matched_basis!r}",
)
truly_flat = [base, next_row(100.0, 100.0)]
index = p56corpact.index_by_symbol_date([action("X", "FVSPLTFRM RS 100 TO RE 1")])
_, breaks = p56adjust.build_adjusted("X", truly_flat, index)
check(
    "a flat boundary on every basis is still CA_WITHOUT_DISCONTINUITY",
    breaks and breaks[0].status == p56adjust.CA_WITHOUT_DISCONTINUITY,
)
check("pre-adjusted break names no matching basis", breaks[0].matched_basis == "")

# INOXWIND 2024: the 3:1 bonus was published for 17-May and the prices changed
# basis on 24-May. Applying the factor on the announced date would rewrite five
# sessions of levels and leave the real jump unexplained.
def dated(day: str, open_: float, close: float, prev: float) -> "p56bhav.RawRow":
    return p56bhav.RawRow(**{
        **base.as_dict(), "trade_date": day, "open": open_, "high": max(open_, close),
        "low": min(open_, close), "close": close, "last": close, "prev_close": prev,
    })


late = [
    dated("2024-05-16", 100.0, 100.0, 100.0),
    dated("2024-05-17", 101.0, 100.0, 100.0),
    dated("2024-05-21", 100.0, 100.0, 100.0),
    dated("2024-05-22", 100.0, 100.0, 100.0),
    dated("2024-05-23", 100.0, 100.0, 100.0),
    dated("2024-05-24", 25.0, 25.5, 100.0),
]
index = p56corpact.index_by_symbol_date([action("X", "BONUS 3:1", date(2024, 5, 17))])
_, breaks = p56adjust.build_adjusted("X", late, index)
by_date = {item.on_date: item for item in breaks}
check(
    "an action is explained on the session the basis actually changed",
    "2024-05-24" in by_date and by_date["2024-05-24"].status == p56adjust.EXPLAINED,
    f"got {[(b.on_date, b.status) for b in breaks]}",
)
check(
    "the published ex-date stays on the record",
    by_date["2024-05-24"].published_ex_date == "2024-05-17",
)
check("the session offset it used is reported", by_date["2024-05-24"].session_offset == 4)
check(
    "the announced session is not marked as an action session",
    "2024-05-17" not in by_date,
)
rows_adj, _ = p56adjust.build_adjusted("X", late, index)
by_day = {row.trade_date: row for row in rows_adj}
check(
    "no factor is applied before the basis changed",
    by_day["2024-05-23"].cum_factor == by_day["2024-05-16"].cum_factor,
)
# BCG: the action was published for August and the name did not trade again until
# October. Pinning the factor to the session it resumed on would invent an
# ex-date this source cannot establish.
suspended = [
    dated("2021-08-16", 100.0, 100.0, 100.0),
    dated("2021-10-12", 105.0, 105.0, 100.0),
]
index = p56corpact.index_by_symbol_date([action("X", "BONUS 1:4", date(2021, 8, 18))])
alignment = p56adjust.align_actions("X", suspended, index)
check("an ex-date with no session near it is not aligned", alignment.assigned == {})
rows, _ = p56adjust.build_adjusted("X", suspended, index)
check(
    "an unplaced action with a real factor still bars lookback",
    rows[-1].lookback_valid_from == "2021-10-12" and rows[-1].cum_factor == 1.0,
    f"got {rows[-1].lookback_valid_from!r} factor {rows[-1].cum_factor}",
)
check(
    "the unmapped action is reported with its reason",
    alignment.unmapped == [("2021-08-18", "NO_SESSION_NEAR_PUBLISHED_EX_DATE", "2021-10-12")],
    f"got {alignment.unmapped}",
)

# HAL 2023: two published groups, one boundary. Merging them multiplied 0.5 by
# 0.5 and reported a 1:4 split that never happened.
two_claims = [
    dated("2023-09-27", 100.0, 100.0, 100.0),
    dated("2023-09-28", 50.0, 50.0, 100.0),
    dated("2023-09-29", 50.0, 50.0, 50.0),
]
index = p56corpact.index_by_symbol_date([
    action("X", "FV SPLT FRM RS 10 TO RS 5", date(2023, 9, 28)),
    action("X", "AGM/DIV - RS 5 PER SH", date(2023, 9, 29)),
])
_, breaks = p56adjust.build_adjusted("X", two_claims, index)
check(
    "two groups do not multiply into a fabricated factor",
    breaks and breaks[0].expected_factor == 0.5,
    f"got {breaks[0].expected_factor if breaks else 'no break'}",
)
check("the real split stays EXPLAINED", breaks[0].status == p56adjust.EXPLAINED)

# VSTIND 2024: 10:1 bonus published for 08-30, basis changed on 09-06, and the
# observed 0.107 disagrees with the published 0.091 by more than the tolerance.
vstind = [
    dated("2024-08-29", 4805.0, 4766.45, 4759.25),
    dated("2024-08-30", 4780.0, 4567.6, 4766.45),
    dated("2024-09-02", 4648.0, 4560.9, 4567.6),
    dated("2024-09-05", 4467.0, 4456.45, 4560.9),
    dated("2024-09-06", 474.4, 481.25, 4456.45),
]
index = p56corpact.index_by_symbol_date([action("X", "BONUS 10:1", date(2024, 8, 30))])
rows, breaks = p56adjust.build_adjusted("X", vstind, index)
mismatch = [item for item in breaks if item.status == CA_RATIO_MISMATCH]
check(
    "a published action is named on the session the basis changed",
    len(mismatch) == 1 and mismatch[0].on_date == "2024-09-06",
    f"got {[(b.on_date, b.status) for b in breaks]}",
)
check(
    "the disagreement keeps the published date and offset on the record",
    mismatch[0].published_ex_date == "2024-08-30" and mismatch[0].session_offset == 3,
    f"got {mismatch[0].published_ex_date} offset {mismatch[0].session_offset}",
)
check(
    "a disputed factor is never applied to the series",
    all(row.cum_factor == 1.0 for row in rows),
    f"got {[row.cum_factor for row in rows]}",
)
check(
    "a disputed boundary bars lookback across it",
    rows[-1].lookback_valid_from == "2024-09-06",
    f"got {rows[-1].lookback_valid_from!r}",
)

far = [dated("2024-01-02", 100.0, 100.0, 100.0), dated("2024-01-03", 25.0, 25.0, 100.0)]
index = p56corpact.index_by_symbol_date([action("X", "BONUS 3:1", date(2023, 6, 1))])
_, breaks = p56adjust.build_adjusted("X", far, index)
check(
    "a match outside the declared window is not claimed",
    breaks and breaks[0].status == p56adjust.UNEXPLAINED_DISCONTINUITY,
    f"got {breaks[0].status if breaks else 'no break'}",
)

within_band = [base, next_row(90.0, 100.0)]
_, breaks = p56adjust.build_adjusted("X", within_band, {})
check("a -10% day is not a discontinuity", breaks == [])

dividend_only = [base, next_row(78.0, 100.0)]
index = p56corpact.index_by_symbol_date([action("X", "AGM/DIV - RS 22 PER SH")])
_, breaks = p56adjust.build_adjusted("X", dividend_only, index)
check("large dividend drop is not adjusted away", breaks[0].expected_factor is None)
check("dividend-only break is not EXPLAINED", breaks[0].status != p56adjust.EXPLAINED)

# two quantified actions on one ex-date compose
double = [base, next_row(25.0, 100.0)]
index = p56corpact.index_by_symbol_date(
    [action("X", "FVSPLT FRM RS 10 TO RS 5"), action("X", "BONUS 1:1")]
)
_, breaks = p56adjust.build_adjusted("X", double, index)
check("two actions compose multiplicatively", close(breaks[0].expected_factor, 0.25))
check("composed factor explains the jump", breaks[0].status == p56adjust.EXPLAINED)

# ------------------------------------------------------- date-correct universe

def life_row(day: str, symbol: str, **extra):
    """One row per (day, symbol) with a distinct ISIN, as the archive has."""
    return p56bhav.RawRow(
        **{**base.as_dict(), "trade_date": day, "symbol": symbol, "isin": f"INE{symbol}", **extra}
    )


sessions = {
    "2022-01-03": [life_row("2022-01-03", "ALIVE"), life_row("2022-01-03", "ZOMBIE")],
    "2022-01-04": [life_row("2022-01-04", "ALIVE"), life_row("2022-01-04", "ZOMBIE")],
    "2022-01-05": [
        life_row("2022-01-05", "ALIVE"),
        life_row("2022-01-05", "NEWCO"),
        life_row("2022-01-05", "RESTRICTED", series="BE"),
    ],
}
lives = p56universe.build_lives(sessions)
check("life records built for every symbol", set(lives) == {"ALIVE", "ZOMBIE", "NEWCO", "RESTRICTED"})
check("first session read from archive", lives["ZOMBIE"].first_session == "2022-01-03")
check("last session read from archive", lives["ZOMBIE"].last_session == "2022-01-04")
check("newco has later first session", lives["NEWCO"].first_session == "2022-01-05")

stops = p56universe.stop_reasons(lives, [action("ZOMBIE", "MERGER", date(2022, 1, 6))])
check("merger explains disappearance", stops.get("ZOMBIE") == ACTION_MERGER)

status = p56universe.status_on(
    "ZOMBIE", date(2022, 1, 5), lives["ZOMBIE"], None, renames={}, stop_actions=stops
)
check("stopped-with-reason status", status == p56universe.STOPPED_MERGER_OR_SCHEME)
status = p56universe.status_on(
    "ZOMBIE", date(2022, 1, 5), lives["ZOMBIE"], None, renames={}, stop_actions={}
)
check("stopped-without-reason is honest", status == p56universe.STOPPED_TRADING_REASON_UNKNOWN)
status = p56universe.status_on(
    "NEWCO", date(2022, 1, 3), lives["NEWCO"], None, renames={}, stop_actions={}
)
check("pre-listing status", status == p56universe.NOT_YET_IN_ARCHIVE)
status = p56universe.status_on(
    "ALIVE", date(2022, 1, 5), lives["ALIVE"], "EQ", renames={}, stop_actions={}
)
check("trading status", status == p56universe.TRADING)
status = p56universe.status_on(
    "RESTRICTED", date(2022, 1, 5), lives["RESTRICTED"], "BE", renames={}, stop_actions={}
)
check("restricted series flagged, not silently eligible", status == p56universe.TRADING_RESTRICTED_SERIES)
status = p56universe.status_on(
    "ALIVE", date(2022, 1, 4), lives["ALIVE"], None, renames={}, stop_actions={}
)
check("mid-life absence is NOT_TRADED_THIS_SESSION", status == p56universe.NOT_TRADED_THIS_SESSION)

rename_sessions = {
    "2022-01-03": [p56bhav.RawRow(**{**base.as_dict(), "trade_date": "2022-01-03", "symbol": "OLDNAME", "isin": "IX"})],
    "2022-01-04": [p56bhav.RawRow(**{**base.as_dict(), "trade_date": "2022-01-04", "symbol": "NEWNAME", "isin": "IX"})],
}
renamed = p56universe.detect_symbol_changes(p56universe.build_lives(rename_sessions))
check("symbol change detected by ISIN continuity", renamed.get("OLDNAME") == "NEWNAME")
check("no false rename for distinct ISINs", p56universe.detect_symbol_changes(lives) == {})

# ---------------------------------------------------- look-ahead prevention

prior = {"2022-01-03": sessions["2022-01-03"], "2022-01-04": sessions["2022-01-04"]}
try:
    p56universe.eligible_on(date(2022, 1, 4), prior)
    check("screen refuses to see the decision day", False)
except ValueError:
    check("screen refuses to see the decision day", True)

eligibility = p56universe.eligible_on(
    date(2022, 1, 5), prior, min_history=1, min_turnover=0.0, min_price=0.0, top_n=10
)
check("screen returns eligible names", set(eligibility.eligible) == {"ALIVE", "ZOMBIE"})
check("screen labelled as declared liquidity universe", eligibility.label == "DECLARED_LIQUIDITY_UNIVERSE")
eligibility = p56universe.eligible_on(date(2022, 1, 5), prior, min_history=1, min_turnover=1e12)
check("turnover floor rejects", eligibility.eligible == () and eligibility.rejected_turnover == 2)
eligibility = p56universe.eligible_on(date(2022, 1, 5), prior, min_history=1, min_price=1e6, min_turnover=0.0)
check("price floor rejects", eligibility.eligible == () and eligibility.rejected_price == 2)
eligibility = p56universe.eligible_on(date(2022, 1, 5), prior, min_history=99)
check("history floor rejects", eligibility.eligible == () and eligibility.rejected_history == 2)
eligibility = p56universe.eligible_on(
    date(2022, 1, 5), prior, min_history=1, min_turnover=0.0, min_price=0.0, top_n=1
)
check("top-n truncates and reports", len(eligibility.eligible) == 1 and eligibility.rejected_rank == 1)
restricted_prior = {"2022-01-05": sessions["2022-01-05"]}
eligibility = p56universe.eligible_on(
    date(2022, 1, 6), restricted_prior, min_history=1, min_turnover=0.0, min_price=0.0
)
check("BE series never eligible", "RESTRICTED" not in eligibility.eligible and eligibility.rejected_series == 1)

# ------------------------------------------------------------ §36 gate shape

with TemporaryDirectory() as tmp:
    empty = RawStore(Path(tmp))
    payload = p56audit.audit(empty)
    check("empty dataset stops the gate", payload["gate"] == p56audit.GATE_INADEQUATE)

with TemporaryDirectory() as tmp:
    opener = FakeOpener(canned())
    archive = p56archive.Archive(Path(tmp) / "cache", opener=opener, min_interval_sec=0.0)
    store = RawStore(Path(tmp) / "store")
    p56ingest.ingest_range(date(2022, 7, 27), date(2022, 7, 28), archive, store)
    payload = p56audit.audit(store)
    names = {finding["name"]: finding for finding in payload["findings"]}
    for required in (
        "PRICE_COVERAGE",
        "RAW_PRICE_CONFIRMED",
        "CORPORATE_ACTION_COVERAGE",
        "ADJUSTMENT_AUDITABLE",
        "SURVIVORSHIP_CONTROL",
        "UNIVERSE_RECONSTRUCTABLE",
        "HISTORICAL_INDEX_MEMBERSHIP",
        "HISTORICAL_SECTOR_CLASSIFICATION",
    ):
        check(f"gate reports {required}", required in names)
    check(
        "two-session dataset cannot pass the gate",
        payload["gate"] == p56audit.GATE_INADEQUATE and "PRICE_COVERAGE" in payload["blocking_failures"],
    )
    check(
        "raw probe confirmed on the ingested split",
        names["RAW_PRICE_CONFIRMED"]["numbers"]["probes"][0]["status"] == "RAW_CONFIRMED",
    )
    check(
        "index membership always reported missing",
        names["HISTORICAL_INDEX_MEMBERSHIP"]["status"] == p56audit.MISSING,
    )
    check("gate fingerprint present", len(payload["fingerprint"]) == 16)
    check("gate carries prereg fingerprint", payload["prereg_fingerprint"] == PREREG.fingerprint())
    check(
        "gate is deterministic",
        p56audit.audit(store)["fingerprint"] == payload["fingerprint"],
    )

# ------------------------------------------------------------- isolation §37

package = Path(__file__).resolve().parent / "app" / "research" / "phase56" / "nse"
sources = {path.name: path.read_text() for path in package.glob("*.py")}
joined = "\n".join(sources.values())
for banned in (
    "phase41", "phase42", "phase43", "phase44", "phase45", "phase46", "phase47",
    "phase48", "phase49", "phase50", "phase51", "phase52", "phase53", "phase54", "phase55",
):
    check(f"no import of {banned}", banned not in joined)
for banned in ("placeOrder", "place_order", "smartConnect", "SmartConnect", "getCandleData"):
    check(f"no broker path: {banned}", banned not in joined)
for banned in ("SMARTAPI_KEY", "SMARTAPI_PIN", "TOTP", "os.environ"):
    check(f"no credential read: {banned}", banned not in joined)
for banned in ("BUY_PROBABILITY", "CONFIDENCE_SCORE", "GUARANTEED_RETURN"):
    check(f"prohibited label absent: {banned}", banned not in joined)
check("no tick hook registration", "register_tick" not in joined and "on_tick" not in joined)
check(
    "only the archive host is addressed",
    "nsearchives.nseindia.com" in joined and "apiconnect.angelone.in" not in joined,
)
check(
    "raw parser knows nothing about adjustment",
    "AdjustedRow" not in sources["bhav.py"]
    and "adjusted_dir" not in sources["bhav.py"]
    and "cum_factor" not in sources["bhav.py"],
)
check(
    "only the store owns raw file writes",
    all("open(\"w\"" not in text for name, text in sources.items() if name in ("bhav.py", "adjust.py", "corpact.py")),
)

print(f"\nPHASE 56 NSE SMOKE — {PASSED} passed, {FAILED} failed")
raise SystemExit(1 if FAILED else 0)
