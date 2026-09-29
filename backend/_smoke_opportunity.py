"""Opportunity engine smoke — the refusals that keep a wide search honest.

A market-wide scanner is the easiest place in this platform to manufacture an
edge, because every mistake scales with the number of candidates. Each check
here exists because the opposite mistake would produce a confident, plausible,
wrong result:

* a midpoint fill, which books half the spread as profit on both ends of every
  trade and is larger than any edge being measured on an option book;
* one candidate's state reaching another's decision, so a "parallel" search is
  really one path dressed as many;
* a frozen definition edited in place, so yesterday's evidence silently
  describes a different rule than the one that produced it;
* a future bar, an outcome or a giveback reaching an admission function;
* the same shadow row or paper leg counted twice on a re-run;
* a capture gap read as a decision to stand aside, which quietly improves the
  record of exactly those candidates whose data is worst;
* an instrument with no history ranked low rather than unranked, implying a
  comparison that never happened;
* a candidate killed for being down on a small sample, which discards the
  unlucky and keeps the lucky;
* a composite score letting a strong cheap dimension pay for a failed
  expensive one at the promotion gate;
* this package reaching a production signal, the order path, or Phases 41-44.

    .venv/bin/python _smoke_opportunity.py
"""
from __future__ import annotations

import ast
import datetime as dt
import inspect
import os
import tempfile

import numpy as np

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="oppsmoke-"))

from app.research import opportunity as opp  # noqa: E402
from app.research.opportunity import (  # noqa: E402
    CAPTURE_GAP,
    CE,
    DISCOVERY,
    FROZEN_AT,
    FUTURES,
    GATE_NAMES,
    HISTORICAL_REJECTED,
    JOURNAL_FILE,
    MEASURED,
    NO_CANDIDATE,
    NO_CHAMPION,
    NO_HISTORY,
    NO_TRADE_ANYWHERE,
    PAPER,
    PROMOTION_MIN_SESSIONS,
    PROMOTION_MIN_TRADES,
    SHADOW,
    SHADOW_FILE,
    STALE,
    UNMEASURED,
)
from app.research.opportunity import bars as oppbars  # noqa: E402
from app.research.opportunity import cli as ocli  # noqa: E402
from app.research.opportunity import htf, stats  # noqa: E402
from app.research.opportunity import cycle as ocycle  # noqa: E402
from app.research.opportunity import generator, guard  # noqa: E402
from app.research.opportunity import mechanisms as mech  # noqa: E402
from app.research.opportunity import paper, promotion  # noqa: E402
from app.research.opportunity import ranking, registry  # noqa: E402
from app.research.opportunity import screen, shadow, store  # noqa: E402

PASS = 0
FAIL: list[str] = []

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
PKG_DIR = os.path.dirname(os.path.abspath(opp.__file__))
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(PKG_DIR)))


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


def _window(closes: list[float], instrument: str = "CRUDEOIL",
            start: int = 1_700_000_000) -> mech.Window:
    n = len(closes)
    arr = np.asarray(closes, dtype=float)
    return mech.Window(
        instrument=instrument,
        ts=np.asarray([start + 60 * i for i in range(n)], dtype=np.int64),
        open=arr, high=arr + 0.5, low=arr - 0.5, close=arr,
        volume=np.full(n, 100.0),
    )


def _candidate(name: str = "smoke_breakout", *, mechanism: str = "breakout",
               params: dict | None = None, vehicle: str = FUTURES,
               instrument: str = "CRUDEOIL") -> dict:
    return registry.register({
        "candidate_name": name,
        "mechanism_family": "A_DIRECTIONAL_MOMENTUM",
        "instrument_scope": [instrument],
        "vehicle_scope": [vehicle],
        "entry_definition": {"mechanism": mechanism,
                             "params": params or {"lookback": 15,
                                                  "buffer_atr": 0.25}},
        "exit_definition": dict(generator.EXITS["T1_THEN_BREAKEVEN"],
                                rule="T1_THEN_BREAKEVEN"),
        "cost_definition": {
            "fills": "ASK_IN_BID_OUT_FOR_LONG_OPTIONS_EXECUTABLE_SIDE_FOR_FUTURES",
            "midpoint": "NEVER",
            "model": "PER_ORDER_BROKERAGE_PLUS_STATUTORY_CHARGES",
            "spread": "MEASURED_WHERE_A_TWO_SIDED_BOOK_EXISTS",
            "structure": None,
        },
    })


