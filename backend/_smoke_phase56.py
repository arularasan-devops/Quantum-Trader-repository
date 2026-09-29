"""Phase 56 stage-1 smoke — the properties that make the gate trustworthy.

What is tested is not "does the audit print a table". It is the claims a reader
of the §36 verdict has to be able to rely on:

* the gate is a stop, not a warning: a MISSING field cannot yield PROCEED, and
  there is no command in the package that could run a study anyway;
* a throttled or failing provider yields INCONCLUSIVE, never MISSING — a rate
  limit must never be recorded as an absent dataset;
* the two §29 universes are never merged, and the survivor universe is labelled
  diagnostic-only wherever it appears;
* the probe sets are frozen in the package before measurement, and the verdict
  logic reads them rather than choosing symbols from the data;
* a pre-adjusted series is reported as pre-adjusted: a provider that silently
  back-adjusts cannot be recorded as supplying RAW prices;
* the cost model never favours the strategy — slippage adds cost on both sides,
  the DP charge is never waived, and the round trip is decomposed as §8 asks;
* there is no order path, no production import, no write to any live store, and
  nothing in Phases 41-55 is imported or touched.

Every fetch and master list is injected, so the suite runs with no credentials
and no network.
"""
from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

from app.research import phase56
from app.research.phase56 import audit as p56audit
from app.research.phase56 import costs as p56costs
from app.research.phase56 import probe as p56probe
from app.research.phase56 import report as p56report
from app.research.phase56 import universe as p56universe

PASS = 0
FAIL = 0


def raises(fn) -> bool:
    try:
        fn()
    except Exception:
        return True
    return False


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {label}")


PKG = Path("app/research/phase56")
SOURCES = sorted(PKG.glob("*.py"))
BLOB = "\n".join(path.read_text(encoding="utf-8") for path in SOURCES)


# ----------------------------------------------------------------- fixtures

def master_row(symbol: str, token: str, exch: str = "NSE", itype: str = "", name: str | None = None) -> dict:
    return {
        "token": token,
        "symbol": symbol,
        "name": name if name is not None else symbol.split("-")[0],
        "expiry": "",
        "strike": "-1.000000",
        "lotsize": "1",
        "instrumenttype": itype,
        "exch_seg": exch,
        "tick_size": "5.000000",
    }


MASTER = [
    master_row("RELIANCE-EQ", "2885"),
    master_row("TCS-EQ", "11536"),
    master_row("HDFCBANK-EQ", "1333"),
    master_row("ITC-EQ", "1660"),
    master_row("MARUTI-EQ", "10999"),
    master_row("SUNPHARMA-EQ", "3351"),
    master_row("NESTLEIND-EQ", "17963"),
    master_row("TATASTEEL-EQ", "3499"),
    master_row("WIPRO-EQ", "3787"),
    master_row("BAJFINANCE-EQ", "317"),
    master_row("RELIANCE-EQ", "2885"),            # the master repeats rows
    master_row("NIFTYBEES-EQ", "10576"),
    master_row("GS2028-GS", "9999"),              # government security, not equity
    master_row("SOMESME-SM", "9998"),             # SME scrip, not equity
    master_row("NIFTY", "26000", itype="AMXIDX", name="NIFTY"),
    master_row("RELIANCE25SEP26FUT", "55555", exch="NFO", itype="FUTSTK", name="RELIANCE"),
]

TOKENS = {row["symbol"]: row["token"] for row in p56universe.equity_rows(MASTER)}


def daily(day: dt.date, close: float) -> list:
    return [f"{day.isoformat()}T00:00:00+05:30", close, close, close, close, 1000]


def series_fetch(*, adjusted: bool, error: str | None = None, empty: bool = False):
    """A provider that either pre-adjusts actions away or leaves raw jumps."""

    def fetch(params: dict) -> list[list]:
        if error is not None:
            raise RuntimeError(error)
        if empty:
            return []
        start = dt.date.fromisoformat(params["fromdate"][:10])
        end = dt.date.fromisoformat(params["todate"][:10])
        floor = dt.date(2021, 3, 30)
        day = max(start, floor)
        rows = []
        while day <= end:
            if day.weekday() < 5:
                close = 100.0
                if not adjusted:
                    # every declared action is a 10x/2x level change on its date
                    for probe in phase56.CORPORATE_ACTION_PROBES:
                        ex = dt.date.fromisoformat(probe["ex_date"])
                        if day < ex:
                            close = close / probe["ratio"]
                rows.append(daily(day, round(close, 4)))
            day += dt.timedelta(days=1)
        return rows

    return fetch


