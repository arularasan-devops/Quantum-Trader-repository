"""Phase 41 smoke — the properties that stop a frozen diagnostic from lying.

A freeze is only worth having if it cannot quietly fail open, and an
accumulation view is only worth reading if it cannot make one day look like a
sample. Each check below exists because the opposite mistake is silent:

* a fingerprint that ignores function bodies, which is the exact error that
  produced 69.76% adverse-first and then 51.28% from the same constants;
* a fingerprint that moves on a comment, which trains the reader to ignore it;
* a fingerprint that restates a threshold instead of reading it, so the hash
  agrees while the two copies drift;
* a hashed-function list that silently stops covering a module;
* pooling runs across definitions, which lays two measurements side by side as
  though they were one;
* a ledger that overwrites, letting a definition change disappear;
* a cumulative series that is not chronological, so the answer's history is
  invented;
* a pooled verdict driven by one session and not labelled as such;
* quoting the overlapping frame as the verdict, whose deviate grows with how
  densely a session was sampled and so crosses any threshold on sampling alone;
* a reporting-rule change that leaves no trace, so a headline that moved for
  that reason is read as the market moving;
* a "sessions needed" figure presented as a forecast;
* an unmeasured share printed as zero;
* Phase 41 recomputing a Phase 40 number instead of calling it;
* writing anything at all to the store it reads.

    .venv/bin/python _smoke_phase41.py
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sqlite3
import sys
import tempfile

import _smoke_phase40 as f40
from app.research import phase39, phase40
from app.research.phase35 import CE, FUTURES, LONG, SHORT, store
from app.research.phase40 import (
    ENTRY_DOMINANT,
    EXIT_DOMINANT,
    INSUFFICIENT,
    MIXED,
    REFERENCE,
    UNMEASURED,
    VERDICTS,
)
from app.research.phase40 import firstevent as p40firstevent
from app.research.phase40 import verdict as p40verdict
from app.research.phase41 import (
    ARTEFACT_DIR,
    CHANGED,
    DESCRIPTIVE_FRAME,
    DOMINATED,
    FIRST_RUN,
    GOVERNING_FRAME,
    HORIZON,
    JSON_NAME,
    MD_NAME,
    MIN_CLASSIFIED,
    ONE_SESSION,
    REPORTING_RULE,
    STABLE,
    UNCHANGED,
    UNSTABLE,
    VERSION,
    Z,
    accumulate,
    cli,
    freeze,
    ledger,
    report,
    service,
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


def _docstring_only(x: int) -> int:
    """One sentence."""
    return x + 1


def _docstring_changed(x: int) -> int:
    """A completely different sentence, and a comment below."""
    # this comment did not exist in the other one
    return x + 1


def _body_changed(x: int) -> int:
    """One sentence."""
    return x + 2


def _legs_for(session: str, *, fav: int, adv: int, cohort: str = f40.BOARD,
              vehicle: str = CE, direction: str = LONG) -> list[dict]:
    """Legs whose first event is decided by the prices, not by a label.

    The counts are produced by walking real quote series through Phase 40, so a
    check on the shares below is a check on the measurement rather than on an
    assignment in this file.
    """
    out = []
    up = [100.0, 100.6, 101.4, 102.4, 103.4]
    down = [100.0, 99.4, 98.6, 97.6, 96.6]
    for i in range(fav):
        out.append(f40._leg(
            mids=up, threshold=0.6, vehicle=vehicle, direction=direction,
            session=session, cohort=cohort, obs_id=f"{session}-f{i}",
        ))
    for i in range(adv):
        out.append(f40._leg(
            mids=down, threshold=0.6, vehicle=vehicle, direction=direction,
            session=session, cohort=cohort, obs_id=f"{session}-a{i}",
        ))
    return out


def main() -> int:  # noqa: PLR0915 - one flat list of independent checks
    # ---------------------------------------------------------------- freeze
    first = freeze.fingerprint()
    ok(first == freeze.fingerprint(),
       "the fingerprint is deterministic: hashing the same definition twice "
       "gives the same hash")
    ok(len(first["definition"]) == 16 and set(first["components"]) == {
        "race", "cost", "money", "test", "declared"},
       "one overall hash plus one per component, so a mismatch says which "
       "part of the definition moved")

    ok(freeze._behaviour(_docstring_only)
       == freeze._behaviour(_docstring_changed),
       "rewriting a docstring or adding a comment does not move the hash — "
       "prose gets corrected after a reader misunderstands a number, and that "
       "must not invalidate the sessions measured under it")
    ok(freeze._behaviour(_docstring_only) != freeze._behaviour(_body_changed),
       "changing a function *body* does move the hash: the race origin moved "
       "inside a body with every constant untouched, and that is the failure "
       "this exists to catch")

    declared = freeze.declared()
    ok(declared["reference_horizon"] == phase40.REFERENCE
       and declared["materiality_z"] == phase40.MATERIALITY_Z
       and declared["horizons"] == list(phase40.FIRST_EVENT_HORIZONS)
       and declared["max_instants_per_session_key"]
       == phase39.MAX_MOVE_INSTANTS_PER_SESSION_KEY,
       "the declared numbers are read from Phase 39/40, not restated here: a "
       "copy of a threshold would be a second threshold the hash could not "
       "see drift")

    was_z = phase40.MATERIALITY_Z
    try:
        phase40.MATERIALITY_Z = 2.58
        moved = freeze.fingerprint()
    finally:
        phase40.MATERIALITY_Z = was_z
    ok(moved["definition"] != first["definition"]
       and moved["components"]["declared"] != first["components"]["declared"]
       and moved["components"]["race"] == first["components"]["race"],
       "changing a declared threshold moves the overall hash and the declared "
       "component, and nothing else — the diagnosis stays specific")
    ok(freeze.fingerprint() == first,
       "and restoring it restores the hash, so the freeze tracks the "
       "definition rather than accumulating drift")

    listed = {name for names in first["covers"].values() for name in names}
    for module in (p40firstevent, f40.metrics, p40verdict):
        for name, fn in vars(module).items():
            if not inspect.isfunction(fn) or fn.__module__ != module.__name__:
                continue
            ok(f"{module.__name__}.{name}" in listed,
               f"{module.__name__}.{name} is inside the fingerprint — a "
               f"function that can change a number must not be able to change "
               f"it invisibly")

    ok(not any("report" in n or "cli" in n for n in listed),
       "rendering and CLI code is outside the fingerprint: it cannot change a "
       "measured number, and a hash that moves for cosmetic reasons gets "
       "ignored")

    serialised = freeze._behaviour(p40verdict.classify)
    ok(not any(f"{field}=" in serialised
               for field in freeze.VERSION_SPECIFIC),
       "the serialised tree carries no field that only some interpreters "
       "have: 3.13 adds fields 3.10 never had, and hashing them makes two "
       "boxes disagree about identical code")
    ok(freeze._canonical(ast.parse("a, b = (1, 2)"))
       == freeze._canonical(ast.parse("(a, b) = (1, 2)")),
       "the same tree serialises the same way however it was spelled — "
       "ast.unparse parenthesises this pair differently across versions, "
       "which is why the serialisation is written out here")
    ok(first["interpreter"] == "%d.%d.%d" % sys.version_info[:3]
       and first["interpreter"] not in first["definition"],
       "the interpreter is recorded for diagnosis and is not hashed: the "
       "fingerprint must answer 'is this the same definition', not 'is this "
       "the same machine'")

    # ----------------------------------------------------------- accumulation
    a = _legs_for("2026-09-07", fav=44, adv=36)
    b = _legs_for("2026-09-08", fav=44, adv=36)
    c = _legs_for("2026-09-09", fav=44, adv=36)
    mixed_legs = a + b + c

    ok(accumulate.sessions_of(mixed_legs)
       == ["2026-09-07", "2026-09-08", "2026-09-09"],
       "sessions come back in date order, so the cumulative series is the "
       "answer's actual history and not an arbitrary one")

    built = accumulate.build(mixed_legs)
    labels = [r["label"] for r in built["all_legs"]["cumulative"]]
    ok(labels == ["2026-09-07..2026-09-07", "2026-09-07..2026-09-08",
                  "2026-09-07..2026-09-09"],
       "each cumulative row is every session up to and including one date, "
       "never a later session leaking into an earlier answer")
    sizes = [r["legs"] for r in built["all_legs"]["cumulative"]]
    ok(sizes == sorted(sizes) and sizes[-1] == len(mixed_legs),
       "the cumulative sample only grows, and the last row is the whole store")
    ok([r["label"] for r in built["all_legs"]["per_session"]]
       == accumulate.sessions_of(mixed_legs)
       and sum(r["legs"] for r in built["all_legs"]["per_session"])
       == len(mixed_legs),
       "the per-session rows partition the legs: every leg is in exactly one "
       "session's row")

    pooled = built["all_legs"]["pooled"]
    ok(pooled == p40verdict.classify(mixed_legs, horizon=REFERENCE),
       "the pooled verdict is Phase 40's own classify over the same legs, not "
       "a second implementation of it")
    ok(all(r["verdict"] in VERDICTS
           for r in built["all_legs"]["cumulative"]
           + built["all_legs"]["per_session"]),
       "every row carries one of Phase 40's declared verdicts")

    loo = built["all_legs"]["leave_one_out"]
    ok(len(loo) == 3 and all(
        r["legs"] == len(mixed_legs) - 80 for r in loo),
       "leave-one-out withholds exactly one session's legs at a time")
    ok(accumulate.leave_one_out(a) == [],
       "with one session there is nothing to leave out, and an empty table is "
       "the honest answer rather than a row comparing a session with itself")

    ok(accumulate.stability(pooled, [], [])["stability"] == ONE_SESSION,
       "an empty store is not a stable answer")
    ok(accumulate.stability(
        pooled, built["all_legs"]["per_session"][:1], [],
    )["stability"] == ONE_SESSION,
       "one session is labelled a measurement, not a sequence")

    even = accumulate.stability(pooled, built["all_legs"]["cumulative"], loo)
    ok(even["stability"] == STABLE
       and even["cumulative_verdicts"] == [r["verdict"] for r in
                                           built["all_legs"]["cumulative"]],
       "a verdict that held as each session was added, and survived every "
       "removal, is STABLE — and the series it is based on travels with it")

    flip = accumulate.stability(
        {"verdict": MIXED},
        [{"verdict": ENTRY_DOMINANT}, {"verdict": MIXED}],
        [],
    )
    ok(flip["stability"] == UNSTABLE and "ENTRY" in flip["because"],
       "a verdict that changed while the sample grew is UNSTABLE and names "
       "what it read before")
    ok(accumulate.stability(
        {"verdict": MIXED}, [{"verdict": MIXED}, {"verdict": MIXED}],
        [{"verdict": EXIT_DOMINANT, "label": "without 2026-09-08"}],
    )["stability"] == DOMINATED,
       "a pooled verdict that flips when one session is dropped is DOMINATED: "
       "it is that session's answer wearing the sample's clothes")
    ok(accumulate.stability(
        {"verdict": MIXED}, [{"verdict": MIXED}, {"verdict": MIXED}],
        [{"verdict": INSUFFICIENT, "label": "without 2026-09-08"}],
    )["stability"] == STABLE,
       "a leave-one-out row that merely runs out of sample is not evidence of "
       "dominance — too few legs to answer is not a different answer")

    res = accumulate.resolution(pooled)
    n = pooled["race_test"]["n"]
    z = abs(pooled["race_test"]["z"]) or None
    ok(res["measurable"] and not res["already_material"]
       and res["classified_now"] == n and z
       and res["classified_needed_at_this_share"]
       == int(n * (Z / z) ** 2) + 1,
       "the sample-needed figure is the deviate's own arithmetic at the share "
       "observed, n*(Z/z)^2")
    ok("not a prediction" in res["note"]
       and "never reaches the threshold" in res["note"],
       "and it is labelled arithmetic rather than a forecast, including that "
       "an even split never resolves at any sample size")
    ok(accumulate.resolution({"race_test": {"n": 0, "z": None}})["measurable"]
       is False,
       "with nothing raced there is nothing to project, and no number is "
       "invented")
    even_split = accumulate.resolution({"race_test": {"n": 400, "z": 0.0,
                                                      "material": False}})
    ok(even_split["classified_needed_at_this_share"] is None
       and "itself a finding" in even_split["note"],
       "an exactly even split gets no sample target at all: no sample size "
       "resolves it, and it is a finding rather than a delay")
    material = accumulate.resolution({
        "race_test": {"n": 900, "z": 4.2, "material": True},
    })
    ok(material["already_material"] and "already outside" in material["note"],
       "a split already outside the declared deviate is not given a sample "
       "target")

    indep = built["non_overlapping"]
    ok(indep["pooled"] == p40verdict.classify(
        p40firstevent.independent(mixed_legs, horizon=HORIZON),
        horizon=HORIZON),
       "the non-overlapping frame is Phase 40's own independence filter, "
       "chosen on timestamps alone so no outcome decides which legs survive")
    ok(indep["pooled"]["classified"] <= pooled["classified"],
       "and it is never the larger sample")
    ok(built["frames_agree"] is (
        pooled["verdict"] == indep["pooled"]["verdict"]),
       "whether the two frames agree is read off the two verdicts, not "
       "asserted")
    ok(("same verdict" in built["frames_note"]) is built["frames_agree"]
       and ("not independent draws" in built["frames_note"])
       is not built["frames_agree"],
       "and when they disagree the note says the non-overlapping figure is "
       "the one to trust, because overlapping windows are not independent "
       "draws")
    disagree = accumulate.build(a)
    ok(built["still_unresolved"] and disagree["still_unresolved"],
       "an unresolved answer is flagged from the non-overlapping frame, which "
       "is the honest denominator")

    # ------------------------------------------------- which frame is quoted
    ok(GOVERNING_FRAME == "non_overlapping"
       and DESCRIPTIVE_FRAME == "all_legs"
       and built["governing_frame"] == GOVERNING_FRAME
       and built["reporting_rule"] == REPORTING_RULE,
       "the frame the verdict is quoted from is declared, not implicit")
    ok(built["verdict"] == indep["pooled"]["verdict"]
       and built["verdict_because"] == indep["pooled"]["because"],
       "the published verdict is the non-overlapping frame's, because it is "
       "the only one counting independent trials")
    ok(built["descriptive_verdict"] == pooled["verdict"]
       and built["stability"] is indep["stability"]
       and built["resolution"] is indep["resolution"],
       "the overlapping verdict is still carried, but under a name that "
       "cannot be mistaken for the answer, and the stability and resolution "
       "quoted at the top are the governing frame's")
    ok(built[GOVERNING_FRAME]["governs_verdict"] is True
       and built[GOVERNING_FRAME]["z_valid_for_testing"] is True
       and built[GOVERNING_FRAME]["z_caveat"] is None,
       "the governing frame says so on itself")
    ok(not [fn for fns in freeze.COMPONENTS.values() for fn in fns
            if "phase41" in fn.__module__],
       "no function Phase 41 owns is inside the definition hash, so choosing "
       "which frame to quote cannot move the fingerprint: the definition is "
       "what was measured, not what was printed")
    ok(built[DESCRIPTIVE_FRAME]["governs_verdict"] is False
       and built[DESCRIPTIVE_FRAME]["z_valid_for_testing"] is False
       and "not a count of independent trials"
       in built[DESCRIPTIVE_FRAME]["z_caveat"],
       "and the descriptive frame carries the reason its deviate is not a "
       "test, on the frame itself rather than in prose someone can drop")

    # The property the whole change exists for: sampling the same session more
    # densely adds no evidence, so it must not be able to move the verdict.
    # Repeating each leg leaves the independence filter's output untouched
    # (same contract, same instant) while tripling the overlapping count.
    dense = accumulate.build(mixed_legs * 3)
    ok(dense[GOVERNING_FRAME]["pooled"] == built[GOVERNING_FRAME]["pooled"]
       and dense["verdict"] == built["verdict"],
       "tripling how densely the same session is sampled changes neither the "
       "non-overlapping figures nor the verdict: no new fact arrived")
    dz = abs(dense[DESCRIPTIVE_FRAME]["pooled"]["race_test"]["z"])
    bz = abs(pooled["race_test"]["z"])
    ok(dense[DESCRIPTIVE_FRAME]["pooled"]["race_test"]["n"]
       == 3 * pooled["race_test"]["n"]
       and dz > bz and round(dz / bz, 2) == round(3 ** 0.5, 2),
       "while the overlapping deviate grows by exactly sqrt(3) on the same "
       "evidence — which is why it is described and never tested: at a fixed "
       "share it crosses any threshold on sampling density alone")
    ok(dense[DESCRIPTIVE_FRAME]["resolution"].get("already_material") is True
       and not dense[GOVERNING_FRAME]["resolution"].get("already_material"),
       "and on this fixture the overlapping frame becomes 'material' from the "
       "densification alone while the governing frame does not move at all — "
       "the exact way a reader would be handed an edge that is not there")

    # ------------------------------------------------------ dominance in situ
    heavy = (_legs_for("2026-09-07", fav=2, adv=2)
             + _legs_for("2026-09-08", fav=90, adv=2))
    hb = accumulate.build(heavy)
    ok(hb["all_legs"]["per_session"][0]["verdict"] == INSUFFICIENT,
       "a session with fewer classified legs than the declared floor of "
       f"{MIN_CLASSIFIED} says INSUFFICIENT_DATA rather than guessing")
    ok(hb["all_legs"]["stability"]["stability"] in (
        STABLE, UNSTABLE, DOMINATED),
       "a two-session store gets a sequence label, not the one-session excuse")

    # ---------------------------------------------------------------- ledger
    with tempfile.TemporaryDirectory() as tmp:
        ok(ledger.read(tmp) == [],
           "a missing ledger reads as empty rather than failing")
        state = ledger.compare([], first)
        ok(state["state"] == FIRST_RUN and state["changed_components"] == [],
           "the first run under a definition is the baseline, not a change")

        payload = {
            "sessions": ["2026-09-07"], "legs": 80,
            "accumulation": {
                "verdict": MIXED,
                "all_legs": {"pooled": {"verdict": ENTRY_DOMINANT}},
                "non_overlapping": {"pooled": {"verdict": MIXED}},
            },
        }
        e1 = ledger.append(tmp, fingerprint=first, payload=payload)
        ok(len(ledger.read(tmp)) == 1
           and ledger.read(tmp)[0]["definition"] == first["definition"],
           "a run is recorded with the definition it ran under")
        ok(e1["verdict"] == MIXED
           and e1["verdict_all_legs_descriptive"] == ENTRY_DOMINANT
           and e1["reporting_rule"] == REPORTING_RULE,
           "the recorded verdict is the governing frame's, with the "
           "descriptive one beside it and the rule that chose between them")
        ok(ledger.reporting_rule_state([])["changed"] is False
           and ledger.reporting_rule_state(ledger.read(tmp))["changed"]
           is False,
           "a first run and a run under the same rule are not a rule change")
        older = ledger.reporting_rule_state(
            [{"definition": first["definition"], "verdict": ENTRY_DOMINANT}],
        )
        ok(older["changed"] is True
           and older["previous_reporting_rule"] is None
           and "not because of the market" in older["because"],
           "a run recorded before the rule existed is reported as a rule "
           "change, so a headline that moved for that reason cannot be read "
           "as the market moving")
        ledger.append(tmp, fingerprint=first, payload=payload)
        rows = ledger.read(tmp)
        ok(len(rows) == 2 and rows[0] == e1,
           "the ledger appends and never overwrites: a definition change that "
           "could vanish on the next successful run is the failure it exists "
           "to catch")

        same = ledger.compare(rows, first)
        ok(same["state"] == UNCHANGED
           and same["runs_under_this_definition"] == 3,
           "an identical hash is reported as the same measurement, and counts "
           "the runs made under it")

        drifted = json.loads(json.dumps(first))
        drifted["components"]["race"] = "0000000000000000"
        drifted["definition"] = "ffffffffffffffff"
        changed = ledger.compare(rows, drifted)
        ok(changed["state"] == CHANGED
           and changed["changed_components"] == ["race"]
           and "must not be compared" in changed["because"],
           "a changed race hash refuses comparison with the earlier run and "
           "names the component that moved")
        ok("recomputed under the current definition" in changed["because"],
           "and it says the *sessions* are unaffected — the raw store is "
           "never overwritten, so it is the old artefact that is stale, not "
           "the days")

    # --------------------------------------------------------------- service
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "s.db")
        con = store.connect(db)
        for i, session_mids in enumerate((
            [100.0, 100.7, 101.5, 102.4, 103.4],
            [100.0, 99.3, 98.5, 97.6, 96.6],
        )):
            f40._series(con, instrument="CRUDEOIL", vehicle=CE,
                        symbol=f40.CE_SYM, mids=session_mids, spread=0.4,
                        tag=f"p41-{i}", engine_selected=bool(i))
            f40._series(con, instrument="CRUDEOIL", vehicle=FUTURES,
                        symbol=f40.FUT_SYM, mids=session_mids, spread=0.4,
                        direction=SHORT, tag=f"p41f-{i}")
        con.commit()

        before = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in store.RAW_TABLES}
        payload = service.run(con)
        after = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in store.RAW_TABLES}
        ok(before == after,
           "the run leaves every raw table exactly as it found it")
        ok("recorded" not in payload,
           "with no directory given, nothing is written at all — the API and "
           "the read-only checks can run the diagnostic without recording it")

        ok(payload["version"] == VERSION
           and payload["frozen_definition"]["definition"]
           == first["definition"]
           and payload["reference_horizon"] == REFERENCE
           and payload["materiality_z"] == Z,
           "the payload carries the fingerprint and the declared parameters, "
           "so a reader never has to ask which definition produced it")
        ok(payload["definition_state"]["state"] == FIRST_RUN,
           "with no ledger directory there is no history, so the run reports "
           "itself as the first under this definition")
        ok(payload["mode"] == ["RESEARCH_ONLY", "PAPER_ONLY",
                              "READ_ONLY_NO_CAPTURE_NO_PRODUCTION_CHANGE"]
           and "NOT_A_STRATEGY" in payload["not_a_strategy"],
           "and it says what it is not")

        raced = p40firstevent.legs(con)
        ok(payload["legs"] == len(raced["legs"])
           and payload["accumulation"]["all_legs"]["pooled"]
           == p40verdict.classify(raced["legs"], horizon=REFERENCE),
           "Phase 41's figures are Phase 40's figures over the same legs: if "
           "these could differ, one of the two would be a reimplementation "
           "and the freeze would be worthless")

        names = [r["instrument"] for r in payload["per_instrument"]]
        ok(names == ["CRUDEOIL"],
           "instruments are kept separate and ordered as Phase 40 orders them")
        ok(all("cumulative" in r and "resolution" in r
               for r in payload["per_instrument"]),
           "each instrument gets its own sequence, so a market-wide answer "
           "cannot hide one instrument settling and another not")

        recorded = service.run(con, directory=os.path.join(tmp, "led"))
        ok(recorded["recorded"]["definition"] == first["definition"]
           and recorded["recorded"]["sessions"] == recorded["sessions"],
           "when a directory is given the run is recorded with what it saw")
        again = service.run(con, directory=os.path.join(tmp, "led"))
        ok(again["definition_state"]["state"] == UNCHANGED
           and len(again["definition_history"]) == 1,
           "the second run compares itself against the first and finds the "
           "definition unchanged")
        ok(not os.path.exists(os.path.join(tmp, "led", "opportunity.db")),
           "the ledger is written beside the artefacts, never into the store: "
           "a diagnostic that appends to the evidence it reads cannot be "
           "re-run against it")

        # ------------------------------------------------------------ report
        md = report.render(payload)
        ok(first["definition"] in md.split("## 1.")[0],
           "the definition hash is above the numbers, not in an appendix — a "
           "reader comparing two tables needs it before the figures")
        for phrase in ("READ_ONLY", "RESEARCH_ONLY",
                       "NO PHASE 40 DEFINITION WAS CHANGED"):
            ok(phrase in md, f"the document states {phrase}")
        ok("Non-overlapping windows only" in md
           and "not independent draws" in md,
           "the overlapping caveat sits beside the overlapping table rather "
           "than in a footnote")
        ok(md.index("Non-overlapping windows only")
           < md.index("Every raced leg"),
           "and the frame that is a test is printed first: reading the "
           "overlapping table first is what makes a sampling artefact look "
           "like a finding")
        ok("descriptive only, not a test" in md
           and "not a count of independent trials" in md
           and "No resolution figure is projected for this frame" in md,
           "the overlapping table is labelled not-a-test and is given no "
           "sample target, which would otherwise read as a schedule to a "
           "finding")
        ok(f"REPORTING_RULE = {REPORTING_RULE}" in md.split("## 1.")[0],
           "and which frame the verdict came from is stated above the "
           "numbers, beside the definition hash")
        ok("an unresolved answer resolve" in md
           and "frozen and wrong" in md,
           "the document says what it cannot do, including that a frozen "
           "definition can still be the wrong definition")
        ok("It proposes nothing" in md,
           "and that it proposes no exit, entry, vehicle or gate")

        empty = report.render({
            **payload,
            "accumulation": accumulate.build([]),
            "per_instrument": [],
            "sessions": [], "legs": 0,
        })
        ok("| 0% |" not in empty and "| — |" in empty,
           "an empty store renders empty rows, never a 0% share — a zero in "
           "a share column reads as 'it never happened' when the truth is "
           "'nobody looked'")
        ok(report._f(None) == UNMEASURED and report._f(None, "%") == UNMEASURED
           and report._f(0.0, "%") == "0%",
           "and a missing figure inside a row prints as UNMEASURED, kept "
           "distinct from a measured zero")

        written = report.write(payload, root=tmp)
        ok(os.path.exists(os.path.join(tmp, ARTEFACT_DIR, MD_NAME))
           and os.path.exists(os.path.join(tmp, ARTEFACT_DIR, JSON_NAME)),
           "both artefacts are written under the phase's own directory")
        with open(written["json"], encoding="utf-8") as fh:
            js = json.load(fh)
        ok(js["frozen_definition"]["definition"] == first["definition"],
           "the JSON artefact is stamped with the definition too, so a stored "
           "result can never be pooled with one from another definition by "
           "mistake")

        # --------------------------------------------------------------- cli
        text = cli.status(payload)
        ok(payload["accumulation"]["verdict"] in text
           and "WHAT WOULD SETTLE IT:" in text,
           "the short read gives the standing answer and what would settle it")
        ok(text.index("STANDING ANSWER") < text.index("DESCRIPTIVE ONLY")
           and "non-overlapping windows" in text.split("\n")[3]
           and "NOT A TEST" in text,
           "the standing answer names the frame it came from and precedes the "
           "overlapping figures, which are marked NOT A TEST where they are "
           "printed rather than in a footnote")
        ok(cli.summary(payload)["verdict"]
           == payload["accumulation"]["verdict"]
           and cli.summary(payload)["all_legs_z_valid_for_testing"] is False
           and cli.summary(payload)["reporting_rule"] == REPORTING_RULE,
           "the machine-readable summary quotes the same frame as the text, "
           "and marks the other one unusable as a test")
        ok("not a forecast" in text or "nothing to settle" in text,
           "and never presents the sample target as a forecast")
        ok(cli.summary(payload)["frozen_definition"] == first["definition"],
           "the summary carries the fingerprint")
        stale = cli.status({
            **payload,
            "definition_state": {"state": CHANGED,
                                 "changed_components": ["race"]},
        })
        ok("must not be compared" in stale,
           "and when the definition has changed, the CLI leads with that "
           "rather than with a number")

        # --------------------------------------------------- read-only proof
        con.close()
        ro = store.connect(db)

        def deny(action, *_args):
            return sqlite3.SQLITE_DENY if action in (
                sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE,
                sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE,
                sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE,
            ) else sqlite3.SQLITE_OK

        ro.set_authorizer(deny)
        try:
            denied = service.run(ro)
            ok(denied["accumulation"]["verdict"]
               == payload["accumulation"]["verdict"],
               "the whole diagnostic runs again with SQLite refusing every "
               "write and reaches the same verdict — read-only is enforced by "
               "execution, not by intention")
        except sqlite3.DatabaseError as exc:  # pragma: no cover - failure path
            ok(False, f"the diagnostic attempted a write: {exc}")
        finally:
            ro.set_authorizer(None)
            ro.close()

    print()
    print(f"PHASE 41 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
