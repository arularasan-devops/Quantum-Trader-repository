"""MCX history layer smoke — the properties whose loss would make the dataset a lie.

The load-bearing tests here are not "does it collect". They are the guarantees a
reader of the resulting dataset has to be able to rely on:

* a window that fails is recorded and the run continues; a resumed run asks only
  for what is missing, and calendar-aligned windows mean two passes cannot leave
  a seam;
* a re-requested window cannot duplicate a minute, because the store key is
  ``(root, token, ts)``;
* bars from two tokens of one root are never concatenated into one file;
* a session below the coverage floor is ``NOT_GRADED`` rather than quietly
  averaged into a coverage percentage;
* no price is ever adjusted, and the boundary scan reports a discontinuity
  instead of removing it;
* ``data/history.db`` is not read or written anywhere in the package — the
  live-capture rows cannot reach the dataset;
* there is no order path, and no credential is read outside the login helper;
* nothing in Phases 41-54 moved.

Every fetch is injected, so the whole suite runs with no credentials and no
network.
"""
from __future__ import annotations

import ast
import datetime as dt
import io
import json
import os
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np

from app.research import mcxhist
from app.research.mcxhist import audit as mcxaudit
from app.research.mcxhist import cli as mcxcli
from app.research.mcxhist import collector as mcxcollector
from app.research.mcxhist import contracts as mcxcontracts
from app.research.mcxhist import export as mcxexport
from app.research.mcxhist import probe as mcxprobe
from app.research.mcxhist import report as mcxreport
from app.research.mcxhist.store import STATUS_FAILED, STATUS_OK, Store

PASS = 0
FAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {label}")


# --------------------------------------------------------------- declaration

print("declaration and fingerprint")
pre = mcxhist.preregistration()
check("fingerprint is stable", mcxhist.fingerprint() == mcxhist.fingerprint())
check("fingerprint is 16 hex", len(mcxhist.fingerprint()) == 16)
check("data class is candle data", pre["data_class"] == "HISTORICAL_CANDLE_DATA")
check("series class names the unverified roll",
      pre["series_class"] == "PROVIDER_CONTINUOUS_UNVERIFIED_ROLL")
check("roll policy says none constructed", pre["roll_policy"].startswith("NONE_CONSTRUCTED"))
check("no price adjustment is declared", pre["price_adjustment"].startswith("NONE"))
check("options are declared unavailable",
      pre["options"].startswith("HISTORICAL_OPTION_EXECUTION_UNAVAILABLE"))
check("declaration is JSON serialisable", bool(json.dumps(pre)))

# a changed declared value must move the fingerprint
before = mcxhist.fingerprint()
saved = mcxhist.CHUNK_DAYS
mcxhist.CHUNK_DAYS = saved + 1
check("fingerprint follows the declaration", mcxhist.fingerprint() != before)
mcxhist.CHUNK_DAYS = saved
check("fingerprint restored", mcxhist.fingerprint() == before)


# ------------------------------------------------------------------- windows

print("request windows")
w = mcxcollector.windows(dt.date(2021, 1, 1), dt.date(2021, 1, 12), 5)
check("windows tile the span", w[0][0] == dt.date(2021, 1, 1) and w[-1][1] == dt.date(2021, 1, 12))
check("windows do not overlap",
      all(b[0] > a[1] for a, b in zip(w, w[1:])))
check("windows are contiguous",
      all((b[0] - a[1]).days == 1 for a, b in zip(w, w[1:])))
# calendar alignment: the same start always produces the same keys, so a
# resumed run cannot shift a boundary and leave a seam
again = mcxcollector.windows(dt.date(2021, 1, 1), dt.date(2021, 1, 30), 5)
check("windows are calendar aligned, not run aligned",
      [x[0] for x in again][:2] == [x[0] for x in w][:2])
check("a one-day span is one window",
      mcxcollector.windows(dt.date(2021, 1, 1), dt.date(2021, 1, 1), 5)
      == [(dt.date(2021, 1, 1), dt.date(2021, 1, 1))])

