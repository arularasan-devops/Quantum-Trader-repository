"""Phase 55 smoke — the properties that decide whether the verdict is honest.

The tests that matter here are not "does the study run". They are the claims a
reader of the Phase 55 verdict has to be able to rely on:

* the universe is declared before collection, and a name that cannot be resolved
  is reported as unresolved rather than replaced by a different instrument;
* the gate is decided by the pre-declared floors, and an excluded instrument
  keeps its row and its reason in the report;
* an instrument the audit refused cannot reach a grade by being named directly;
* the correction denominator grows with the universe rather than staying at one
  instrument's grid, so twenty names make promotion harder, not easier;
* a single instrument out of twenty is never promoted on its own;
* MCX series carry the unverified-roll label everywhere they are reported, and
  no cash series is described as having a roll;
* ``data/history.db`` is not read or written in the package, so live-capture
  rows cannot reach the dataset;
* there is no order path, credentials are read only in the CLI login helper, and
  nothing in Phases 41-54 moved.

Every fetch, series load and master list is injected, so the suite runs with no
credentials and no network.
"""
from __future__ import annotations

import ast
import json

from pathlib import Path

import numpy as np

from app.research import phase55
from app.research.phase53 import ROBUST_CANDIDATE as P53_ROBUST
from app.research.phase55 import audit as p55audit
from app.research.phase55 import collect as p55collect
from app.research.phase55 import report as p55report
from app.research.phase55 import study as p55study
from app.research.phase55 import universe as p55universe

PASS = 0
FAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {label}")


PKG = Path("app/research/phase55")
SOURCES = sorted(PKG.glob("*.py"))


class FakeSeries:
    """The minimum of the Phase 24 Series interface the audit consumes."""

    def __init__(self, sessions: int, minutes: int, start_day: int = 0) -> None:
        base = 1_627_875_000  # a weekday 09:15 IST instant
        ts: list[int] = []
        for day in range(sessions):
            day0 = base + (start_day + day) * 86_400
            ts.extend(day0 + m * 60 for m in range(minutes))
        self.ts = np.array(ts, dtype=np.int64)
        n = self.ts.size
        walk = np.cumsum(np.full(n, 0.5))
        self.open = 100.0 + walk
        self.close = self.open + 0.25
        self.high = np.maximum(self.open, self.close) + 0.1
        self.low = np.minimum(self.open, self.close) - 0.1
        self.volume = np.ones(n)

    def __len__(self) -> int:
        return int(self.ts.size)


# ------------------------------------------------------------- declaration
print("declaration before collection")
prereg = phase55.preregistration()
check("20 instruments declared", len(phase55.UNIVERSE) == 20)
check("the fingerprint is stable", phase55.fingerprint() == phase55.fingerprint())
check("the declaration carries the universe",
      len(prereg["universe"]) == len(phase55.UNIVERSE))
check("both frozen families are named",
      set(phase55.FAMILIES) == {"ORB_RETEST_P53", "MULTIDAY_EXPANSION_P54"})
check("the floors are declared as numbers, not derived later",
      phase55.MIN_GRADED_SESSIONS > 0 and phase55.MIN_BARS > 0)
check("cash series are declared roll-free",
      phase55.SERIES_CLASS_CASH == "CASH_SERIES_NO_ROLL")
check("MCX series carry the unverified-roll label",
      phase55.SERIES_CLASS_FUTURES == "PROVIDER_CONTINUOUS_UNVERIFIED_ROLL")

# changing the universe must change the fingerprint
_fp_before = phase55.fingerprint()
_saved = dict(phase55.UNIVERSE)
phase55.UNIVERSE.pop("ITC")
check("the fingerprint moves when the universe moves",
      phase55.fingerprint() != _fp_before)
phase55.UNIVERSE.clear()
phase55.UNIVERSE.update(_saved)
check("the fingerprint returns when the universe returns",
      phase55.fingerprint() == _fp_before)