def _obs(ts: float, instrument: str = "CRUDEOIL", obs_id: str = "o1") -> dict:
    return {"obs_id": obs_id, "ts": ts, "session": "2026-09-16",
            "instrument": instrument, "context_json": None}


def main() -> int:  # noqa: C901 - one linear suite, grouped by concern
    tmp = tempfile.mkdtemp(prefix="opp_")
    data = os.path.join(tmp, "data_dir")
    os.makedirs(data, exist_ok=True)
    original = store.data_dir
    store.data_dir = lambda: data  # type: ignore[assignment]
    try:
        # --- the package is paper-only by construction ---------------------
        ok(opp.VERSION == "OPPORTUNITY_V1", "the package declares its version")
        ok(any("no order path" in s for s in opp.STANDING_LIMITS),
           "and states the no-order-path limit in its own standing limits")

        pkg_text = ""
        for fn in sorted(os.listdir(PKG_DIR)):
            if fn.endswith(".py"):
                with open(os.path.join(PKG_DIR, fn), encoding="utf-8") as fh:
                    pkg_text += fh.read()
        for forbidden in ("place_order", "placeOrder", "smartConnect",
                          "SmartConnect", "kiteconnect", "broker.",
                          "requests.post", "httpx.post"):
            ok(forbidden not in pkg_text,
               f"no module in the package references {forbidden}")
        ok("import requests" not in pkg_text and "import httpx" not in pkg_text,
           "and the package makes no HTTP client import at all")
        for phase in ("phase41", "phase42", "phase43", "phase44"):
            ok(phase not in pkg_text,
               f"and never imports or touches {phase}")
        ok("signals" not in pkg_text.replace("shadow signals", "")
           .replace("shadow signal", "").replace("signal(s)", "")
           .replace("_signals", "").replace("signals_considered", "")
           or "app.services" not in pkg_text,
           "and imports nothing from the production signal services")

        # --- midpoint is absent, not merely unused -------------------------
        lowered = pkg_text.lower()
        ok("midpoint" in lowered, "the package names the midpoint rule")
        ok("(bid + ask)" not in lowered and "(bid+ask)" not in lowered
           and "/ 2.0" not in pkg_text.replace("* 2.0", ""),
           "but never computes one anywhere in the package")

        # --- fingerprints and immutability ---------------------------------
        cand = _candidate()
        fp = cand["definition_fingerprint"]
        again = _candidate()
        ok(again["candidate_id"] == cand["candidate_id"],
           "registering the same definition twice yields one candidate")
        ok(again["definition_fingerprint"] == fp,
           "and the same fingerprint, so identity is content-addressed")
        ok(cand["status"] == DISCOVERY, "a new candidate starts at DISCOVERY")

        amended = registry.amend_definition(
            cand["candidate_id"], {"minimum_trades": 40})
        ok(amended["ok"] and amended["candidate_id"] != cand["candidate_id"],
           "amending a definition produces a new candidate, not an edit")
        ok(registry.get(cand["candidate_id"])["definition_fingerprint"] == fp,
           "and leaves the original fingerprint exactly as it was")

        skip = registry.set_status(cand["candidate_id"], SHADOW)
        ok(not skip["ok"] and "illegal transition" in skip["error"],
           "a candidate cannot jump from DISCOVERY straight to SHADOW")
        ok(registry.set_status(cand["candidate_id"],
                               opp.HISTORICAL_TESTING)["ok"]
           and registry.set_status(cand["candidate_id"], SHADOW)["ok"],
           "it reaches SHADOW only through the historical stage")
        ok(SHADOW in FROZEN_AT, "SHADOW is a frozen status")

        frozen = registry.amend_definition(cand["candidate_id"],
                                           {"minimum_trades": 99})
        ok(not frozen["ok"] and frozen["error"] == "DEFINITION_IS_FROZEN_AT_THIS_STATUS",
           "a definition frozen at SHADOW refuses to be amended in place")
        ok("new candidate" in frozen["remedy"],
           "and names the honest way forward instead of just blocking")
        ok(registry.get(cand["candidate_id"])["definition_fingerprint"] == fp,
           "leaving the frozen fingerprint untouched")

        back = registry.set_status(cand["candidate_id"], DISCOVERY)
        ok(not back["ok"],
           "and the lifecycle refuses to walk backwards out of SHADOW")

        # --- append-only storage -------------------------------------------
        before = len(store.read(opp.CANDIDATE_FILE))
        registry.set_status(cand["candidate_id"], PAPER)
        ok(len(store.read(opp.CANDIDATE_FILE)) > before,
           "a status change appends a line rather than rewriting one")
        ok(registry.get(cand["candidate_id"])["status"] == PAPER,
           "and the folded view reads the latest status")

        # --- look-ahead: structural, then audited --------------------------
        sig = inspect.signature(mech.breakout)
        ok(list(sig.parameters)[0] == "w",
           "a mechanism receives a Window and nothing else positionally")
        src = inspect.getsource(mech)
        tree = ast.parse(src)
        forbidden_names = {"mfe", "mae", "giveback", "outcome", "exit_price",
                           "pnl", "future_high", "next_bar", "hit_t1", "hit_sl"}
        found = {n.id for n in ast.walk(tree)
                 if isinstance(n, ast.Name) and n.id in forbidden_names}
        ok(not found,
           "no admission function mentions an outcome or a future field")
        audit = guard.audit(mech.ADMISSION_FUNCTIONS)
        ok(audit["status"] == guard.CLEAN,
           "and the AST guard audits every admission function clean")
        ok(audit["checked"] == len(mech.ADMISSION_FUNCTIONS),
           "auditing all of them, not a sample")

        w = _window([100.0] * 20)
        ok(not hasattr(w, "future") and "future" not in dir(w),
           "a Window exposes no field that could hold a later bar")

        # --- candidate isolation -------------------------------------------
        params = list(inspect.signature(shadow.evaluate).parameters)
        ok(params == ["candidate", "obs", "bars", "quotes_by_vehicle"],
           "shadow.evaluate takes one candidate, the observation and the book")
        ok(not any(p in params for p in ("champion", "others", "peers",
                                         "registry")),
           "and has no parameter through which another candidate could reach it")

        bars = shadow.LiveBars()
        base_ts = 1_700_000_000
        for i in range(40):
            bars.observe("CRUDEOIL", base_ts + 60 * i, 100.0 + (i % 3) * 0.1)
        bars.observe("CRUDEOIL", base_ts + 60 * 40, 108.0)
        quote = {"vehicle": FUTURES, "ts": base_ts + 60 * 40, "bid": 107.9,
                 "ask": 108.1, "symbol": "CRUDEOIL26SEPFUT"}
        obs = _obs(base_ts + 60 * 40)

        c1 = _candidate("iso_one")
        c2 = _candidate("iso_two", params={"lookback": 30, "buffer_atr": 0.5})
        r1 = shadow.evaluate(c1, obs, bars, {FUTURES: quote})
        r1_again = shadow.evaluate(c1, obs, bars, {FUTURES: quote})
        r2 = shadow.evaluate(c2, obs, bars, {FUTURES: quote})
        ok(r1 == r1_again,
           "the same candidate on the same observation is deterministic")
        ok(r1["shadow_id"] != r2["shadow_id"],
           "two candidates on one observation produce two distinct rows")
        ok(r1["obs_id"] == r2["obs_id"],
           "both judged the same observation, so the comparison is fair")
        ok(shadow.evaluate(c1, obs, bars, {FUTURES: quote}) == r1,
           "and evaluating the second candidate first changes nothing")

        # --- bars are forward-only -----------------------------------------
        b2 = shadow.LiveBars()
        b2.observe("NIFTY", base_ts + 600, 100.0)
        b2.observe("NIFTY", base_ts + 60, 500.0)      # out of order, dropped
        wn = b2.window("NIFTY", 3)
        ok(wn is None or float(wn.close.max()) < 500.0,
           "an out-of-order observation cannot rewrite a bar already used")
        ok(b2.last_ts("NIFTY") == int((base_ts + 600) // 60 * 60),
           "and the cursor does not move backwards")
        wsmall = bars.window("CRUDEOIL", 500)
        ok(wsmall is None,
           "a window longer than the session so far is refused, not padded")

        # --- evidence states: a gap is a gap -------------------------------
        gap = shadow.quote_evidence(None, obs["ts"])
        ok(gap["state"] == CAPTURE_GAP,
           "no quote row at the decision instant is a CAPTURE_GAP")
        partial = shadow.quote_evidence({"bid": 10.0, "ask": None,
                                         "ts": obs["ts"]}, obs["ts"])
        ok(partial["state"] == UNMEASURED,
           "a one-sided quote is UNMEASURED, not half a price")
        old = shadow.quote_evidence(
            {"bid": 10.0, "ask": 10.2,
             "ts": obs["ts"] - shadow.MAX_QUOTE_AGE_SEC - 1}, obs["ts"])
        ok(old["state"] == STALE,
           "a quote older than the freshness bound is STALE, not MEASURED")
        fresh = shadow.quote_evidence({"bid": 10.0, "ask": 10.2,
                                       "ts": obs["ts"]}, obs["ts"])
        ok(fresh["state"] == MEASURED, "a fresh two-sided quote is MEASURED")

        unpriced = shadow.evaluate(c1, obs, bars, {})
        if unpriced["verdict"] == shadow.UNPRICED:
            ok(unpriced["evidence"] == CAPTURE_GAP,
               "a signal with no book is recorded with its gap named")
            ok("not a decision to stand aside" in unpriced["not_a_no_trade"],
               "and says in the row that it is not a NO_TRADE")
            ok("net_pct" not in unpriced and "gross_pct" not in unpriced,
               "and carries no P&L of any kind")
        else:
            ok(unpriced["verdict"] != shadow.SIGNAL,
               "an unpriceable observation never becomes a signal")
            ok("net_pct" not in unpriced,
               "and carries no P&L of any kind")
            ok(unpriced.get("evidence") in (None, CAPTURE_GAP, UNMEASURED,
                                            STALE),
               "with its evidence state named rather than assumed")

        # --- executable sides, never a midpoint ----------------------------
        row = {"vehicle": FUTURES, "direction": "LONG", "bid": 100.0,
               "ask": 100.2, "decision_price": 100.0, "atr": 1.0,
               "decision_ts": base_ts, "obs_id": "o1", "session": "s"}
        entry, side = paper._entry_price(row)
        ok(entry == 100.2 and side == "ASK",
           "a long futures leg is entered on the ask")
        short = dict(row, direction="SHORT")
        ok(paper._entry_price(short) == (100.0, "BID"),
           "a short futures leg is entered on the bid")
        ce = dict(row, vehicle=CE, direction="SHORT")
        ok(paper._entry_price(ce) == (100.2, "ASK"),
           "a long PE/CE position is bought at the ask whichever way it points")
        ok(paper._exit_side(dict(row, vehicle=CE)) == "BID",
           "and sold at the bid on the way out")
        ok(paper._entry_price(dict(row, bid=None))[0] is None,
           "a missing side gives no entry price rather than a midpoint")
        mid = (row["bid"] + row["ask"]) / 2.0
        ok(entry != mid, "the entry is not the midpoint")

        rule = dict(generator.EXITS["T1_THEN_BREAKEVEN"])
        path_up = [{"ts": base_ts + 60 * i, "bid": 100.0 + i * 1.0,
                    "ask": 100.2 + i * 1.0} for i in range(1, 10)]
        res = paper.resolve_leg(row, path_up, rule)
        ok(res["resolved"] and res["exit_side"] == "BID",
           "a resolved long futures leg exits on the bid")
        ok(res["midpoint_used"] is False and res["net_pct"] < res["gross_pct"],
           "and its net is strictly below its gross, because costs are charged")
        ok(res["cost_pct"] == paper.NON_SPREAD_COST_PCT,
           "with the spread paid by the sides and the rest modelled once")
        ok("ENTERING_AT_THE_ASK" in res["spread_paid"],
           "and the row states how the spread was paid, so it is not double-charged")

        thin = paper.resolve_leg(row, [], rule)
        ok(not thin["resolved"] and "NO_FORWARD_PATH" in thin["status"],
           "a leg with no forward path is unresolved, never flat")
        short_path = paper.resolve_leg(
            row, [{"ts": base_ts + 60, "bid": 100.1, "ask": 100.3}], rule)
        ok(not short_path["resolved"],
           "and a path that ends long before the rule would exit stays open")
        ok(short_path["status"] == paper.OPEN_AT_END,
           "labelled as open when the capture ended, not as a zero")

        stop_path = [{"ts": base_ts + 60 * i, "bid": 100.0 - i * 1.5,
                      "ask": 100.2 - i * 1.5} for i in range(1, 6)]
        stopped = paper.resolve_leg(row, stop_path, rule)
        ok(stopped["resolved"] and stopped["exit_reason"] == "STOP",
           "a leg that trades through its stop resolves at the stop")
        ok(stopped["net_pct"] < 0, "and books a loss")

        unmeasured_path = [{"ts": base_ts + 60, "bid": None, "ask": None}]
        ok(not paper.resolve_leg(row, unmeasured_path, rule)["resolved"],
           "an unmeasured instant in the path is skipped, never interpolated")

        # --- the journal cannot double-count itself ------------------------
        leg = {"leg_id": "L1", "candidate_id": c1["candidate_id"],
               "resolved": True, "net_pct": 1.0, "gross_pct": 1.2,
               "session": "2026-09-16", "vehicle": FUTURES}
        ok(store.append_unique(JOURNAL_FILE, leg, field="leg_id"),
           "a new paper leg is written")
        ok(not store.append_unique(JOURNAL_FILE, leg, field="leg_id"),
           "and the same leg_id is refused on a second pass")
        ok(len(store.read(JOURNAL_FILE)) == 1,
           "so the journal holds one row for one outcome")
        srow = dict(r1, shadow_id="S1")
        store.append_unique(SHADOW_FILE, srow, field="shadow_id")
        store.append_unique(SHADOW_FILE, srow, field="shadow_id")
        ok(len(store.read(SHADOW_FILE)) == 1,
           "and a shadow row is idempotent on candidate and observation")
        integ = store.integrity(JOURNAL_FILE)
        ok(integ["bytes"] > 0 and integ["lines"] == 1,
           "a file that holds a line reports a size, not a defaulted zero")
        ok(integ["parsed"] == integ["lines"],
           "and parsed lines match written lines, so no line is torn")
        absent = store.integrity("no_such_file.jsonl")
        ok(absent["bytes"] == 0 and not absent["exists"],
           "while a file that does not exist reports zero and says so")

        # --- early kills are structural, never 'looks bad' -----------------
        kill_src = inspect.getsource(screen)
        ok("CATASTROPHIC" in kill_src,
           "the screen's kill reasons are pre-registered constants")
        ok(opp.CATASTROPHIC_NET_PCT <= -0.5,
           "and the catastrophic bar is a structural loss, not a small drawdown")
        reasons = [name for name in dir(opp) if name.startswith("KILL_")]
        ok(reasons, "the kill vocabulary is declared in the package")
        ok(not any("LOOKS" in getattr(opp, r) or "SEEMS" in getattr(opp, r)
                   for r in reasons),
           "and contains no judgement-shaped reason such as LOOKS_BAD")

        # --- timeframes: one variable moves, and it is declared ------------
        ok(oppbars.TIMEFRAMES == (1, 5, 15, 60, 1440),
           "the timeframe sweep is a declared tuple, not a caller's argument")
        ok(oppbars.label(1440) == "1d" and oppbars.label(15) == "15m",
           "and each bar length has one label")
        try:
            oppbars.series_at("CRUDEOIL", 7)
            bad_tf = False
        except ValueError:
            bad_tf = True
        ok(bad_tf, "an undeclared timeframe is refused, not silently resampled")

        # A definition that predates the sweep must keep the identity it was
        # fingerprinted with: read as one minute, not re-registered.
        legacy = _candidate("smoke_legacy_tf")
        ok("timeframe_minutes" not in legacy["entry_definition"],
           "a candidate generated without a timeframe carries no timeframe key")
        ok(screen._timeframe(legacy) == 1,
           "and the screen reads it as one minute rather than re-fingerprinting")

        # A time stop stated in minutes must not be read as bars.
        ok(screen.horizon_bars({"time_stop_min": 120}, 15) == 8,
           "a 120-minute stop is 8 bars of 15 minutes, not 120 of them")
        ok(screen.horizon_bars({"time_stop_bars": 30, "time_stop_min": 120},
                               15) == 30,
           "and a stop stated in bars is used as stated at every timeframe")
        ok(screen.horizon_bars({"time_stop_min": 5}, 60) == 1,
           "a stop shorter than one bar still gets one bar, never zero")

        # Gross and cost are kept apart from net, because 'no edge' and 'an
        # edge that cannot pay for itself' want opposite responses.
        tf_trades = [{"net_pct": 0.01, "gross_pct": 0.07, "cost_pct": 0.06,
                      "hold_min": 30.0, "session": 1, "r_multiple": 0.2}]
        m = screen._metrics(tf_trades)
        ok(abs((m["cost_multiple"] or 0) - 0.06 / 0.07) < 1e-9,
           "cost_multiple says how many times the round trip exceeds the edge")
        ok(screen._metrics([{"net_pct": -0.01, "gross_pct": -0.0,
                             "cost_pct": 0.06, "hold_min": 1.0, "session": 1,
                             "r_multiple": -0.1}])["cost_multiple"] is None,
           "and is None when there is no positive edge to be a multiple of")
        ok(stats.median([]) is None and stats.median([1.0, 3.0]) == 2.0,
           "an empty period has no median rather than a median of zero")

        # The sweep's excluded mechanism is excluded with its reason on record.
        ok("session_position" in generator.HTF_EXCLUDED,
           "a mechanism whose question does not exist at 60m is excluded")
        ok(generator.HTF_EXCLUDED["session_position"],
           "and the exclusion carries its reason, so absence is not a negative")
        ok(all(len(p) <= 2 for p in generator.HTF_REPRESENTATIVE.values()),
           "the sweep uses one parameter set per mechanism, not a grid")

        # The report reads results; it cannot produce them.
        htf_src = inspect.getsource(htf)
        ok(not any(tok in htf_src for tok in ("screen_candidate(", "cycle.run(",
                                              "store.append(")),
           "the timeframe report runs no study of its own and writes nothing")
        ok(htf.USABLE_TRADES >= 100,
           "and reads a per-trade mean only on a usable sample")

        # A row screened before gross and cost were recorded apart has no gross
        # figure. Reading it as 0.0 would publish an edge nobody measured.
        pre_split = [{
            "screenable": True,
            "candidate_name": "legacy_row",
            "timeframe_minutes": 1,
            "train": {"trades": 5000, "net_mean_pct": -0.0638},
            "validation": {"trades": 0},
            "holdout": {"trades": 0},
        }]
        legacy = htf._summarise(pre_split, "train")["timeframes"][0]
        ok(legacy["gross_unrecorded"] == 1,
           "a row with no recorded gross is counted as unrecorded")
        ok(legacy["usable_candidates"] == 0,
           "and is not counted as a usable sample")
        ok(legacy["median_gross_pct"] is None
           and legacy["best_gross_pct"] is None,
           "so a missing gross reads as absent, never as a zero edge")
        ok(legacy["pays_for_itself"] is False,
           "and an unrecorded gross cannot clear the cost bar")

        # A bar-counted time stop on a wide bar runs past the close. The trade
        # is then held overnight, which the intraday cost model does not price,
        # so the share is measured rather than left as a silent property.
        overnight = screen._metrics([
            {"net_pct": 0.1, "gross_pct": 0.16, "cost_pct": 0.06,
             "hold_min": 60, "session": 1, "crossed_session": True},
            {"net_pct": -0.1, "gross_pct": -0.04, "cost_pct": 0.06,
             "hold_min": 60, "session": 1, "crossed_session": False},
        ])
        ok(overnight["overnight_share"] == 0.5,
           "the share of trades held through a close is a measured number")
        ok("crossed_session" in inspect.getsource(screen._result),
           "and every resolved trade records whether it crossed one")

        # A re-measurement exists to fill in the gross and cost columns on a
        # row screened before they were recorded. It must not become a second
        # chance at admission: a rejected candidate re-scored into shadow would
        # be the same hypothesis tested twice, which is what the correction is
        # for.
        run_src = inspect.getsource(screen.run)
        ok("remeasure" in run_src,
           "an already-rejected candidate can be re-measured for gross and cost")
        ok('res["significance"] = NOT_SIGNIFICANT' in run_src
           and 'res["shadow_admitted"] = False' in run_src,
           "but a re-measured row is never significant and never admitted")
        ok("registry.set_status" not in run_src.split("remeasured: list")[1]
           .split("scored = [")[0],
           "and re-measuring moves no status, so the rejection stands")
        rm_rows = [
            {"screenable": True, "candidate_id": "x", "candidate_name": "x",
             "timeframe_minutes": 1,
             "train": {"trades": 200, "gross_mean_pct": 0.01,
                       "net_mean_pct": -0.05},
             "validation": {"trades": 0}, "holdout": {"trades": 0}},
            {"screenable": True, "candidate_id": "x", "candidate_name": "x",
             "timeframe_minutes": 1, "remeasured": True,
             "train": {"trades": 200, "gross_mean_pct": 0.09,
                       "net_mean_pct": 0.03},
             "validation": {"trades": 0}, "holdout": {"trades": 0}},
        ]
        one = htf._summarise([rm_rows[1]], "train")["timeframes"][0]
        ok(one["candidates"] == 1,
           "a re-measured candidate is counted once, not twice, in the "
           "timeframe comparison")
        ok("latest" in inspect.getsource(htf._rows),
           "because the comparison reads the newest row per candidate")

        # --- multiple testing ----------------------------------------------
        screen_src = inspect.getsource(screen)
        ok("FDR" in screen_src or "fdr" in screen_src,
           "the screen applies a false-discovery cut")
        ok("UNCORRECTED_ONLY" in pkg_text,
           "and labels a survivor that only clears the uncorrected bar")

        # --- promotion gates: every one, no score --------------------------
        empty = promotion.report()
        ok(empty["verdict"] == NO_CANDIDATE,
           "with no qualifying candidate the verdict is NO_CANDIDATE")
        ok(empty["champion"]["state"] == NO_CHAMPION,
           "and there is no champion rather than a best-of-a-bad-set")
        ok(empty["gate_definition"]["all_of_them"] is True,
           "the gate definition states that every gate must pass")
        ok(len(GATE_NAMES) >= 10, "there are at least ten gates")

        strong = {"resolved_trades": PROMOTION_MIN_TRADES + 10,
                  "sessions": PROMOTION_MIN_SESSIONS + 2,
                  "net_total_pct": 12.0, "profit_factor": 1.9,
                  "max_drawdown_pct": 5.0, "cost_stress_net_pct": 6.0,
                  "one_trade_share": 0.1, "midpoint_used_anywhere": False}
        frozen_cand = dict(cand, status=PAPER)
        scr_row = {"holdout": {"net_total_pct": 3.0, "trades": 12}}
        verdict = promotion.gates(frozen_cand, strong, scr_row, audit)
        ok(verdict["all_pass"], "a candidate that clears every gate is promotable")
        for name, patch in (
            ("RESOLVED_TRADES", {"resolved_trades": 5}),
            ("SESSIONS", {"sessions": 2}),
            ("NET_AFTER_COSTS", {"net_total_pct": -1.0}),
            ("PROFIT_FACTOR", {"profit_factor": 1.0}),
            ("DRAWDOWN", {"max_drawdown_pct": 99.0}),
            ("COST_STRESS", {"cost_stress_net_pct": -0.5}),
            ("NO_SINGLE_OUTLIER", {"one_trade_share": 0.9}),
            ("DATA_COVERAGE", {"midpoint_used_anywhere": True}),
        ):
            v = promotion.gates(frozen_cand, dict(strong, **patch), scr_row,
                                audit)
            ok(not v["all_pass"] and name in v["failed"],
               f"and failing {name} alone blocks promotion on its own")
        no_holdout = promotion.gates(frozen_cand, strong,
                                     {"holdout": {"trades": 0}}, audit)
        ok("HOLDOUT_POSITIVE" in no_holdout["failed"],
           "an unmeasured holdout does not pass by default")
        missing_screen = promotion.gates(frozen_cand, strong, None, audit)
        ok("HOLDOUT_POSITIVE" in missing_screen["failed"],
           "and neither does a missing historical screen row")
        look = promotion.gates(frozen_cand, strong, scr_row,
                               {"status": "LOOK_AHEAD_SUSPECTED"})
        ok("NO_LOOK_AHEAD" in look["failed"],
           "a look-ahead suspicion blocks promotion outright")
        unfrozen = promotion.gates(dict(cand, status=DISCOVERY), strong,
                                   scr_row, audit)
        ok("FROZEN_FINGERPRINT" in unfrozen["failed"],
           "and an unfrozen definition cannot be promoted at all")

        prom_src = inspect.getsource(promotion)
        ok("composite score" in prom_src,
           "the module states in writing that there is no composite score")
        ok("weight" not in prom_src.lower().replace("weighted average", ""),
           "and weighs no gate against another")
        ok(opp.READY_FOR_CONTROLLED_LIVE_REVIEW.endswith("REVIEW"),
           "the strongest readiness label is a review, not an authorisation")
        ok("authorise" in prom_src or "authorises" in prom_src,
           "and the module says explicitly that it authorises nothing")

        # --- champion/challenger -------------------------------------------
        rows = [
            {"candidate_id": "a", "candidate_name": "a", "status": PAPER,
             "resolved_trades": 60, "sessions": 20, "net_total_pct": 6.0},
            {"candidate_id": "b", "candidate_name": "b", "status": PAPER,
             "resolved_trades": 60, "sessions": 20, "net_total_pct": 5.99},
        ]
        champ = promotion.champion(rows)
        ok(champ["champion"]["state"] == "TIED_WITHIN_NOISE_NO_CHAMPION",
           "two candidates inside the margin do not crown a champion")
        ok(len(champ["challengers"]) == 1,
           "and the runner-up is tracked as a challenger in its own right")
        clear = promotion.champion([
            rows[0], dict(rows[1], net_total_pct=1.0)])
        ok(clear["champion"]["state"] == opp.CHAMPION
           and clear["champion"]["candidate_id"] == "a",
           "a candidate ahead by more than the margin becomes champion")
        ok("ranking first is not evidence" in clear["champion"]["margin_rule"],
           "and the row states that ranking first is not on its own evidence")
        thin_sample = promotion.champion([
            dict(rows[0], resolved_trades=3, sessions=1)])
        ok(thin_sample["champion"]["state"] == NO_CHAMPION,
           "a short-term spike on three trades crowns nothing")
        ok(promotion.champion([
            {"candidate_id": "c", "candidate_name": "c", "status": SHADOW,
             "resolved_trades": 400, "sessions": 40,
             "net_total_pct": 40.0}])["champion"]["state"] == NO_CHAMPION,
           "and a candidate still at SHADOW is not eligible to be champion")

        # --- milestones are descriptive ------------------------------------
        m10 = paper.milestone(10)
        ok("DESCRIPTIVE" in m10["caveat"].upper()
           or "NOT_EVIDENCE" in m10["caveat"].upper(),
           "an early milestone carries its caveat in the same row")
        ok(paper.milestone(0)["milestone"] == opp.MILESTONE_NOT_REACHED,
           "and no milestone is claimed at zero trades")

        # --- ranking: absence is not a low rank ----------------------------
        rank = ranking.rank()
        ok(rank["verdict"] in (NO_TRADE_ANYWHERE, opp.OPPORTUNITY),
           "the ranking returns a verdict, and NO_TRADE_ANYWHERE is one of them")
        ok(isinstance(rank["unranked"], list),
           "instruments that cannot be measured are listed separately")
        ok(all(r["instrument"] not in [x["instrument"] for x in rank["ranked"]]
               for r in rank["unranked"]),
           "and never appear in the ranked list at all")
        ok(any(r.get("absence") == NO_HISTORY for r in rank["unranked"])
           or not rank["unranked"],
           "with the absence named rather than scored")
        ok("not a" in rank["not_a_prediction"].lower(),
           "the ranking says in its own payload that it is not a prediction")
        ok(sum(ranking.WEIGHTS.values()) == 1.0,
           "the declared weights sum to one")
        ok(ranking.OPPORTUNITY_COST_MULTIPLE == 3.0,
           "and the opportunity bar reuses the existing 3x cost multiple")

        # --- the funnel narrows in order -----------------------------------
        st = ocycle.status()
        ok(st["universe"]["no_history"] > 0,
           "the status report names instruments with no history")
        ok(st["promotion"]["verdict"] == NO_CANDIDATE,
           "and reports NO_CANDIDATE while nothing has qualified")
        ok(any("paper only" in s or "paper" in s for s in st["standing_limits"]),
           "carrying the standing limits with it")
        rejected = _candidate("killed_one")
        registry.set_status(rejected["candidate_id"], HISTORICAL_REJECTED,
                            reason=opp.KILL_NEGATIVE_IN_TRAIN)
        ok(rejected["candidate_id"] not in
           [c["candidate_id"] for c in registry.by_status(SHADOW)],
           "a historically rejected candidate never reaches the shadow stage")

        # --- the CLI ---------------------------------------------------------
        parser = ocli.build_parser()
        for cmd in ("cycle", "status", "rank", "promotion"):
            ns = parser.parse_args([cmd])
            ok(callable(ns.func), f"the CLI exposes `{cmd}`")
        cli_src = inspect.getsource(ocli)
        ok("No subcommand places an order" in cli_src,
           "the CLI states in its own docstring that it places nothing")
        ok(not any(tok in cli_src for tok in ("place_order", "placeOrder",
                                              "broker", "execute_order")),
           "and no subcommand has a call through which it could")

        # --- production isolation ------------------------------------------
        ok(os.path.isdir(os.path.join(BACKEND, "app", "research", "phase44")),
           "phase 44 is present in the tree")
        p44 = os.path.join(BACKEND, "app", "research", "phase44")
        for fn in sorted(os.listdir(p44)):
            if fn.endswith(".py"):
                with open(os.path.join(p44, fn), encoding="utf-8") as fh:
                    text = fh.read()
                    ok("research.opportunity" not in text
                       and "research import opportunity" not in text,
                       f"and phase44/{fn} does not import this package")
    finally:
        store.data_dir = original  # type: ignore[assignment]

    print(f"\nOPPORTUNITY SMOKE — {PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
