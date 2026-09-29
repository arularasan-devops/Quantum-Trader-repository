"""Historical import smoke — the refusals that keep imported candles honest.

Every check here exists because the opposite mistake would turn a spreadsheet
into a result:

* a naive timestamp read in the importer's timezone instead of the exchange's,
  silently shifting a whole series by hours;
* a mid-point, a carried-forward price or an interpolated bar filling a gap, so
  a hole in the vendor's export reads as a quiet market;
* an OHLC file being treated as a book, so a spread-sensitive study produces a
  number where it should produce ``EXECUTION_UNMEASURED``;
* the same export imported twice and every statistic counted twice with it;
* an option file accepted without a strike, an expiry or a side, so CE and PE
  rows pool into one instrument;
* an imported dataset shadowing the shipped five-year series a prior result was
  measured on, which would change an old number without touching its code;
* imported history reaching a production signal, a gate, or Phase 41-44;
* this package acquiring a network call, which is the line between "an operator
  supplied a file" and "the tool went and got one".

    .venv/bin/python _smoke_historical_import.py
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import ast
import json
import os
import tempfile

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="hismoke-"))

from app.research import historical_import as hi  # noqa: E402
from app.research.historical_import import (  # noqa: E402
    ALREADY_IMPORTED,
    BID_ASK_ABSENT,
    BID_ASK_PRESENT,
    CONTRACT_ABSENT,
    CONTRACT_PRESENT,
    ELIGIBLE_FULL,
    ELIGIBLE_NONE,
    ELIGIBLE_SHORT,
    EXECUTION_UNMEASURED,
    HISTORICAL_CANDLE_DATA,
    HISTORICAL_LEAD,
    IMPORTED,
    IMPORT_REJECTED,
    LIVE_EXECUTABLE_BOOK,
    NOT_GRADED,
    AMBIGUOUS_COLUMNS,
    DUPLICATE_CONFLICT,
    MISSING_TIMEZONE,
    OPTION_PRESENT,
    QUALITY_ANOMALIES,
    QUALITY_CLEAN,
    QUALITY_REJECTED,
    REJECT,
    UNMAPPED_COLUMNS,
)
from app.research.historical_import import adapter as hadapter  # noqa: E402
from app.research.historical_import import cli as hcli  # noqa: E402
from app.research.historical_import import coverage as hcov  # noqa: E402
from app.research.historical_import import ingest, quality  # noqa: E402
from app.research.historical_import import registry, schema  # noqa: E402
from app.research.historical_import import sessions as hsessions  # noqa: E402
from app.research.historical_import import store  # noqa: E402
from app.research.phase24 import data as p24data  # noqa: E402

PASS = 0
FAIL: list[str] = []

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
PKG_DIR = os.path.dirname(os.path.abspath(hi.__file__))


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


def refused(reason: str | None, key: str) -> bool:
    """A refusal for the named reason. Prefix, because reasons carry detail."""
    return bool(reason) and str(reason).startswith(REJECT[key])


def _csv(tmp: str, name: str, header: str, rows: list[str]) -> str:
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + "\n" + "\n".join(rows) + "\n")
    return path


def _bars(days: int, minutes: int = 375, *, start_day: int = 5,
          fmt: str = "%Y-%m-%d %H:%M:%S", tz: dt.tzinfo | None = None,
          skip: set[int] | None = None) -> list[str]:
    """Synthetic 1-minute bars. Deterministic: a smoke must not vary run to run."""
    out: list[str] = []
    px = 100.0
    d = dt.datetime(2026, 1, start_day, 9, 15, tzinfo=tz)
    made = 0
    while made < days:
        if d.weekday() >= 5:
            d += dt.timedelta(days=1)
            continue
        for m in range(minutes):
            if skip and m in skip:
                continue
            t = d + dt.timedelta(minutes=m)
            o = px
            c = round(px + ((m % 7) - 3) * 0.1, 2)
            px = c
            out.append(f"{t.strftime(fmt)},{o:.2f},{max(o, c) + 0.2:.2f},"
                       f"{min(o, c) - 0.2:.2f},{c:.2f},{500 + m}")
        made += 1
        d += dt.timedelta(days=1)
    return out


OHLCV = "timestamp,open,high,low,close,volume"


def main() -> int:  # noqa: C901 - one linear suite, grouped by concern
    tmp = tempfile.mkdtemp(prefix="hi_")
    data = os.path.join(tmp, "data_dir")
    os.makedirs(data, exist_ok=True)
    original = store.data_dir
    store.data_dir = lambda: data  # type: ignore[assignment]
    try:
        # --- the classification vocabulary ---------------------------------
        ok(HISTORICAL_CANDLE_DATA != LIVE_EXECUTABLE_BOOK,
           "historical candles and an executable book are separate labels")
        ok(hi.PROMOTION_CEILING == HISTORICAL_LEAD,
           "the ceiling for anything imported is HISTORICAL_LEAD")

        # --- §6 column mapping is explicit, never guessed -------------------
        m = schema.map_columns(["timestamp", "open", "high", "low", "close",
                                "volume"])
        ok(m["mapping"]["time"] == "timestamp", "a plain OHLCV header maps")
        ok(m["error"] is None, "a complete OHLCV header is accepted")
        bad = schema.map_columns(["timestamp", "open", "high", "low", "volume"])
        ok(str(bad["error"]).startswith(UNMAPPED_COLUMNS),
           "a header with no close is refused, not defaulted")
        ok("close" in (bad["unmapped"] or []),
           "the refusal names the field that is missing")
        amb = schema.map_columns(["time", "datetime", "open", "high", "low",
                                  "close"])
        ok(str(amb["error"]).startswith(AMBIGUOUS_COLUMNS),
           "two columns claiming the same field is ambiguity, not a preference")
        unknown = schema.map_columns(["BarTime", "O", "H", "L", "LastPrice"])
        ok(str(unknown["error"]).startswith(UNMAPPED_COLUMNS),
           "an unrecognised header is refused rather than positionally guessed")
        override = schema.map_columns(
            ["BarTime", "O", "H", "L", "LastPrice"],
            {"time": "BarTime", "open": "O", "high": "H", "low": "L",
             "close": "LastPrice"},
        )
        ok(override["error"] is None,
           "an operator can state their own mapping explicitly")
        ok(override["mapping"]["close"] == "LastPrice",
           "the stated mapping is used verbatim")
        ok(schema.map_columns(["timestamp", "open", "high", "low", "close"],
                              {"close": "NoSuchColumn"})["error"]
           .startswith(UNMAPPED_COLUMNS),
           "an override naming a column the file lacks fails closed")

        # --- §5 timezone: fail closed, never assume -------------------------
        naive = schema.timezone_decision(["2026-01-05 09:15:00"], None)
        ok(str(naive["error"]).startswith(MISSING_TIMEZONE),
           "naive timestamps with no --tz are refused")
        told = schema.timezone_decision(["2026-01-05 09:15:00"], "Asia/Kolkata")
        ok(told["error"] is None and "Asia/Kolkata" in told["source"],
           "an operator-declared timezone is recorded as declared, not inferred")
        offset = schema.timezone_decision(["2026-01-05T09:15:00+05:30"], None)
        ok(offset["error"] is None,
           "timestamps carrying their own offset need no --tz")
        ok("OFFSET" in offset["source"],
           "the offset case records that the file carried the offset")
        epoch = schema.timezone_decision(["1767584700"], None)
        ok(epoch["error"] is None and "EPOCH" in epoch["source"],
           "epoch seconds are unambiguous and are recorded as such")
        ok(schema.parse_timestamp("2026-01-05 09:15:00", IST)
           == int(dt.datetime(2026, 1, 5, 9, 15, tzinfo=IST).timestamp()),
           "a declared timezone is applied to the naive timestamp, not the host's")
        ok(schema.parse_timestamp("2026-01-05T09:15:00+05:30", None)
           == schema.parse_timestamp("2026-01-05T03:45:00+00:00", None),
           "an offset-bearing timestamp keeps the instant it names")
        ok(schema.parse_timestamp("not a time", IST) is None,
           "an unparseable timestamp returns None instead of a guess")

        # --- §4 quality is measured and never repaired ----------------------
        rows = [
            {"time": 10, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
             "volume": 10.0, "_line": 2},
            {"time": 20, "open": 1.0, "high": 0.5, "low": 2.0, "close": 1.5,
             "volume": 10.0, "_line": 3},
            {"time": 30, "open": 0.0, "high": 1.0, "low": 0.0, "close": 1.0,
             "volume": 5.0, "_line": 4},
            {"time": 40, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
             "volume": -1.0, "_line": 5},
        ]
        q = quality.check_rows(rows)
        ok(q["anomalies"].get("IMPOSSIBLE_OHLC_RELATIONSHIP") == 1,
           "high below low is counted as impossible, not reordered")
        ok(q["anomalies"].get("NON_POSITIVE_PRICE") == 1,
           "a zero price is counted, not replaced")
        ok(q["anomalies"].get("NEGATIVE_VOLUME") == 1, "negative volume is counted")
        ok(q["status"] == QUALITY_ANOMALIES, "anomalies change the status")
        ok(len(rows) == 4, "check_rows does not drop or rewrite the rows it checks")
        dupes = quality.check_rows([
            {"time": 10, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
             "volume": 1.0, "_line": 2},
            {"time": 10, "open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0,
             "volume": 1.0, "_line": 3},
        ])
        ok(any(f.startswith(DUPLICATE_CONFLICT) for f in dupes["fatal"]),
           "one timestamp with two different prices is a conflict, not a choice")
        ok(dupes["status"] == QUALITY_REJECTED,
           "and a conflicting duplicate rejects the dataset outright")

        # --- §10 sessions: absent days are NOT_GRADED -----------------------
        day = int(dt.datetime(2026, 1, 5, 9, 15, tzinfo=IST).timestamp())
        ts = [day + 60 * i for i in range(375)]
        rep = hsessions.sessions(ts, 1)
        ok(rep["session_count"] == 1, "one day of bars is one session")
        ok(rep["missing_bars"] == 0, "a complete run of minutes has no missing bars")
        holed = hsessions.sessions([t for i, t in enumerate(ts) if i not in
                                    (100, 101, 102)], 1)
        ok(holed["missing_bars"] == 3,
           "a hole inside a session span is counted as missing")
        ok(holed["gap_pct"] > 0, "the gap percentage is reported, not smoothed")
        ok(NOT_GRADED in json.dumps(rep),
           "the session report carries the NOT_GRADED vocabulary")
        wk = hsessions.sessions(
            [day + 86_400 * 4 + 60 * i for i in range(10)] + ts[:10], 1)
        ok(wk["not_graded_weekday_count"] >= 1,
           "a weekday with no bars at all is NOT_GRADED, not called a failure")
        ok(wk["grading"] == NOT_GRADED and all(
               isinstance(d, str) for d in wk["not_graded_weekdays"]),
           "absent weekdays are named, and the grading for them is NOT_GRADED")

        # --- §2/§3 the happy path, end to end -------------------------------
        f_bn = _csv(tmp, "BANKNIFTY_1m.csv", OHLCV, _bars(6))
        insp = ingest.inspect(f_bn, tz="Asia/Kolkata", instrument="BANKNIFTY")
        ok(insp["would_import"], "a clean OHLCV file with a declared tz imports")
        ok(insp["classification"] == HISTORICAL_CANDLE_DATA,
           "inspect classifies the file as historical candles")
        ok(not os.path.exists(os.path.join(data, "registry.jsonl")),
           "inspect writes nothing at all")

        res = ingest.import_file(f_bn, source="smoke", tz="Asia/Kolkata",
                                 instrument="BANKNIFTY")
        ok(res["status"] == IMPORTED, "the import succeeds")
        did = res["dataset_id"]
        ok(res["rows"] == 6 * 375, "every source bar is stored, none collapsed")
        ok(res["classification"] == HISTORICAL_CANDLE_DATA,
           "the stored dataset is labelled historical candles")

        # --- §7 idempotency --------------------------------------------------
        again = ingest.import_file(f_bn, source="smoke", tz="Asia/Kolkata",
                                   instrument="BANKNIFTY")
        ok(again["status"] == ALREADY_IMPORTED, "the same bytes re-import as ALREADY")
        ok(again["dataset_id"] == did, "and resolve to the same dataset id")
        ok(len(store.read_bars(did)) == 6 * 375,
           "a second import adds no duplicate rows")
        ok(len([d for d in os.listdir(data)
                if os.path.isdir(os.path.join(data, d))]) == 1,
           "and creates no second dataset directory")
        renamed = _csv(tmp, "same_bytes_other_name.csv", OHLCV, _bars(6))
        ok(ingest.import_file(renamed, source="smoke", tz="Asia/Kolkata",
                              instrument="BANKNIFTY")["status"] == ALREADY_IMPORTED,
           "idempotency is on the bytes, so renaming the file changes nothing")
        ok(store.file_hash(f_bn) == store.file_hash(renamed),
           "identical bytes hash identically regardless of filename")

        # --- §3 provenance ---------------------------------------------------
        man = store.read_manifest(did) or {}
        prov = man.get("provenance") or {}
        for field in ("source", "original_filename", "file_hash_sha256",
                      "import_timestamp", "source_columns", "column_mapping"):
            ok(bool(prov.get(field)), f"provenance records {field}")
        for field in ("importer_version", "schema_version"):
            ok(bool(man.get(field)), f"the manifest records {field}")
        ok(prov["file_hash_sha256"] == store.file_hash(f_bn),
           "the recorded hash is the hash of the operator's file")
        ok(man.get("dataset_fingerprint"),
           "the normalised dataset carries its own fingerprint")
        ok(prov.get("original_path") and os.path.exists(prov["original_path"]),
           "the source file is still where the operator left it")
        ok(open(f_bn, encoding="utf-8").read().startswith(OHLCV),
           "and its bytes were not rewritten by the import")
        ok(man["identity"].get("lot_size") is None,
           "lot size is absent because the source never supplied one")

        # --- §8/§13 the executable boundary ---------------------------------
        ok(res["bid_ask_status"] == BID_ASK_ABSENT, "an OHLCV file has no bid/ask")
        ok(res["execution_note"] == EXECUTION_UNMEASURED,
           "so execution economics on it is UNMEASURED")
        bars = store.read_bars(did)
        ok(all("bid" not in b and "ask" not in b for b in bars),
           "no stored bar carries an invented bid or ask")
        ok(all("spread" not in b and "mid" not in b for b in bars),
           "and none carries a spread or a mid-price either")
        ok(hadapter.execution_status(registry.get(did) or {})
           == EXECUTION_UNMEASURED,
           "the adapter reports UNMEASURED for a bid/ask-less dataset")

        f_q = _csv(tmp, "WITHQUOTES.csv",
                   "timestamp,open,high,low,close,volume,bid,ask",
                   [r + ",99.5,100.5" for r in _bars(2)])
        q_res = ingest.import_file(f_q, source="smoke_quotes", tz="Asia/Kolkata",
                                   instrument="NATURALGAS")
        ok(q_res["bid_ask_status"] == BID_ASK_PRESENT,
           "a file that really has bid and ask is recorded as having them")
        qbars = store.read_bars(q_res["dataset_id"])
        ok(all(b.get("bid") == 99.5 and b.get("ask") == 100.5 for b in qbars),
           "supplied quotes are stored as supplied")

        # --- §6 contract and option identity --------------------------------
        ok(res["contract_detail_status"] == CONTRACT_ABSENT,
           "an index candle file has no contract identity and says so")
        f_fut = _csv(tmp, "CRUDE_FUT.csv",
                     "timestamp,open,high,low,close,volume,contract,expiry",
                     [r + ",CRUDEOIL26JANFUT,2026-01-19" for r in _bars(2)])
        fut = ingest.import_file(f_fut, source="smoke_fut", tz="Asia/Kolkata",
                                 instrument="CRUDEOIL")
        ok(fut["contract_detail_status"] == CONTRACT_PRESENT,
           "a futures file with a contract column carries contract identity")
        f_mixed = _csv(tmp, "MIXED.csv",
                       "timestamp,open,high,low,close,volume,contract",
                       [r + (",A26JANFUT" if i % 2 else ",A26FEBFUT")
                        for i, r in enumerate(_bars(1))])
        mixed = ingest.inspect(f_mixed, tz="Asia/Kolkata", instrument="X")
        ok(refused(mixed["reject_reason"], "MIXED_CONTRACTS"),
           "two expiries in one file are refused, not silently merged")
        f_opt_bad = _csv(tmp, "OPT_NOSTRIKE.csv",
                         "timestamp,open,high,low,close,volume,option_type",
                         [r + ",CE" for r in _bars(1)])
        opt_bad = ingest.inspect(f_opt_bad, tz="Asia/Kolkata", instrument="NIFTY")
        ok(refused(opt_bad["reject_reason"], "CONTRACT_AMBIGUOUS"),
           "a CE with no strike and no expiry is not a contract and is refused")
        f_opt = _csv(
            tmp, "OPT.csv",
            "timestamp,open,high,low,close,volume,strike,expiry,option_type",
            [r + ",23000,2026-01-29,CE" for r in _bars(2)])
        opt = ingest.import_file(f_opt, source="smoke_opt", tz="Asia/Kolkata",
                                 instrument="NIFTY_OPT")
        ok(opt["option_detail_status"] == OPTION_PRESENT,
           "strike + expiry + side together are a complete option identity")
        ok(opt["bid_ask_status"] == BID_ASK_ABSENT,
           "an option candle file is still not a book")

        # --- §2 malformed files fail closed ---------------------------------
        empty = _csv(tmp, "EMPTY.csv", OHLCV, [])
        ok(refused(ingest.inspect(empty, tz="Asia/Kolkata",
                                  instrument="X")["reject_reason"], "NO_ROWS"),
           "a header-only file is refused")
        ok(refused(ingest.inspect(f_bn)["reject_reason"], "MISSING_TIMEZONE"),
           "a naive file with no --tz is refused at import too")
        noinst = _csv(tmp, "NOINSTRUMENT.csv", OHLCV, _bars(1))
        ok(refused(ingest.inspect(noinst, tz="Asia/Kolkata")["reject_reason"],
                   "NO_INSTRUMENT"),
           "a file with no symbol column and no --instrument is refused")
        junk = _csv(tmp, "JUNK.csv", OHLCV,
                    ["not,a,real,row,at,all"] * 20)
        junk_reason = ingest.inspect(junk, tz="Asia/Kolkata",
                                     instrument="X")["reject_reason"]
        ok(refused(junk_reason, "UNPARSEABLE_TIMESTAMPS")
           or refused(junk_reason, "NO_ROWS")
           or refused(junk_reason, "MISSING_TIMEZONE"),
           "a file of garbage rows is refused rather than partially read")
        mixsym = _csv(tmp, "MIXSYM.csv",
                      "timestamp,symbol,open,high,low,close,volume",
                      [r.replace(",", f",{'NIFTY' if i % 2 else 'BANKNIFTY'},", 1)
                       for i, r in enumerate(_bars(1))])
        ok(refused(ingest.inspect(mixsym, tz="Asia/Kolkata")["reject_reason"],
                   "MIXED_SYMBOLS"),
           "two instruments in one file are refused")
        rejected = registry.rejections()
        before = len(rejected)
        ingest.import_file(empty, source="smoke_bad", tz="Asia/Kolkata",
                           instrument="X")
        ok(len(registry.rejections()) == before + 1,
           "a rejected import is recorded in the registry rather than vanishing")
        ok(not os.path.isdir(store.dataset_dir("X_1M_smoke_bad_00000000")),
           "and writes no dataset directory")

        # --- §4 gaps are reported, not filled -------------------------------
        f_gap = _csv(tmp, "GAPPY.csv", OHLCV,
                     _bars(3, skip={100, 101, 102, 200}))
        gap = ingest.import_file(f_gap, source="smoke_gap", tz="Asia/Kolkata",
                                 instrument="COPPER")
        gbars = store.read_bars(gap["dataset_id"])
        ok(len(gbars) == 3 * 371, "only the bars the file had are stored")
        gspan = (store.read_manifest(gap["dataset_id"]) or {}).get("span", {})
        ok(gspan.get("missing_bars") == 12, "the missing minutes are counted")
        times = [b["time"] for b in gbars]
        ok(times == sorted(set(times)),
           "stored bars are ascending and unique, with nothing inserted")

        # --- §15 the registry ------------------------------------------------
        rec = registry.get(did) or {}
        ok(rec.get("instrument") == "BANKNIFTY", "the registry names the instrument")
        ok(rec.get("timeframe") == 1, "and the timeframe it measured")
        ok(rec.get("file_hash") == store.file_hash(f_bn), "and the source hash")
        lines = registry.all_lines()
        registry.append({"dataset_id": did, "note": "revalidated"})
        ok(len(registry.all_lines()) == len(lines) + 1,
           "the registry is append-only")
        ok(registry.all_lines()[:len(lines)] == lines,
           "and no earlier line is rewritten by a later one")

        # --- validate --------------------------------------------------------
        v = ingest.validate(did)
        ok(v["status"] == "VALID", "a freshly imported dataset validates")
        ok(v["fingerprint_recomputed"] == v["fingerprint_in_manifest"],
           "the recomputed fingerprint matches the manifest")
        ok(v["source_state"] == "SOURCE_FILE_UNCHANGED",
           "and the source file is unchanged on disk")
        with open(f_bn, "a", encoding="utf-8") as fh:
            fh.write("2026-01-20 09:15:00,1,1,1,1,1\n")
        v2 = ingest.validate(did)
        ok(v2["source_state"] == "SOURCE_FILE_HAS_CHANGED_SINCE_IMPORT",
           "a source edited after import is reported, not re-read silently")
        ok(v2["status"].startswith("VALID_BUT_"),
           "the stored bars stay valid, but the dataset stops being re-derivable")
        ok(v2["fingerprint_recomputed"] == v2["fingerprint_in_manifest"],
           "the stored dataset itself is untouched by an edit to the source")
        ok(ingest.validate("NO_SUCH_DATASET")["status"] == "UNKNOWN_DATASET",
           "validating an unknown id is an answer, not a crash")

        # --- §9/§14 the research adapter -------------------------------------
        ser = hadapter.load_series("BANKNIFTY", timeframe=1)
        ok(ser is not None and len(ser) == 6 * 375,
           "the adapter serves the imported bars as a Series")
        ok(isinstance(ser, p24data.Series),
           "and it is the existing Series type, so existing studies need no change")
        ok(list(ser.ts) == sorted(ser.ts), "the series is chronological")
        src = hadapter.series_source("BANKNIFTY")
        ok(src["origin"] == "IMPORTED_HISTORICAL_CSV", "the origin is visible")
        ok(src["dataset_fingerprint"] == man["dataset_fingerprint"],
           "and carries the dataset fingerprint the numbers came from")
        ok(src["execution_status"] == EXECUTION_UNMEASURED,
           "the adapter keeps the execution caveat attached to the series")
        ok(hadapter.load_series("NIFTY") is None or
           hadapter.series_source("NIFTY")["origin"]
           == "REPOSITORY_FIVE_YEAR_SERIES",
           "a shipped five-year series is never shadowed by an import")
        ok("NIFTY" in hadapter.shipped_instruments(),
           "NIFTY is known to be shipped, so it cannot be overridden by a file")
        ok(hadapter.load_series("NO_SUCH_INSTRUMENT") is None,
           "an instrument with no dataset returns None rather than an empty series")

        p24 = p24data.load_series("BANKNIFTY")
        ok(p24 is not None and len(p24) == 6 * 375,
           "phase24 sees the imported instrument through the same interface")
        p24n = p24data.load_series("NIFTY")
        ok(p24n is not None and len(p24n) > 100_000,
           "and still reads the shipped NIFTY file, not an import")

        # --- §11/§16 the coverage audit ---------------------------------------
        cov = hcov.report()
        qs = cov["questions"]
        ok(len(qs) == 8, "the coverage report answers exactly the eight questions")
        ok("BANKNIFTY" in qs["1_what_historical_instruments_do_we_have"],
           "Q1 names the imported instruments")
        ok("BANKNIFTY" in qs["3_which_have_only_weeks_or_months"],
           "Q3 places a six-session file in weeks, not five years")
        ok(qs["2_which_have_five_year_coverage"] == ["NONE"],
           "Q2 does not claim five years for a six-session file")
        ok(any(EXECUTION_UNMEASURED in json.dumps(g)
               for g in qs["8_which_research_gaps_are_now_closed"]),
           "Q8 attaches the execution caveat to every gap it reports on")
        nb = [g for g in qs["8_which_research_gaps_are_now_closed"]
              if g["gap"] == "NIFTY_VS_BANKNIFTY_RELATIVE_VALUE"]
        ok(len(nb) == 1, "the requested NIFTY vs BANKNIFTY pair is a declared gap")
        ok("DESCRIPTIVE_ONLY" in nb[0]["state"],
           "and six sessions of BANKNIFTY makes it descriptive, not answerable")
        ok(any(r["status"] == IMPORT_REJECTED for r in cov["datasets"]),
           "the audit shows rejected attempts as well as successes")
        ok(any("EXECUTION_UNMEASURED" in lim for lim in cov["standing_limits"]),
           "the standing limits state the execution boundary")
        ok(cov["totals"]["clean_datasets"] >= 1,
           "a clean dataset is counted as clean")
        ok(any(r.get("ohlc_quality") == QUALITY_CLEAN
               for r in cov["datasets"] if r["status"] == "IMPORTED"),
           "and the per-dataset quality column agrees")

        # --- the CLI ----------------------------------------------------------
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            rc_i = hcli.main(["inspect", f_gap, "--tz", "Asia/Kolkata",
                              "--instrument", "COPPER"])
            rc_bad = hcli.main(["inspect", f_bn])
            rc_l = hcli.main(["list"])
            rc_c = hcli.main(["coverage"])
            rc_v = hcli.main(["validate", gap["dataset_id"]])
            rc_vs = hcli.main(["validate", did])
        out = sink.getvalue()
        ok(rc_i == 0, "inspect exits 0 for a file that would import")
        ok(rc_bad == 2, "and non-zero for one that would be refused")
        ok(rc_l == 0 and rc_c == 0 and rc_v == 0, "list, coverage and validate run")
        ok(rc_vs != 0,
           "and validate refuses to exit clean on a dataset whose source moved on")
        ok("NOT EXECUTABLE QUOTES" in out,
           "the CLI states the non-executable boundary in its own output")
        ok(EXECUTION_UNMEASURED in out,
           "and prints the execution status rather than leaving it implied")
        ok(HISTORICAL_LEAD in out,
           "an import announces its promotion ceiling")

        # A refusal has to be honest about whether it left a record. A path
        # that does not resolve has no bytes and no hash, so there is nothing
        # the audit could ever re-examine — and claiming a registry line the
        # coverage report cannot show is how an audit stops being one.
        before = len(registry.rejections())
        missing = ingest.import_file(os.path.join(tmp, "NOT_A_REAL_FILE.csv"),
                                     source="MANUAL_EXPORT")
        ok(missing["status"] == IMPORT_REJECTED and "FILE_NOT_FOUND"
           in missing["reason"], "a path that does not resolve is refused")
        ok(missing["recorded"] is False
           and len(registry.rejections()) == before,
           "and is not registered, because a typo is not a dataset")
        sink2 = io.StringIO()
        with contextlib.redirect_stdout(sink2):
            rc_missing = hcli.main(["import", os.path.join(tmp, "NOPE.csv"),
                                    "--source", "MANUAL_EXPORT"])
        miss_out = sink2.getvalue()
        ok(rc_missing != 0, "the CLI exits non-zero for a file it cannot read")
        ok("nothing was written at all" in miss_out
           and "on the record" not in miss_out,
           "and says nothing was written, rather than claiming a registry line")
        sink3 = io.StringIO()
        with contextlib.redirect_stdout(sink3):
            hcli.main(["import", f_bn, "--source", "MANUAL_EXPORT"])
        ok("on the record in the registry" in sink3.getvalue(),
           "a file that was read and refused does say so, and the audit has it")

        # --- §17 no network, anywhere in this package -------------------------
        banned = ("requests", "urllib", "http.client", "httpx", "socket",
                  "aiohttp", "websocket", "smartapi", "SmartConnect",
                  "selenium", "playwright", "tradingview")
        for name in sorted(os.listdir(PKG_DIR)):
            if not name.endswith(".py"):
                continue
            src_text = open(os.path.join(PKG_DIR, name), encoding="utf-8").read()
            tree = ast.parse(src_text)
            mods: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods.add(node.module)
            ok(not any(b in mod for mod in mods for b in banned),
               f"{name} imports nothing that can reach the network")
            ok("eval(" not in src_text and "exec(" not in src_text,
               f"{name} contains no dynamic execution")

        # --- §17 non-regression: the live and production paths --------------
        for phase in ("phase41", "phase42", "phase43", "phase44"):
            pdir = os.path.join(os.path.dirname(PKG_DIR), phase)
            texts = [
                open(os.path.join(pdir, f), encoding="utf-8").read()
                for f in os.listdir(pdir) if f.endswith(".py")
            ]
            ok(not any("historical_import" in t for t in texts),
               f"{phase} does not reference the import layer at all")
        main_src = open(os.path.join(os.path.dirname(os.path.dirname(PKG_DIR)),
                                     "main.py"), encoding="utf-8").read()
        ok(main_src.count("@app.get(\"/api/historical-import") == 1
           and main_src.count("historical-import") == 1,
           "the app references the import layer from exactly one read-only route")
        ok("/api/historical-import/datasets" in main_src,
           "and that route is the datasets audit")
        ok("def historical_import_datasets" in main_src
           and "@app.post" not in main_src.split(
               "def historical_import_datasets")[0][-400:],
           "the import layer has no POST route: an import is a CLI an operator runs")
        pkg_texts = {
            name: open(os.path.join(PKG_DIR, name), encoding="utf-8").read()
            for name in os.listdir(PKG_DIR) if name.endswith(".py")
        }
        for name, text in pkg_texts.items():
            ok("place_order" not in text and "placeOrder" not in text,
               f"{name} cannot reach an order path")
            ok("phase42" not in text and "phase44" not in text,
               f"{name} does not touch the live gate or the dormant arm")
        ok(all("promote(" not in t and "promotion_ceiling\"] =" not in t
               for t in pkg_texts.values()),
           "nothing in the package promotes anything")

        # --- §18 the panel states the boundary before it states a number -----
        panel = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(PKG_DIR))),
            "..", "frontend", "components", "HistoricalDatasetsPanel.tsx")
        if os.path.exists(panel):
            ptext = open(panel, encoding="utf-8").read()
            ok("HISTORICAL CANDLE DATA" in ptext and
               "NOT EXECUTABLE QUOTES" in ptext,
               "the panel says what the data is and what it is not")
            ok("EXECUTION_UNMEASURED" in ptext and HISTORICAL_LEAD in ptext,
               "and carries the execution caveat and the promotion ceiling")
            ok("fetch(" not in ptext and "post" not in ptext.lower(),
               "the panel only reads through the api module, and never imports")
            ok(all(v in ptext for v in (QUALITY_CLEAN, ELIGIBLE_FULL,
                                        ELIGIBLE_SHORT, ELIGIBLE_NONE)),
               "and colours the labels the backend actually emits, not shorthand")

        # --- the eligibility ceiling -----------------------------------------
        ok(res["research_eligibility"] != ELIGIBLE_NONE,
           "a clean six-session file is usable for descriptive candle research")
        ok("DESCRIPTIVE_ONLY" in res["research_eligibility"],
           "but is labelled descriptive-only, so it cannot carry a split")
        ok(res["promotion_ceiling"] == HISTORICAL_LEAD,
           "and its ceiling is HISTORICAL_LEAD regardless of how clean it is")
    finally:
        store.data_dir = original  # type: ignore[assignment]

    print(f"\nHISTORICAL IMPORT SMOKE — {PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