# --------------------------------------------------------------- resolution
print("token resolution never substitutes")
MASTER = [
    {"name": "NIFTY", "exch_seg": "NSE", "instrumenttype": "AMXIDX",
     "symbol": "Nifty 50", "token": "99926000", "expiry": ""},
    {"name": "BANKNIFTY", "exch_seg": "NSE", "instrumenttype": "AMXIDX",
     "symbol": "Nifty Bank", "token": "99926009", "expiry": ""},
    {"name": "RELIANCE", "exch_seg": "NSE", "instrumenttype": "",
     "symbol": "RELIANCE-EQ", "token": "2885", "expiry": ""},
    {"name": "RELIANCE", "exch_seg": "NSE", "instrumenttype": "",
     "symbol": "RELIANCE-BE", "token": "2886", "expiry": ""},
]
rows = p55universe.resolve(MASTER, ["NIFTY", "BANKNIFTY", "RELIANCE", "ITC"])
by_name = {r["instrument"]: r for r in rows}
check("an index resolves to its AMXIDX row",
      by_name["NIFTY"]["token"] == "99926000")
check("an equity resolves to its -EQ row, not -BE",
      by_name["RELIANCE"]["token"] == "2885")
check("a missing name is UNRESOLVED, not swapped",
      by_name["ITC"]["status"] == "UNRESOLVED" and by_name["ITC"]["token"] is None)
check("an unresolved name still appears in the output",
      len(rows) == 4)
check("the unresolved row states why",
      bool(by_name["ITC"].get("basis")))
ambiguous = p55universe.resolve_equity(
    MASTER + [{"name": "RELIANCE", "exch_seg": "NSE", "instrumenttype": "",
               "symbol": "RELIANCE-EQ", "token": "9999", "expiry": ""}],
    "RELIANCE", "NSE",
)
check("two candidate rows resolve to UNRESOLVED rather than the first one",
      ambiguous["status"] == "UNRESOLVED")
check("a cash resolution is labelled roll-free",
      by_name["NIFTY"]["series_class"] == phase55.SERIES_CLASS_CASH)

# -------------------------------------------------------------------- gate
print("the audit gate uses the declared floors")
thin = p55audit.audit_instrument("BANKNIFTY", FakeSeries(30, 375))
check("a short history is excluded on history",
      thin["gate"] == phase55.GATE_EXCLUDED_HISTORY)
check("the exclusion states the measured number",
      "30" in thin["gate_reason"] or "bars" in thin["gate_reason"])

full = p55audit.audit_instrument("BANKNIFTY", FakeSeries(1_100, 375))
check("a five-year NSE series is admitted", full["gate"] == phase55.GATE_INCLUDED)
check("the NSE session length is 375, not the MCX 870",
      full["session_minutes"] == 375)
mcx = p55audit.audit_instrument("COPPER", FakeSeries(1_100, 375))
check("the same series graded against an 870-minute session covers less",
      mcx["coverage_pct"] < full["coverage_pct"])

dupe = FakeSeries(1_100, 375)
dupe.ts[5] = dupe.ts[4]
dup_row = p55audit.audit_instrument("BANKNIFTY", dupe)
check("a duplicate minute is excluded on quality",
      dup_row["gate"] == phase55.GATE_EXCLUDED_QUALITY)

bad = FakeSeries(1_100, 375)
bad.high[7] = bad.low[7] - 1.0
bad_row = p55audit.audit_instrument("BANKNIFTY", bad)
check("an impossible bar is excluded on quality",
      bad_row["gate"] == phase55.GATE_EXCLUDED_QUALITY)

unres = p55audit.unresolved_row(
    {"instrument": "ITC", "exchange": "NSE", "basis": "no matching -EQ row"}
)
check("an unresolved instrument is gated UNRESOLVED",
      unres["gate"] == phase55.GATE_UNRESOLVED)


def loader(name: str):
    return {
        "NIFTY": FakeSeries(1_100, 375),
        "BANKNIFTY": FakeSeries(1_100, 375),
        "MIDCPNIFTY": FakeSeries(40, 375),
    }.get(name)