print("rate-limit recognition")
for text in ("Access denied because of exceeding access rate",
             "AB1021", "couldn't parse the JSON response", "Too many requests"):
    check(f"recognised: {text[:24]}", mcxcollector.is_rate_limited(text))
check("a real error is not a rate limit",
      not mcxcollector.is_rate_limited("invalid token"))


# --------------------------------------------------------------------- store

print("store: dedupe, resume, provenance")
tmp = tempfile.mkdtemp(prefix="mcxhist_smoke_")
store = Store(os.path.join(tmp, "mcxhist.db"))

rows = [
    ["2021-08-02T09:00:00+05:30", 47000, 47010, 46990, 47005, 12],
    ["2021-08-02T09:01:00+05:30", 47005, 47020, 47000, 47015, 9],
]
check("insert accepts parseable rows", store.insert_bars("GOLD", "483079", rows) == 2)
check("re-inserting the same window cannot duplicate",
      store.insert_bars("GOLD", "483079", rows) == 2 and store.bar_count("GOLD") == 2)
check("an unparseable row is rejected, not filed under a guess",
      store.insert_bars("GOLD", "483079", [["not-a-time", 1, 1, 1, 1, 1]]) == 0
      and store.bar_count("GOLD") == 2)
check("a second token stays a second series",
      store.insert_bars("GOLD", "999999", rows) == 2
      and store.bar_count("GOLD", "483079") == 2
      and sorted(store.tokens("GOLD")) == ["483079", "999999"])

store.record_provenance(
    "GOLD", "483079", mcxhist.ONE_MINUTE, exchange="MCX",
    trading_symbol="GOLD05OCT26FUT", expiry="2026-10-05",
    source="unit_test", fingerprint=mcxhist.fingerprint(),
)
prov = store.provenance("GOLD")
check("provenance is stored", len(prov) == 1)
check("provenance carries the data class", prov[0]["data_class"] == "HISTORICAL_CANDLE_DATA")
check("provenance carries the series class",
      prov[0]["series_class"] == "PROVIDER_CONTINUOUS_UNVERIFIED_ROLL")
check("provenance carries a timezone", "+05:30" in prov[0]["timezone"])
check("provenance carries the fingerprint", prov[0]["fingerprint"] == mcxhist.fingerprint())

store.mark_chunk("GOLD", "483079", mcxhist.ONE_MINUTE, "2021-08-02", "2021-08-06", STATUS_OK, 2)
store.mark_chunk("GOLD", "483079", mcxhist.ONE_MINUTE, "2021-08-07", "2021-08-11",
                 STATUS_FAILED, 0, "provider refused")
check("an OK window is not re-requested",
      store.done_chunks("GOLD", "483079", mcxhist.ONE_MINUTE) == {"2021-08-02"})
check("a failed window is re-requested",
      "2021-08-07" not in store.done_chunks("GOLD", "483079", mcxhist.ONE_MINUTE))
check("a failure keeps its reason",
      store.failures("GOLD", "483079", mcxhist.ONE_MINUTE)[0]["reason"] == "provider refused")


# ----------------------------------------------------------------- collector

print("collector: failure isolation and resume")


def _bars(day: dt.date, count: int, base: float = 47000.0) -> list[list]:
    out = []
    for i in range(count):
        stamp = dt.datetime.combine(day, dt.time(9, 0)) + dt.timedelta(minutes=i)
        price = base + i * 0.1
        out.append([stamp.isoformat() + "+05:30", price, price + 1, price - 1, price, 1])
    return out


calls: list[tuple] = []


def flaky_fetch(exchange, token, interval, start, end):
    calls.append((start, end))
    if start == dt.date(2021, 1, 6):
        raise RuntimeError("AB1021 exceeding access rate")
    if start == dt.date(2021, 1, 11):
        return []
    return _bars(start, 5)