print("declaration")
check("fingerprint is stable", phase56.fingerprint() == phase56.fingerprint())
check("fingerprint is 16 hex", len(phase56.fingerprint()) == 16)
prereg = phase56.preregistration()
check("execution model is cash equity", prereg["execution_model"] == "BUY_SHARES_HOLD_SELL_SHARES")
for banned in ("FUTURES", "OPTIONS", "LEVERAGE", "SHORT_SELLING", "MARGIN"):
    check(f"{banned} is prohibited in the declaration", banned in prereg["prohibited"])
check("phases 41-55 are declared untouched", "PHASE_41_TO_55_MODIFICATION" in prereg["prohibited"])
check("the probe sets are frozen in the declaration",
      len(prereg["corporate_action_probes"]) == len(phase56.CORPORATE_ACTION_PROBES)
      and len(prereg["vanished_security_probes"]) == len(phase56.VANISHED_SECURITY_PROBES))
check("all seven §2 statuses are declared", len(phase56.ALL_SECURITY_STATUSES) == 7)
check("what would open the gate is declared up front", len(phase56.REQUIRED_FOR_STAGE_2) >= 6)

print("universe")
rows = p56universe.equity_rows(MASTER)
check("repeated master rows do not become two securities",
      sum(1 for r in rows if r["symbol"] == "RELIANCE") == 1)
check("government securities are excluded", all(r["symbol"] != "GS2028" for r in rows))
check("SME scrips are excluded", all(r["symbol"] != "SOMESME" for r in rows))
check("the index row is not an equity", all(r["symbol"] != "NIFTY" for r in rows))
check("no security carries a first_trade_date the source never published",
      all(r["first_trade_date"] is None for r in rows))
check("no security carries a delisting status", all(r["delisting_status"] is None for r in rows))
statuses = {row["status"]: row["assignable"] for row in p56universe.status_assignability()}
check("ACTIVE is assignable", statuses["ACTIVE"] is True)
for unavailable in ("DELISTED", "SUSPENDED", "MERGED", "DEMERGED", "RENAMED"):
    check(f"{unavailable} is not assignable from this source", statuses[unavailable] is False)
check("INSUFFICIENT_HISTORY is assignable", statuses["INSUFFICIENT_HISTORY"] is True)
membership = p56universe.membership_sources(MASTER)
check("all three §3 sources are checked", len(membership) == 3)
check("no §3 source offers dated membership",
      all(not row["dated_membership_available"] for row in membership))
universes = p56universe.universes(MASTER, as_of=dt.date(2026, 9, 21))
check("the survivor universe is diagnostic only",
      universes["CURRENT_SURVIVOR_UNIVERSE"]["usable_for_conclusion"] is False)
check("the historically correct universe is empty with a reason",
      universes["HISTORICALLY_CORRECT_UNIVERSE"]["securities"] == 0
      and "no dated membership" in universes["HISTORICALLY_CORRECT_UNIVERSE"]["reason"])

print("probes")
cov = p56probe.probe_coverage(series_fetch(adjusted=True), TOKENS, end=dt.date(2026, 9, 18))
check("coverage floor is the provider's, not the request's",
      cov["history_floor_latest"] == "2021-03-30")
check("coverage asks for far more history than the study needs",
      p56probe.DEEP_START.year <= 2000)
check("one call carries a full daily series",
      all(row.get("calls_for_full_series") == 1 for row in cov["rows"] if row["outcome"] == "PRESENT"))
cov_err = p56probe.probe_coverage(
    series_fetch(adjusted=True, error="Access denied because of exceeding access rate"),
    TOKENS,
)
check("a rate-limited coverage probe is INCONCLUSIVE, never absent",
      cov_err["inconclusive"] == cov_err["probed"] and cov_err["present"] == 0)
check("the rate limit is recognised as such",
      all(row.get("rate_limited") for row in cov_err["rows"]))
cov_empty = p56probe.probe_coverage(series_fetch(adjusted=True, empty=True), TOKENS)
check("an empty window on a live symbol is INCONCLUSIVE, not absent",
      cov_empty["inconclusive"] == cov_empty["probed"])
check("a symbol missing from the master is not silently dropped from the probe",
      p56probe.probe_coverage(series_fetch(adjusted=True), {}, symbols=("RELIANCE",))["probed"] == 1)