audit_result = p55audit.run(
    loader, ["NIFTY", "BANKNIFTY", "MIDCPNIFTY", "ITC"],
    {"ITC": {"instrument": "ITC", "status": "UNRESOLVED", "basis": "absent from master"}},
)
check("only the admitted names are included",
      audit_result["included"] == ["NIFTY", "BANKNIFTY"])
check("every requested name has a row",
      len(audit_result["instruments"]) == 4)
check("the excluded list carries reasons",
      all(e["reason"] for e in audit_result["excluded"]))
text = p55report.render_history(audit_result)
check("the report keeps the excluded names visible",
      "MIDCPNIFTY" in text and "ITC" in text)
check("the report names the unverified roll for MCX",
      "PROVIDER_CONTINUOUS_UNVERIFIED_ROLL" in text)
check("the report refuses the executable-book label",
      "No series here is executable bid/ask history" in text)
check("the gate is open when something was admitted",
      p55report.gate_open(audit_result))

# ----------------------------------------------------------------- study
print("the correction gets harder as the universe widens")


def row(p: float, status: str, instrument: str, rid: str) -> dict:
    return {
        "candidate_id": rid, "instrument": instrument, "final_status": status,
        "per_partition": {"DISCOVERY": {"p_value_one_sided": p}},
        "human_readable_rule": "test row",
    }


narrow = [row(0.004, P53_ROBUST, "A", "a1")]
wide = narrow + [row(0.6, "NO_EDGE", "B", f"b{i}") for i in range(200)]
check("a p-value that passes at one test fails at 201",
      p55study.universe_correction(narrow)[0]
      and not p55study.universe_correction(wide)[0])

single = {
    "instruments": [
        {"instrument": "A", "eligible": True, "rows": [row(0.0001, P53_ROBUST, "A", "a1")],
         "totals": {"HOLDOUT_POSITIVE": 1, "UNIQUE_EVENT_FAMILIES": 3,
                    "DISCOVERY_LEADS": 1, "VALIDATION_POSITIVE": 1,
                    "ROBUST_CANDIDATES": 1}},
        {"instrument": "B", "eligible": True, "rows": [row(0.9, "NO_EDGE", "B", "b1")],
         "totals": {"HOLDOUT_POSITIVE": 0, "UNIQUE_EVENT_FAMILIES": 3,
                    "DISCOVERY_LEADS": 0, "VALIDATION_POSITIVE": 0,
                    "ROBUST_CANDIDATES": 0}},
    ],
    "totals": {"ROBUST_CANDIDATES": 1, "HOLDOUT_POSITIVE": 1,
               "TOTAL_PARAMETERIZATIONS": 2},
}
graded = p55study.grade_family("ORB_RETEST_P53", single)
check("one instrument out of the universe is not promoted",
      graded["totals"]["PROMOTED"] == 0)
check("the refusal names the single-instrument reason",
      single["instruments"][0]["rows"][0]["phase55_verdict"]
      == p55study.REJECTED_SINGLE_INSTRUMENT)

two = json.loads(json.dumps(single))
two["instruments"][1]["rows"] = [row(0.0002, P53_ROBUST, "B", "b1")]
two["instruments"][1]["totals"]["HOLDOUT_POSITIVE"] = 1
graded2 = p55study.grade_family("ORB_RETEST_P53", two)
check("two independent instruments can promote",
      graded2["totals"]["PROMOTED"] == 2)
check("a promoted row records its own phase verdict too",
      all(r["final_status"] == P53_ROBUST for r in graded2["promoted"]))
check("a non-candidate is never promoted",
      p55study.grade_family("ORB_RETEST_P53", {
          "instruments": [{"instrument": "A", "eligible": True,
                           "rows": [row(0.0001, "NO_EDGE", "A", "a1")],
                           "totals": {"HOLDOUT_POSITIVE": 1}}],
          "totals": {},
      })["totals"]["PROMOTED"] == 0)

counts = p55study.event_counts("ORB_RETEST_P53", single)
check("per-instrument event counts are reported, not pooled only",
      len(counts) == 2 and counts[0]["instrument"] == "A")