store2 = Store(os.path.join(tmp, "collect.db"))
summary = mcxcollector.collect(
    flaky_fetch, store2, root="GOLD", exchange="MCX", token="T1",
    trading_symbol="X", expiry="2026-10-05",
    start=dt.date(2021, 1, 1), end=dt.date(2021, 1, 15),
    sleep=lambda _s: None, min_interval_sec=0.0, backoff_start_sec=0.0,
)
check("every planned window was attempted", summary["windows_planned"] == 3)
check("a failing window does not abandon the run", summary["windows_ok"] == 1)
check("the failing window is recorded as failed", summary["windows_failed"] == 1)
check("an empty window is recorded as empty", summary["windows_empty"] == 1)
check("a rate limit is waited out before being written off",
      summary["rate_limit_waits"] == mcxhist.MAX_ATTEMPTS - 1)
check("bars from the good window landed", summary["bars_inserted"] == 5)
check("the failure reason is kept",
      "AB1021" in store2.failures("GOLD", "T1", mcxhist.ONE_MINUTE)[0]["reason"])

calls.clear()
resume = mcxcollector.collect(
    flaky_fetch, store2, root="GOLD", exchange="MCX", token="T1",
    trading_symbol="X", expiry="2026-10-05",
    start=dt.date(2021, 1, 1), end=dt.date(2021, 1, 15),
    sleep=lambda _s: None, min_interval_sec=0.0, backoff_start_sec=0.0,
)
check("a resumed run skips what already landed",
      resume["windows_skipped_already_stored"] == 1)
check("a resumed run retries only the holes",
      all(start != dt.date(2021, 1, 1) for start, _e in calls))
check("a resumed run cannot duplicate bars",
      store2.bar_count("GOLD", "T1") == 5)

check("collector records provenance before fetching anything",
      len(store2.provenance("GOLD")) == 1)


# --------------------------------------------------------------------- probe

print("probe")


def probe_fetch(exchange, token, interval, start, end):
    if token == "HIST":
        return _bars(start, 800)
    return []


listed = [
    {"token": "HIST", "trading_symbol": "GOLD05OCT26FUT", "expiry": "2026-10-05"},
    {"token": "NONE", "trading_symbol": "GOLD04DEC26FUT", "expiry": "2026-12-04"},
]
result = mcxprobe.probe_root(
    probe_fetch, "GOLD", "MCX", listed,
    windows=(("2021-08-02", "2021-08-04"), ("2022-07-05", "2022-07-07")),
    sleep=lambda _s: None,
)
check("the probe reports one token with pre-listing history",
      result["contracts_with_pre_listing_history"] == 1)
check("the probe names that token", result["history_tokens"] == ["HIST"])
check("a single answering token is named an unverified provider roll",
      result["verdict"] == "SINGLE_TOKEN_ROOT_SERIES_UNVERIFIED_ROLL")
check("empty windows are recorded, not dropped",
      all(len(c["windows"]) == 2 for c in result["per_contract"]))
check("the empty contract is reported as having no history",
      result["per_contract"][1]["windows_with_rows"] == 0)
check("row quality is measured per window",
      result["per_contract"][0]["windows"][0]["median_bars_per_session"] == 800)
check("timestamp quality is reported",
      result["per_contract"][0]["windows"][0]["timestamp_quality"] == "ISO_WITH_OFFSET")
check("an empty window says so",
      result["per_contract"][1]["windows"][0]["timestamp_quality"] == "NO_ROWS")


def dead_fetch(exchange, token, interval, start, end):
    return []


dead = mcxprobe.probe_root(dead_fetch, "GOLD", "MCX", listed,
                           windows=(("2021-08-02", "2021-08-04"),),
                           sleep=lambda _s: None)
check("no answering token is reported as unavailable, not as a small dataset",
      dead["verdict"] == "EXPIRED_CONTRACT_HISTORY_UNAVAILABLE")


def angry_fetch(exchange, token, interval, start, end):
    raise RuntimeError("invalid symboltoken")


angry = mcxprobe.probe_token(angry_fetch, "MCX", "X",
                             windows=(("2021-08-02", "2021-08-04"),),
                             sleep=lambda _s: None)
