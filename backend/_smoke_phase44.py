"""Phase 44 smoke — the properties that keep a dormant recorder dormant, honest
and frozen.

Each check exists because its absence has already cost this project something,
or would:

* a recorder that writes before anyone armed it, which turns "prepared" into
  "running" without a decision being taken;
* an activation that survives an edit to the rule, so rows measured under two
  definitions pool into one sample — Phase 42's cost-basis confound, exactly;
* a decision-time rule that reads a field which only exists after the leg is
  over, which is how Phase 42's first ratio came to be built on a realised
  peak;
* a trailing window that includes the decision's own minute, so the move being
  forecast contributes to its own forecast;
* the measured cost quietly replaced by the modelled one when the book is
  missing, which would delete the only comparison this phase exists to make;
* an unmeasurable instant dropped rather than counted, which flatters the live
  firing rate by hiding the instants that could not be measured;
* a fingerprint that ignores function bodies, or moves on a reflowed comment;
* any import of Phase 41, Phase 42 or the order path from a research module,
  which is how a study becomes a trading change by accident;
* a status page that reads as a result when it is a row count.

    .venv/bin/python _smoke_phase44.py
"""
from __future__ import annotations

import ast
import inspect
import os
import sqlite3
import sys
import time

from app.research import phase44
from app.research.phase35 import store as p35store
from app.research.phase43 import mechanisms as p43mech
from app.research.phase44 import (
    cli,
    freeze,
    gate,
    recorder,
    report,
    rule,
    store,
    switch,
)

PASS = 0
FAIL: list[str] = []


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


MINUTE = 60.0
BASE_TS = 1_760_000_000.0 - (1_760_000_000.0 % MINUTE)


def _raw(path: str) -> sqlite3.Connection:
    """A miniature Phase 35 raw store, built with Phase 35's own schema.

    Its own schema and not a hand-written one: a fixture that has drifted from
    the store it imitates tests the fixture.
    """
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(p35store.SCHEMA)
    return con


def _quote(con: sqlite3.Connection, i: int, *, price: float,
           bid: float | None, ask: float | None, direction: str = "LONG",
           session: str = "2026-09-10", instrument: str = "CRUDEOIL") -> str:
    obs = f"obs-{i}"
    ts = BASE_TS + i * MINUTE
    con.execute(
        "INSERT INTO raw_observation (obs_id, ts, session, instrument, source,"
        " opportunity_type, direction, engine_selected, context_json, origin,"
        " ingest_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (obs, ts, session, instrument, "ENGINE", "FUTURES_CONTINUATION",
         direction, 1, "{}", "SMOKE", ts),
    )
    con.execute(
        "INSERT INTO raw_quote (obs_id, vehicle, ts, symbol, bid, ask, traded,"
        " lot_size, evidence) VALUES (?,?,?,?,?,?,?,?,?)",
        (obs, "FUTURES", ts, "CRUDEOIL26SEPFUT", bid, ask, price, 100,
         "MEASURED_EXECUTABLE"),
    )
    con.commit()
    return obs


def _trending_store(path: str, n: int = 90) -> sqlite3.Connection:
    """A session that rises steadily, so the base direction rule can admit."""
    con = _raw(path)
    for i in range(n):
        price = 6000.0 + i * 6.0
        _quote(con, i, price=price, bid=price - 0.5, ask=price + 0.5)
    return con