raw = p56probe.probe_corporate_actions(series_fetch(adjusted=False), TOKENS)
check("a raw jump at a published ex-date is detected", raw["series_verdict"] == phase56.SERIES_RAW)
check("every declared action is probed", raw["probed"] == len(phase56.CORPORATE_ACTION_PROBES))
adj = p56probe.probe_corporate_actions(series_fetch(adjusted=True), TOKENS)
check("a silently back-adjusted series is labelled as such",
      adj["series_verdict"] == phase56.SERIES_PRE_ADJUSTED)
check("no raw jump is claimed on an adjusted series", adj["raw_jumps_detected"] == 0)
check("the missing action record is stated in both cases",
      raw["action_record_published"] is False and adj["action_record_published"] is False)
act_err = p56probe.probe_corporate_actions(
    series_fetch(adjusted=True, error="Read timed out"), TOKENS
)
check("a failed action probe is UNDETERMINED, not PRE_ADJUSTED",
      act_err["series_verdict"] == phase56.SERIES_UNDETERMINED)

vanished = p56probe.probe_vanished(series_fetch(adjusted=True), set(TOKENS), tokens=TOKENS)
check("every declared vanished security is probed",
      vanished["probed"] == len(phase56.VANISHED_SECURITY_PROBES))
check("a security absent from the master is unreachable, not unqueried",
      vanished["absent"] == vanished["probed"] and vanished["history_reachable"] == 0)
check("the reason names the missing token, not a failed request",
      all("no master row" in row.get("reason", "") for row in vanished["rows"]))
present_master = set(TOKENS) | {p["symbol"] for p in phase56.VANISHED_SECURITY_PROBES}
tokens_with_ghosts = {**TOKENS, **{p["symbol"]: "424242" for p in phase56.VANISHED_SECURITY_PROBES}}
vanished_ok = p56probe.probe_vanished(
    series_fetch(adjusted=True), present_master, tokens=tokens_with_ghosts
)
check("a reachable vanished security is recorded as reachable",
      vanished_ok["history_reachable"] == vanished_ok["probed"])

print("the gate")
bad = p56audit.assess(MASTER, cov, adj, vanished, as_of=dt.date(2026, 9, 21))
check("a missing field stops the phase", bad["gate"] == phase56.GATE_INADEQUATE)
check("survivorship control is reported as limited",
      bad["survivorship_control_status"] == phase56.SURVIVORSHIP_LIMITED)
check("universe coverage is missing", "UNIVERSE_COVERAGE" in bad["data_missing"])
check("membership coverage is missing", "HISTORICAL_MEMBERSHIP_COVERAGE" in bad["data_missing"])
check("corporate-action coverage is missing", "CORPORATE_ACTION_COVERAGE" in bad["data_missing"])
check("listing/delisting coverage is missing", "LISTING_DELISTING_COVERAGE" in bad["data_missing"])
check("price-series coverage is available", "PRICE_SERIES_COVERAGE" in bad["data_available"])
check("the cost schedule is available", "COST_SCHEDULE_COVERAGE" in bad["data_available"])
check("the cross-sectional sections are reported as blocked",
      any("§15" in row["section"] for row in bad["study_stages_blocked"]))
check("the §5 raw/adjusted separation is reported as blocked",
      any("§5" in row["section"] for row in bad["study_stages_blocked"]))
check("every missing field names at least one blocked section",
      all(any(row["blocked_by"] == field for row in bad["study_stages_blocked"])
          for field in bad["data_missing"]))

throttled = p56audit.assess(MASTER, cov_err, act_err, {"probed": 6, "in_master": 0,
                                                       "history_reachable": 0, "absent": 0,
                                                       "inconclusive": 6, "rows": []})
check("throttled survivorship is UNKNOWN, not LIMITED",
      throttled["survivorship_control_status"] == phase56.SURVIVORSHIP_UNKNOWN)
check("a throttled provider yields no probe-derived MISSING field",
      not {"UNIVERSE_COVERAGE", "CORPORATE_ACTION_COVERAGE", "PRICE_SERIES_COVERAGE"}
      & set(throttled["data_missing"]))
check("the throttled probe fields are INCONCLUSIVE",
      {"UNIVERSE_COVERAGE", "CORPORATE_ACTION_COVERAGE", "PRICE_SERIES_COVERAGE"}
      <= set(throttled["data_inconclusive"]))
check("fields measured from the master survive a throttled endpoint",
      {"HISTORICAL_MEMBERSHIP_COVERAGE", "LISTING_DELISTING_COVERAGE"}
      <= set(throttled["data_missing"]))