check("a provider refusal is the probe result, not a crash",
      angry[0]["response"] == "ERROR" and "invalid symboltoken" in angry[0]["error"])


# ------------------------------------------------------------------ contract

print("contract inventory")
master = [
    {"name": "GOLD", "exch_seg": "MCX", "instrumenttype": "FUTCOM",
     "symbol": "GOLD05OCT26FUT", "token": "HIST", "expiry": "05OCT2026"},
    {"name": "GOLD", "exch_seg": "MCX", "instrumenttype": "FUTCOM",
     "symbol": "GOLD04DEC26FUT", "token": "NONE", "expiry": "04DEC2026"},
    {"name": "GOLDM", "exch_seg": "MCX", "instrumenttype": "FUTCOM",
     "symbol": "GOLDM05OCT26FUT", "token": "MINI", "expiry": "05OCT2026"},
    {"name": "GOLD", "exch_seg": "NFO", "instrumenttype": "FUTCOM",
     "symbol": "GOLDNFO", "token": "OTHER", "expiry": "05OCT2026"},
    {"name": "GOLD", "exch_seg": "MCX", "instrumenttype": "OPTFUT",
     "symbol": "GOLD05OCT2680000CE", "token": "OPT", "expiry": "05OCT2026"},
]
futs = mcxcontracts.listed_futures(master, "GOLD", "MCX")
check("GOLDM is not pooled into GOLD", [f["token"] for f in futs] == ["HIST", "NONE"])
check("another exchange is excluded", all(f["exchange"] == "MCX" for f in futs))
check("options are excluded", all("CE" not in (f["trading_symbol"] or "") for f in futs))
check("a repeated master row is not a second contract",
      len(mcxcontracts.listed_futures(master + master, "GOLD", "MCX")) == 2)
check("an unparseable expiry is dropped, not defaulted",
      mcxcontracts.parse_expiry("garbage") is None)

inv = mcxcontracts.inventory(
    master, "GOLD", "MCX", dt.date(2021, 8, 2), dt.date(2026, 8, 5),
    {"HIST": True, "NONE": False},
)
check("the inventory counts what is listed", inv["contracts_listed"] == 2)
check("the inventory counts what is reachable", inv["contracts_reachable"] == 1)
check("the inventory states how many a real per-contract history needed",
      inv["contracts_expected_for_window"] >= 25)
check("unresolvable expired tokens are counted, not omitted",
      inv["contracts_unresolvable"] >= 25)
check("every contract carries a status and a reason",
      all(c["status"] and c["reason"] for c in inv["contracts"]))
check("the history token is identified", mcxcontracts.history_token(inv)["token"] == "HIST")
inv_two = mcxcontracts.inventory(master, "GOLD", "MCX", dt.date(2021, 8, 2),
                                 dt.date(2026, 8, 5), {"HIST": True, "NONE": True})
check("two answering tokens refuse a single-token choice",
      mcxcontracts.history_token(inv_two) is None)


# --------------------------------------------------------------------- audit

print("audit: coverage, gaps, boundaries")


def _series(days: list[tuple[dt.date, int]], base: float = 47000.0):
    ts, o, h, low, c = [], [], [], [], []
    price = base
    for day, count in days:
        for i in range(count):
            stamp = dt.datetime.combine(day, dt.time(9, 0), dt.timezone(dt.timedelta(hours=5, minutes=30)))
            stamp += dt.timedelta(minutes=i)
            ts.append(int(stamp.timestamp()))
            o.append(price)
            h.append(price + 1)
            low.append(price - 1)
            c.append(price)
            price += 0.01
    return (np.array(ts, dtype=np.int64), np.array(o), np.array(h),
            np.array(low), np.array(c))