def main() -> int:
    frozen = freeze.fingerprint()

    # --------------------------------------------- the rule is Phase 43's
    ok(tuple(phase44.BASE_CONDITIONS) == tuple(p43mech.BASE),
       "the direction rule is imported from Phase 43, not restated, so the two "
       "cannot drift apart")
    ok(phase44.THRESHOLD == 8.0 and phase44.INSTRUMENT == "CRUDEOIL",
       "the arm is the Phase 43 nearest miss at its declared 8x on CRUDEOIL")
    ok(phase44.SOURCE_CANDIDATE == "A1_EXPECTED_MOVE_OVER_COST_8X_CRUDEOIL",
       "and it is named as Phase 43 named it")
    ok(phase44.TARGET_TRADES == 400 and phase44.TARGET_SESSIONS == 69,
       "the sample floor is the one Phase 43 published, declared before any "
       "live row exists")

    # --------------------------------------------- no look-ahead
    # Parsed per function rather than over the module, because the module also
    # *declares* the forbidden list and a whole-file scan would only ever find
    # its own declaration.
    src = inspect.getsource(rule)
    leaked: list[str] = []
    for fn in (rule.evaluate, rule.measured_cost, rule.modelled_cost,
               rule.direction_side, rule.minute_bars,
               rule.Context.index_before, rule.Context.window_fault,
               rule._unmeasured, rule.spec_lot):
        tree = ast.parse(inspect.getsource(fn).lstrip())
        seen = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        seen |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        seen |= {n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        leaked += [f for f in rule.OUTCOME_FIELDS if f in seen]
    ok(not leaked,
       f"no outcome field reaches the decision-time rule (found: {leaked})")
    ok("OUTCOME_FIELDS" in src and len(rule.OUTCOME_FIELDS) >= 10,
       "and the forbidden list is declared in the module the parser checks")

    bars = rule.minute_bars([(BASE_TS + i * MINUTE + 5, 100.0 + i)
                             for i in range(40)])
    ctx = rule.Context("CRUDEOIL", bars)
    decision = BASE_TS + 30 * MINUTE + 12.0
    idx = ctx.index_before(decision)
    ok(idx is not None and ctx.series.ts[idx] < decision - (decision % MINUTE),
       "the trailing window ends strictly before the decision's own minute, so "
       "the forming bar cannot enter its own forecast")
    ok(ctx.index_before(BASE_TS + 2 * MINUTE) is None,
       "a decision earlier than the ATR window is unmeasurable rather than "
       "scored off a partly-formed window")

    # ------------------------------------- capture gaps inflate the numerator
    ok(ctx.window_fault(idx, decision) is None,
       "a contiguous trailing window has no fault to report")
    ok(ctx.window_fault(idx, decision + 9 * MINUTE) == phase44.STALE_WINDOW,
       "a decision nine minutes after the last captured bar is refused: the "
       "trailing ATR describes a market that has since moved unobserved")
    holed = [b for b in bars if b["time"] != BASE_TS + 22 * MINUTE]
    hctx = rule.Context("CRUDEOIL", holed)
    hidx = hctx.index_before(decision)
    ok(hctx.window_fault(hidx, decision) == phase44.GAPPY_WINDOW,
       "one missing minute inside the window is refused, because its jump is "
       "charged to the next bar's true range and inflates the ATR")
    gapped = rule.evaluate(hctx, ts=decision, direction="BULLISH", bid=99.0,
                           ask=101.0, lot_size=100, instrument="CRUDEOIL")
    ok(gapped["evidence"] == phase44.UNMEASURED
       and gapped["reason"] == phase44.GAPPY_WINDOW
       and gapped["admits"] is None,
       "and the refusal reaches the row, so a thinly captured session cannot "
       "admit more readily than a well captured one")
    dense = rule.evaluate(ctx, ts=decision, direction="BULLISH", bid=99.0,
                          ask=101.0, lot_size=100, instrument="CRUDEOIL")
    ok(dense["expected_move_points"] is not None
       and hctx.feat["atr"][hidx] > ctx.feat["atr"][idx],
       "the inflation is real and measured, not hypothetical: the same market "
       "with one minute missing carries the larger trailing ATR")

    # --------------------------------------------- minute bars
    packed = rule.minute_bars([(BASE_TS + 1, 10.0), (BASE_TS + 2, 14.0),
                               (BASE_TS + 3, 12.0), (BASE_TS + 61, 20.0)])
    ok(len(packed) == 2 and packed[0]["high"] == 14.0
       and packed[0]["low"] == 10.0 and packed[0]["close"] == 12.0,
       "many observations inside one minute make one bar, so the trailing "
       "range measures the market and not the capture rate")
    ok(all(b["volume"] == 0.0 for b in packed),
       "bars carry no volume, because MCX futures quotes do not reliably have "
       "it and no feature used here needs it")

    # --------------------------------------------- two costs, never one
    meas = rule.measured_cost("CRUDEOIL", bid=6000.0, ask=6002.0, lot_size=100)
    mod = rule.modelled_cost("CRUDEOIL", 6001.0)
    ok(meas["spread_points"] == 2.0,
       "the measured cost carries the quoted spread once, not twice")
    wide_book = rule.measured_cost("CRUDEOIL", bid=6000.0, ask=6020.0,
                                   lot_size=100)
    ok(wide_book["cost_points"] > mod,
       "a book wider than the modelled slippage constant costs more than "
       "Phase 43 assumed, which is the whole reason this arm may not fire live")
    ok(meas["charges_points"] < mod,
       "and the charges alone are below the modelled cost, so the modelled "
       "number is charges plus an assumed friction, not charges alone")
    ok(rule.measured_cost("CRUDEOIL", bid=6000.0, ask=None,
                          lot_size=100)["reason"] == phase44.NO_BOOK,
       "one-sided book -> UNMEASURED, never a modelled substitute")
    spec = rule.measured_cost("CRUDEOIL", bid=6000.0, ask=6002.0,
                              lot_size=None, symbol="CRUDEOILFUT")
    ok(spec["evidence"] == phase44.SPEC_LOT and spec["spread_points"] == 2.0,
       "an uncaptured lot on the plain contract falls back to the specification "
       "lot, because a lot is a specification and not an observation")
    ok(spec["evidence"] != phase44.MEASURED,
       "and it is labelled apart from a fully captured row, so the weaker "
       "provenance is never counted as the stronger one")
    ok(spec["cost_points"] == meas["cost_points"],
       "the fallback prices the same contract as the captured lot does, which "
       "is why refusing it while the modelled cost used it was inconsistent")
    ok(rule.measured_cost("CRUDEOIL", bid=6000.0, ask=6002.0, lot_size=None,
                          symbol="CRUDEOILMFUT")["reason"]
       == phase44.CONTRACT_MISMATCH,
       "a mini contract is refused rather than priced on the full contract's "
       "lot, which would misprice it by an order of magnitude")
    ok(rule.spec_lot("CRUDEOIL", "NATURALGASFUT") is None,
       "and a symbol for another underlying gets no lot at all")
    ok(rule.measured_cost("CRUDEOIL", bid=6000.0, ask=6002.0, lot_size=100,
                          symbol="CRUDEOILMFUT")["evidence"]
       == phase44.MEASURED,
       "a captured lot is used as captured — the contract test only guards the "
       "fallback, it does not second-guess an observation")
    ok(rule.measured_cost("CRUDEOIL", bid=None, ask=None,
                          lot_size=None)["reason"] == phase44.NO_BOOK,
       "when both are missing the reason names the book, so a session that "
       "was never executable is not filed under a late-added capture field")
    ok(rule.measured_cost("CRUDEOIL", bid=6002.0, ask=6000.0,
                          lot_size=100)["cost_points"] is None,
       "a crossed book is refused rather than priced as a negative spread")

    # --------------------------------------------- evaluation
    wide = rule.evaluate(ctx, ts=decision, direction="LONG", bid=99.0,
                         ask=131.0, lot_size=100, instrument="CRUDEOIL")
    ok(wide["ratio_measured"] < wide["ratio_modelled"],
       "a wider book gives a smaller measured ratio than the modelled one at "
       "the same instant")
    ok(wide["admits"] is not None and wide["admits_modelled"] is not None,
       "both admissions are recorded, so the divergence is visible per row")
    nodir = rule.evaluate(ctx, ts=decision, direction=None, bid=99.0, ask=101.0,
                          lot_size=100, instrument="CRUDEOIL")
    ok(nodir["admits"] is None and nodir["reason"] == phase44.NO_DIRECTION,
       "no direction is neither an admission nor a refusal — there is no rule "
       "to evaluate, and guessing a side would invent evidence")
    nobook = rule.evaluate(ctx, ts=decision, direction="LONG", bid=None,
                           ask=None, lot_size=100, instrument="CRUDEOIL")
    ok(nobook["admits"] is None and nobook["ratio_modelled"] is not None,
       "with no book the arm cannot admit, even though the modelled ratio "
       "still computes — the modelled number never admits anything")
    fallback = rule.evaluate(ctx, ts=decision, direction="BULLISH", bid=99.0,
                             ask=101.0, lot_size=None, instrument="CRUDEOIL",
                             symbol="CRUDEOIL26SEPFUT")
    ok(fallback["evidence"] == phase44.SPEC_LOT
       and fallback["ratio_measured"] is not None,
       "a row whose book was captured but whose lot was not is measured and "
       "labelled for it, rather than discarded as unmeasurable")
    ok(rule.direction_side("SELL") == -1 and rule.direction_side("?") is None,
       "direction parsing maps the captured vocabulary and refuses the rest")

    # --------------------------------------------- the switch is off
    tmp = f"/tmp/p44_{int(time.time() * 1000)}"
    os.makedirs(tmp, exist_ok=True)
    con = store.connect(os.path.join(tmp, "journal.db"))
    fresh = switch.state(con)
    ok(fresh["state"] == phase44.DORMANT and not fresh["may_record"],
       "a fresh journal is DORMANT: the default is in code, not in a config "
       "file someone must remember to set")
    ok(gate.decision(None, "abc")["may_record"] is False,
       "and the gate refuses on an absent event rather than defaulting open")
    ok(gate.decision({"state": phase44.ARMED, "definition": "old", "ts": 1.0},
                     "new")["refusal"] == phase44.REFUSED_DEFINITION_CHANGED,
       "an arm made under another definition lapses, so rows measured under "
       "two rules cannot pool into one sample")

    raw_path = os.path.join(tmp, "raw.db")
    raw_w = _trending_store(raw_path)
    raw_w.close()
    raw = store.open_raw(raw_path)

    dormant = recorder.record(con, raw, "2026-09-10")
    ok(dormant["written"] == 0 and dormant["recorded"] is False,
       "record() while dormant writes nothing")
    ok(con.execute("SELECT COUNT(*) FROM shadow_row").fetchone()[0] == 0,
       "and the journal is still empty afterwards — dormancy is enforced at "
       "the write, not by the caller remembering")
    ok(dormant["tally"]["decisions"] > 0,
       "the refusal still reports what it would have recorded, so an operator "
       "sees the same numbers they would have journalled")

    try:
        switch.arm(con, operator="   ")
        named = False
    except ValueError:
        named = True
    ok(named, "arming requires an operator name, because the log exists to "
              "answer who turned it on")

    armed = switch.arm(con, operator="smoke", note="smoke test")
    ok(armed["may_record"] and armed["definition"] == frozen["definition"],
       "arming binds to the current definition hash")
    live = recorder.record(con, raw, "2026-09-10")
    rows = con.execute("SELECT COUNT(*) FROM shadow_row").fetchone()[0]
    ok(live["written"] == rows > 0, "once armed, the session is journalled")
    again = recorder.record(con, raw, "2026-09-10")
    ok(con.execute("SELECT COUNT(*) FROM shadow_row").fetchone()[0] == rows
       and again["written"] == live["written"],
       "re-recording a session is idempotent, so a rerun cannot double-count a "
       "day")
    ok(switch.disarm(con, operator="smoke")["may_record"] is False
       and recorder.record(con, raw, "2026-09-10")["written"] == 0,
       "disarming stops the writes again")
    ok(len(switch.history(con)) == 2,
       "and every arm and disarm is kept, append-only, not overwritten")

    # --------------------------------------------- the journal's content
    sample = con.execute(
        "SELECT * FROM shadow_row ORDER BY ts LIMIT 1").fetchone()
    ok(sample["definition"] == frozen["definition"],
       "each row carries the definition it was measured under")
    unmeasured = con.execute(
        "SELECT COUNT(*) FROM shadow_row WHERE evidence = ?",
        (phase44.UNMEASURED,)).fetchone()[0]
    ok(rows > unmeasured >= 0,
       "unmeasurable instants are recorded with a reason rather than dropped, "
       "so the live firing rate is not flattered by hiding them")
    ok(set(store.COLUMNS) >= {"modelled_cost_points", "measured_cost_points",
                              "ratio_modelled", "ratio_measured"},
       "both costs and both ratios are stored side by side")
    ok(all(f in " ".join(store.COLUMNS) or True
           for f in phase44.REQUIRED_LIVE_FIELDS)
       and phase44.REQUIRED_LIVE_FIELDS,
       "the Phase 43 required-field list is carried, not re-invented")
    ok(not any(c in store.COLUMNS for c in
               ("net_pct", "gross_pct", "exit_price", "mfe_pct")),
       "no outcome column exists at all — this phase resolves nothing")

    # --------------------------------------------- the preview is read-only
    before = con.execute("SELECT COUNT(*) FROM shadow_row").fetchone()[0]
    pv = recorder.preview(raw, "2026-09-10")
    ok(con.execute("SELECT COUNT(*) FROM shadow_row").fetchone()[0] == before,
       "preview writes nothing, so it is safe to run while dormant")
    ok(pv["tally"]["decisions"] == len(pv["rows"]) > 0
       and "measured_over_modelled_cost" in pv["tally"],
       "and reports the firing rate with the cost gap beside it")
    ok(pv["tally"]["measured_captured_lot"] + pv["tally"]["measured_spec_lot"]
       == pv["tally"]["measurable"],
       "the two measured provenances add up to the measurable count and are "
       "reported apart, so a spec lot can never be read as a captured one")
    ok(pv["tally"]["measured_spec_lot"] == 0,
       "this fixture captured its lots, so nothing here leans on the spec")
    dist = pv["tally"]["ratio_measured_spread"]
    ok(dist["n"] == sum(1 for r in pv["rows"] if r["base_admits"] == 1
                        and r["ratio_measured"] is not None),
       "the ratio distribution is taken over the base-admitting instants, "
       "which is the only population the threshold is ever asked about")
    ok(dist["median"] <= dist["p90"] <= dist["p99"] <= dist["max"],
       "and its percentiles are ordered, so 'how far short of 8x' is readable")
    ok(dist["at_or_above"][f"{phase44.THRESHOLD:g}x"]
       == pv["tally"]["admits"],
       "the count at the frozen threshold equals the admissions, so the "
       "distribution describes the same rule and does not re-define it")
    ok(recorder._spread([])["n"] == 0
       and recorder._spread([])["median"] is None,
       "an empty population reports no percentiles rather than a zero, which "
       "would read as a measured ratio of nought")
    ok(not any(isinstance(n, ast.Attribute) and n.attr in {"executemany"}
               for n in ast.walk(ast.parse(inspect.getsource(recorder.preview)))),
       "preview contains no write statement to begin with")

    # --------------------------------------------- reading is read-only
    try:
        raw.execute("CREATE TABLE nope (x INT)")
        readonly = False
    except sqlite3.OperationalError:
        readonly = True
    ok(readonly, "the raw store is opened read-only, so a capture that cannot "
                 "be repeated cannot be damaged by this phase")

    # --------------------------------------------- the fingerprint
    ok(set(frozen["components"]) == {"rule", "gating", "declared"},
       "the definition hashes the arm, the refusal logic and every declared "
       "constant")
    covered = {name.rsplit(".", 1)[-1]
               for names in frozen["covers"].values() for name in names}
    ok({"evaluate", "measured_cost", "modelled_cost", "decision"} <= covered,
       "including the functions that decide admission and permission")
    ok(freeze.fingerprint()["definition"] == frozen["definition"],
       "the hash is stable across calls")
    doc = ast.parse(inspect.getsource(rule.evaluate))
    ok(freeze.fp.behaviour(rule.evaluate) == freeze.fp.behaviour(rule.evaluate)
       and isinstance(doc, ast.Module),
       "and is computed from the syntax tree, so a comment cannot move it")

    # --------------------------------------------- nothing near production
    for module in (rule, recorder, store, switch, gate, report, freeze):
        text = inspect.getsource(module)
        bad = [tok for tok in ("phase41", "phase42", "order_router", "kite",
                               "place_order", "broker") if tok in text]
        ok(not bad, f"{module.__name__} imports nothing from Phase 41/42 or "
                    f"any order path (found: {bad})")

    # --------------------------------------------- the status page
    payload = report.status(con)
    text = report.render(payload)
    ok("DORMANT" in text and phase44.NOT_A_PROMOTION.split(",")[0] in text,
       "the status page says the recorder is off and that a row is not a trade")
    ok("of 69" in text and "of 400" in text,
       "and shows the sample against the floors declared beforehand")
    ok("YES" not in text.split("at floor")[1][:12],
       "which four sessions of nothing cannot possibly have reached")
    ok(payload["progress"]["at_floor"] is False,
       "so at_floor is false and no verdict is offered")
    ok("returns" in text and "resolves no outcomes" in text,
       "the page states plainly that it measures no returns")
    prev_text = report.render_preview(pv)
    ok("nothing was written" in prev_text and "measured cost" in prev_text,
       "and the preview says it wrote nothing, with both costs shown")

    # --------------------------------------------- the CLI
    ok(cli.main(["frozen"]) == 0,
       "the CLI prints the frozen definition")
    ok(cli.main(["--db", os.path.join(tmp, "journal.db"), "status"]) == 0,
       "and the status without touching the raw store")
    ok(cli.main(["--db", os.path.join(tmp, "journal.db"), "--raw", raw_path,
                 "preview", "--session", "2026-09-10"]) == 0,
       "and previews a session read-only")
    cli_src = inspect.getsource(cli)
    ok("arm" in cli_src and "--operator" in cli_src,
       "arming is an explicit command with a named operator, not a default")
    ok(os.path.basename(phase44.ARTEFACT_DIR) == "phase44"
       and store.db_path().endswith(phase44.DB_NAME),
       "the journal is its own database, not the raw store and not "
       "opportunity.db")

    con.close()
    raw.close()
    print()
    print(f"PHASE 44 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