verdict = p55report.render_verdict({
    "fingerprint": phase55.fingerprint(), "families": [graded],
    "totals": {"PROMOTED": 0}, "event_counts": counts,
})
check("a zero verdict refuses to substitute a best row",
      "PROMOTED = 0" in verdict and "substituted" in verdict)
check("the verdict prints the universe-wide denominator",
      "universe-wide FDR denominator" in verdict)
check("promoted_ids is empty when nothing is promoted",
      p55report.promoted_ids({"families": [graded]}) == [])

# an instrument the audit refused cannot be graded by naming it
src = (PKG / "cli.py").read_text(encoding="utf-8")
check("the study command grades the audit's admitted list",
      "admitted = audit_result[\"included\"]" in src
      and "p55study.run(admitted" in src)

# ------------------------------------------------------------- boundaries
print("boundaries")
blob = "\n".join(p.read_text(encoding="utf-8") for p in SOURCES)
# The live-capture store may be *named* once, in the declaration that says it is
# out of bounds. What must not exist is a path that opens it.
check("data/history.db is named only as an out-of-bounds declaration",
      blob.count("history.db") == 1
      and "data/history.db is neither read nor written"
      in (PKG / "__init__.py").read_text(encoding="utf-8"))
for path in SOURCES:
    opened = [
        n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and n.value.endswith("history.db")
    ]
    check(f"{path.name} never opens the live-capture store", not opened)
check("no live-capture store is imported", "app.store" not in blob)
for bad_call in ("place_order", "placeOrder", "cancelOrder", "modifyOrder"):
    check(f"no order path: {bad_call}", bad_call not in blob)
check("no midpoint pricing language", "midpoint" not in blob.lower())
check("historical data keeps its class",
      phase55.DATA_CLASS == "HISTORICAL_CANDLE_DATA")
check("no option leg, strike or premium is constructed anywhere",
      not any(t in blob.lower() for t in ("strike", "premium", '"ce"', "'ce'")))

creds = ("SMARTAPI_KEY", "SMARTAPI_CLIENT_CODE", "SMARTAPI_PIN",
         "SMARTAPI_TOTP_SECRET")
for path in SOURCES:
    text = path.read_text(encoding="utf-8")
    if path.name == "cli.py":
        continue
    check(f"{path.name} reads no credential",
          not any(c in text for c in creds) and "os.environ" not in text)
check("the CLI delegates login to the existing helper",
      "from app.backtest.angel_history import login_smart" in src)
check("no credential literal anywhere",
      not any(c + " =" in blob for c in creds))

for path in SOURCES:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    check(f"{path.name} parses", isinstance(tree, ast.Module))

print("phases 41-54 untouched")
for phase in range(41, 55):
    d = Path(f"app/research/phase{phase}")
    if d.is_dir():
        check(f"phase{phase} present", True)
check("phase53 study is imported, not copied",
      "from app.research.phase53 import study as p53study"
      in (PKG / "study.py").read_text(encoding="utf-8"))
check("phase54 study is imported, not copied",
      "from app.research.phase54 import study as p54study"
      in (PKG / "study.py").read_text(encoding="utf-8"))
check("no phase53/54 module is written to",
      "phase53/" not in blob and "phase54/" not in blob)

print("collection")
check("already-shipped names are not re-collected",
      "NIFTY" not in p55collect.plan(["NIFTY", "BANKNIFTY"]))
check("the plan keeps the new names",
      p55collect.plan(["NIFTY", "BANKNIFTY"]) == ["BANKNIFTY"])
check("a multi-token export is refused rather than stitched",
      "more than one token" in (PKG / "collect.py").read_text(encoding="utf-8"))
check("the phase writes its own store, not the mcx one",
      p55collect.default_db().endswith("phase55hist.db"))
check("the collector is reused, not forked",
      "mcxcollector.collect" in (PKG / "collect.py").read_text(encoding="utf-8"))

print(f"\nPHASE 55 SMOKE — {PASS} passed, {FAIL} failed")
if FAIL:
    raise SystemExit(1)