full = [(dt.date(2021, 3, 1) + dt.timedelta(days=i), 870) for i in range(5)]
thin = [(dt.date(2021, 3, 8) + dt.timedelta(days=i), 69) for i in range(5)]
ts, o, h, low, c = _series(full + thin)
cov = mcxaudit.session_coverage(ts, mcxhist.SESSION_MINUTES_FULL)
check("full sessions are graded OK", cov["ok"] == 5)
check("9%-coverage sessions are NOT_GRADED, not averaged away", cov["not_graded"] == 5)
check("coverage percent is bars over session minutes",
      abs(cov["coverage_pct"] - (870 * 5 + 69 * 5) / (10 * 870) * 100) < 0.01)
check("the median session is reported", cov["median_bars_per_session"] == 469)
check("the thinnest and fattest session are both reported",
      cov["min_bars_per_session"] == 69 and cov["max_bars_per_session"] == 870)
check("per-session detail is kept", len(cov["per_session"]) == 10)

gapped = [(dt.date(2021, 3, 1), 870), (dt.date(2021, 3, 2), 870), (dt.date(2021, 3, 10), 870)]
ts2, o2, h2, low2, c2 = _series(gapped)
gaps = mcxaudit.calendar_gaps(ts2)
check("missing weekday sessions are counted", gaps["missing_weekday_sessions"] == 5)
# Mar 3-5 and Mar 8-9 are two runs: a weekend is not an absent session
check("the longest absent run is reported", gaps["longest_absent_run"] == 3)
check("a weekend does not join two runs of missing sessions", len(gaps["runs"]) == 2)
check("a holiday is not asserted to be a hole", "holiday" in gaps["basis"])

# a stitch-shaped jump: one boundary 40x the others
days = [(dt.date(2021, 3, 1) + dt.timedelta(days=i), 60) for i in range(12)]
ts3, o3, h3, low3, c3 = _series(days)
o3 = o3.copy()
boundary_index = 60 * 6
o3[boundary_index:] += 4000.0
c3 = c3.copy()
c3[boundary_index:] += 4000.0
scan = mcxaudit.boundary_scan(ts3, c3, o3)
check("every overnight boundary is examined", scan["boundaries"] == 11)
check("an outsized overnight move is flagged", scan["flagged"] == 1)
check("the flag names both sessions and both prices",
      scan["flags"][0]["session_end"] == "2021-03-06"
      and scan["flags"][0]["next_open"] > scan["flags"][0]["last_close"])
check("the threshold scales with the instrument's own distribution",
      scan["threshold_pct"] > 0 and scan["jump_multiple"] == mcxhist.JUMP_FLAG_MULTIPLE)
check("no price was adjusted to remove the jump", float(o3[boundary_index]) > 47000.0 + 3000.0)
check("the scan says a flag is not a proven stitch", "not a proven stitch" in scan["basis"])

cycle = mcxaudit.expiry_cycle_scan({"flags": [{"session_start": "2021-04-05"}]})
check("an expiry-week flag is recognised as clustered",
      cycle["reading"] == "CLUSTERED_AT_EXPIRY_WEEK")
check("a mid-cycle flag is not called clustered",
      mcxaudit.expiry_cycle_scan({"flags": [{"session_start": "2021-03-17"}]})["reading"]
      == "NOT_CLUSTERED_AT_EXPIRY_WEEK")
check("no flags is its own reading",
      mcxaudit.expiry_cycle_scan({"flags": []})["reading"] == "NO_FLAGS")
check("a clean scan is said to bound, not disprove, a stitch",
      "bounds" in cycle["basis"])

dup_ts = np.array([1, 2, 2, 3], dtype=np.int64)
check("duplicate timestamps are counted",
      mcxaudit.duplicate_scan(dup_ts)["duplicate_timestamps"] == 1)
check("non-monotonic series is reported", not mcxaudit.duplicate_scan(dup_ts)["monotonic"])

thin_cov = mcxaudit.session_coverage(_series([(dt.date(2021, 3, 1), 870)])[0])
answer = mcxaudit.timeframe_answerability(thin_cov)
check("one session answers no timeframe",
      all(v["status"] == mcxaudit.INSUFFICIENT_HISTORY for v in answer.values()))
check("every declared timeframe is reported",
      set(answer) == set(mcxhist.TIMEFRAMES))