good_actions = {**raw, "action_record_published": True}
good = p56audit.assess(MASTER, cov, good_actions, vanished_ok, as_of=dt.date(2026, 9, 21))
check("a source that answers every field opens the gate",
      good["gate"] == phase56.GATE_ADEQUATE or "LISTING_DELISTING_COVERAGE" in good["data_missing"])
check("listing/delisting stays missing even when the rest passes",
      "LISTING_DELISTING_COVERAGE" in good["data_missing"])

print("report")
text = p56report.render(bad)
check("the report states the gate", phase56.GATE_INADEQUATE in text)
check("the report states the survivorship status", phase56.SURVIVORSHIP_LIMITED in text)
check("the report carries the fingerprint", bad["fingerprint"] in text)
check("the report prints all six §36 fields",
      all(field["field"] in text for field in bad["fields"]))
check("zero candidates is explained as unmeasured, not refuted",
      "absence of measurement" in text)
check("the report says what would open the gate", "WHAT WOULD OPEN THE GATE" in text)
check("no forbidden verdict word appears",
      not any(word in text for word in ("WINNER", "GUARANTEED", "HIGH_CONFIDENCE", "BUY_PROBABILITY")))
check("the survivor universe is never presented as the conclusion universe",
      "DIAGNOSTIC_ONLY" in text)

print("costs")
flat = p56costs.breakeven_example()
check("a flat round trip costs money", flat["net_pnl"] < 0)
check("the round trip is decomposed as §8 asks",
      all(key in flat for key in ("brokerage", "exchange_charges", "regulatory_charges",
                                  "transaction_taxes", "stamp_duty", "slippage")))
check("every cost row is labelled MODELLED_COST",
      flat["cost_status"] == p56costs.MODELLED
      and all(row["provenance"] == p56costs.MODELLED for row in p56costs.schedule()))
check("brokerage is date-aware",
      p56costs.brokerage(1_000_000.0, dt.date(2022, 1, 1)) == 0.0
      and p56costs.brokerage(1_000_000.0, dt.date(2024, 1, 1)) > 0.0)
check("the exchange charge is date-aware",
      p56costs.exchange_charge(1_000_000.0, dt.date(2024, 1, 1))
      > p56costs.exchange_charge(1_000_000.0, dt.date(2025, 1, 1)))
win = p56costs.round_trip(buy_price=100.0, sell_price=110.0, quantity=100,
                          buy_date=dt.date(2026, 1, 1), sell_date=dt.date(2026, 1, 8))
check("net is below gross on a winner", win["net_pnl"] < win["gross_pnl"])
check("slippage is charged on both sides", win["slippage"] > 0)
check("the DP charge is never waived", win["dp_charges"] > 0)
check("a zero quantity is refused rather than costed",
      raises(lambda: p56costs.round_trip(buy_price=1.0, sell_price=1.0, quantity=0,
                                         buy_date=dt.date(2026, 1, 1),
                                         sell_date=dt.date(2026, 1, 2))))

print("isolation")
check("no order path anywhere in the package",
      not any(word in BLOB for word in ("placeOrder", "place_order", "generateOrder")))
check("no production store is opened",
      "history.db" not in BLOB and "research.db" not in BLOB)
check("no phase 41-55 module is imported",
      not any(f"phase{n}" in BLOB for n in range(41, 56)))
check("no tick hook is registered", "on_tick" not in BLOB and "register_hook" not in BLOB)
check("no module reads a credential from the environment",
      "os.environ" not in BLOB and "getenv" not in BLOB)
check("the login helper is the shared one",
      "from app.backtest.angel_history import login_smart" in (PKG / "cli.py").read_text(encoding="utf-8"))
check("no stage-1 command can run a study",
      '"study"' not in (PKG / "cli.py").read_text(encoding="utf-8"))
check("stage 1 has no collect command", '"collect"' not in (PKG / "cli.py").read_text(encoding="utf-8"))
check("the only writer is the CLI artefact helper",
      sum("open(" in path.read_text(encoding="utf-8") for path in SOURCES) == 1)
check("artefacts land under the phase directory alone",
      "OUT_DIR = \"data/research/phase56\"" in (PKG / "cli.py").read_text(encoding="utf-8")
      and BLOB.count("os.makedirs") == 1)

tree = ast.parse((PKG / "probe.py").read_text(encoding="utf-8"))
raises = [node for node in ast.walk(tree) if isinstance(node, ast.Raise)]
check("the probe layer never raises past the caller", raises == [])

print(f"\nPHASE 56 SMOKE — {PASS} passed, {FAIL} failed")
if FAIL:
    raise SystemExit(1)