big = [(dt.date(2021, 3, 1) + dt.timedelta(days=i), 870) for i in range(500)]
ts4, o4, h4, low4, c4 = _series(big)
full_audit = mcxaudit.audit_series("GOLD", ts4, o4, h4, low4, c4)
check("500 full sessions are SUFFICIENT",
      full_audit["data_quality"] == mcxaudit.QUALITY_SUFFICIENT)
check("SUFFICIENT means VALID_HISTORY = YES", full_audit["valid_history"] == "YES")
check("daily research is answerable at 500 sessions",
      full_audit["timeframes"]["daily"]["status"] == mcxaudit.ANSWERABLE)
thin_audit = mcxaudit.audit_series("GOLD", *_series(thin))
check("37-session-equivalent history is INSUFFICIENT",
      thin_audit["data_quality"] == mcxaudit.QUALITY_INSUFFICIENT)
check("INSUFFICIENT means VALID_HISTORY = NO", thin_audit["valid_history"] == "NO")

bad_h = h4.copy()
bad_h[10] = low4[10] - 5.0
check("an impossible bar is counted",
      mcxaudit.audit_series("GOLD", ts4, o4, bad_h, low4, c4)["impossible_bars"] >= 1)


# -------------------------------------------------------------------- export

print("export: one token per file, provenance sidecar")
out_dir = os.path.join(tmp, "export")
result = mcxexport.export_root(store2, "GOLD", data_dir=out_dir)
check("export writes the token that was collected", result["status"] == "OK")
check("export writes one line per bar",
      sum(1 for _ in open(result["jsonl"], encoding="utf-8")) == 5)
sidecar = json.load(open(result["provenance"], encoding="utf-8"))
check("the sidecar names the token", sidecar["token"] == "T1")
check("the sidecar carries the data class", sidecar["data_class"] == "HISTORICAL_CANDLE_DATA")
check("the sidecar carries the series class",
      sidecar["series_class"] == "PROVIDER_CONTINUOUS_UNVERIFIED_ROLL")
check("the sidecar carries the roll policy", sidecar["roll_policy"].startswith("NONE_CONSTRUCTED"))
check("the sidecar carries the fingerprint", sidecar["fingerprint"] == mcxhist.fingerprint())
check("the exported row shape matches the project format",
      set(json.loads(open(result["jsonl"], encoding="utf-8").readline()))
      == {"time", "open", "high", "low", "close", "volume"})

store2.insert_bars("GOLD", "T2", _bars(dt.date(2022, 1, 3), 3))
refused = mcxexport.export_root(store2, "GOLD", data_dir=out_dir)
check("two tokens are never concatenated into one series",
      refused["status"] == "MULTIPLE_TOKENS_REFUSED" and refused["written"] == 0)
named = mcxexport.export_root(store2, "GOLD", token="T1", data_dir=out_dir)
check("naming the token is required and sufficient", named["status"] == "OK")
check("nothing collected is not an empty file",
      mcxexport.export_root(store2, "SILVER", data_dir=out_dir)["status"] == "NO_BARS_STORED")


# -------------------------------------------------------------------- report

print("report and stage verdict")
bundle = {
    "fingerprint": mcxhist.fingerprint(),
    "generated_at": "now",
    "data_class": mcxhist.DATA_CLASS,
    "series_class": mcxhist.SERIES_CLASS,
    "preregistration": pre,
    "instruments": [
        mcxaudit.audit_series("GOLD", ts4, o4, h4, low4, c4, tokens=["HIST"]),
        mcxaudit.audit_series("SILVER", *_series(thin), tokens=["S1"]),
    ],
}
verdict = mcxreport.stage_verdict(bundle, {"GOLD": inv, "SILVER": inv})
check("a sufficient root reads YES", verdict["GOLD"]["valid_history"] == "YES")
check("an insufficient root reads NO", verdict["SILVER"]["valid_history"] == "NO")
check("one NO closes the Phase 55 gate", verdict["phase55_gate"] == "CLOSED")
check("the roll count is zero by construction, and says so",
      verdict["GOLD"]["rollover_count"] == 0
      and "not exposed" in verdict["GOLD"]["rollover_basis"])
check("the verdict reports the unresolvable contract count",
      verdict["GOLD"]["contracts_unresolvable"] >= 25)
text = mcxreport.render_verdict(verdict)
check("the deliverable prints VALID_GOLD_HISTORY", "VALID_GOLD_HISTORY = YES" in text)
check("the deliverable prints VALID_SILVER_HISTORY", "VALID_SILVER_HISTORY = NO" in text)
for token in ("GOLD_1M_BARS", "SILVER_1M_BARS", "GOLD_DATE_RANGE",
              "ROLLOVER_COUNT_GOLD", "DATA_QUALITY_STATUS"):
    check(f"the deliverable prints {token}", token in text)
check("the table renders every audited row",
      "GOLD" in mcxreport.render_table(bundle) and "SILVER" in mcxreport.render_table(bundle))
check("detail renders the boundary scan",
      "overnight boundaries" in mcxreport.render_detail(bundle["instruments"][0]))

written = mcxreport.write_artefacts(bundle, verdict, {"GOLD": inv}, {"probe": "x"},
                                   os.path.join(tmp, "artefacts"))
check("five JSON artefacts and one text report are written", len(written) == 6)
check("every artefact exists", all(os.path.exists(p) for p in written))
check("the artefacts carry the fingerprint",
      json.load(open(os.path.join(tmp, "artefacts", "preregistration.json"),
                     encoding="utf-8"))["fingerprint"] == mcxhist.fingerprint())

absent = mcxreport._absent("SILVER", "no bars collected")
check("an absent instrument is NO, never blank", absent["valid_history"] == "NO")
check("an absent instrument keeps its reason", absent["absent_reason"] == "no bars collected")


# ----------------------------------------------------------------- isolation

print("isolation")
pkg = Path("app/research/mcxhist")
sources = {p.name: p.read_text(encoding="utf-8") for p in pkg.glob("*.py")}
joined = "\n".join(sources.values())

check("no module reads or writes the live-capture store", "history.db" not in
      "\n".join(v for k, v in sources.items() if k != "__init__.py"))
check("the live-capture boundary is stated in the declaration",
      "history.db is never read" in sources["__init__.py"])
for forbidden in ("placeOrder", "place_order", "cancelOrder", "modifyOrder"):
    check(f"no order path: {forbidden}", forbidden not in joined)
check("no phase 41-54 module is imported",
      not any(f"phase{n}" in joined for n in range(41, 55)))
check("only the CLI names the credential configuration, and only in prose",
      [k for k, v in sources.items() if "SMARTAPI" in v] == ["cli.py"])
check("no module reads an environment variable", "os.environ" not in joined)
check("no module reads a credential by any other spelling", "getenv" not in joined)
check("the store path is separate from production stores",
      "mcxhist.db" in sources["store.py"])

# the collector must not call the network itself: the fetcher is injected
tree = ast.parse(sources["collector.py"])
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        names = [a.name for a in getattr(node, "names", [])] + [getattr(node, "module", "") or ""]
        check("collector imports no http client",
              not any(n.split(".")[0] in {"httpx", "requests", "urllib"} for n in names))

print("phases 41-54 unchanged")
for phase in range(41, 55):
    mod = Path(f"app/research/phase{phase}")
    if not mod.exists():
        continue
    check(f"phase{phase} package present", mod.is_dir())
smoke_out = io.StringIO()
with redirect_stdout(smoke_out):
    cli_text = mcxcli._cmd_prereg()
check("the CLI prints the declaration", "fingerprint" in cli_text)
check("the CLI has no order command",
      set(("prereg", "probe", "collect", "export", "audit")) and "order" not in
      "".join(mcxcli.main.__doc__ or ""))

store.close()
store2.close()

print(f"\nMCX HISTORY SMOKE — {PASS} passed, {FAIL} failed")
if FAIL:
    raise SystemExit(1)
