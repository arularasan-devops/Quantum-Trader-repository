"""Phase 48 smoke — the properties that keep an attribution honest.

A funnel is a diagnostic, and the two ways a diagnostic lies are both cheap to
write by accident:

* it **re-decides** instead of describing. If this phase ever computed its own
  direction, its own cost ratio or its own admission, the funnel would be a
  second classifier disagreeing silently with the one that wrote the journal;
* it **becomes a threshold search**. The distance figures say how far the
  refused instants fell short of the gate. The moment a multiple is chosen
  because it would have admitted more of them, the gate is fitted to the sample
  it was measured on — the error the Phase 44 8x arm was abandoned for.

So the checks below hold the phase to: every stage decided from a state and
reason Phase 46 already wrote, the feed-versus-definition split preserved,
counts that add up, no store of its own, no gate of its own, no future field,
no write anywhere, no order path, and Phase 41-47 untouched.

    .venv/bin/python _smoke_phase48.py
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
from pathlib import Path

from app.research import phase45, phase46, phase48
from app.research.phase41 import freeze as p41freeze
from app.research.phase42 import freeze as p42freeze
from app.research.phase43 import freeze as p43freeze
from app.research.phase44 import freeze as p44freeze
from app.research.phase44 import store as p44store
from app.research.phase44 import switch as p44switch
from app.research.phase45 import freeze as p45freeze
from app.research.phase45 import store as p45store
from app.research.phase46 import overlay
from app.research.phase46 import store as p46store
from app.research.phase48 import cli as cli_mod
from app.research.phase48 import funnel
from app.research.phase48 import service

PASS = 0
FAIL: list[str] = []

FROZEN_41 = "34fc64a299d29760"
FROZEN_42 = "b67709af902c8bff"
FROZEN_43 = "e45c3e72ad37f864"
FROZEN_44 = "95d393256ed1f505"
FROZEN_45 = "a3b0b9b0b321ee47"

FORBIDDEN = (
    "order", "broker", "execution", "smartapi", "angel", "kite", "trade_api",
    "place_order", "autobot", "executor",
)

# Fields that exist only after an instant is over. An attribution of why an
# instant was refused may not read one: the refusal happened at the instant.
FUTURE_FIELDS = (
    "mfe_pct", "mae_pct", "giveback_pct", "resolved_ts", "outcome",
    "exit_reason", "realised_pct", "t1_hit", "sl_hit", "net_pct",
)


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


def source(mod: object) -> str:
    return inspect.getsource(mod)  # type: ignore[arg-type]


def fields_read(fn: object) -> set[str]:
    """Every string key the function passes to a ``.get(...)``.

    An AST walk rather than a substring scan, so a field named in a docstring
    or computed into the output is not mistaken for a field consumed as input.
    """
    tree = ast.parse(inspect.getsource(fn))  # type: ignore[arg-type]
    out: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            out.add(node.args[0].value)
    return out


def p44_armed() -> bool:
    """Whether the dormant Phase 44 arm has been switched on. It must not be."""
    path = p44store.db_path()
    if not os.path.exists(path):
        return False
    con = p45store.open_read_only(path)
    try:
        return bool(p44switch.state(con).get("may_record"))
    finally:
        con.close()


def row(state: str, **kw: object) -> dict:
    base = {
        "overlay_state": state,
        "session": "2026-09-17",
        "instrument": "CRUDEOIL",
        "vehicle": phase45.CE,
        "decision_ts": 1.0,
    }
    base.update(kw)
    return base


def main() -> int:  # noqa: C901 - one linear acceptance list, read top to bottom
    # ---------------------------------------------------------- stage mapping
    ok(funnel.stage(row(phase46.SUPPORTED)) == phase48.ADMITTED,
       "an admitted instant lands in ADMITTED")
    ok(funnel.stage(row(phase46.DISAGREES)) == phase48.DIRECTION,
       "a vehicle-disagreement refusal lands in the direction stage")
    ok(funnel.stage(row(phase46.COST_BLOCKED)) == phase48.COST,
       "a cost refusal lands in the cost stage")
    ok(funnel.stage(row(phase46.WATCH)) == phase48.NOT_FRESH,
       "a WATCH row lands in the not-a-fresh-buy stage rather than in a "
       "refusal the research made")
    ok(funnel.stage(row(phase46.STALE)) == phase48.STALE,
       "a stale book lands in the stale stage")
    ok(funnel.stage(row(phase46.CAPTURE_GAP)) == phase48.CAPTURE_GAP,
       "a capture gap lands in the capture-gap stage")
    ok(funnel.stage(row(phase46.NO_RESEARCH_EVIDENCE)) == phase48.NO_EVIDENCE,
       "a missing shadow row lands in the no-evidence stage")

    # The one split, and the reason it exists: UNMEASURED is two different
    # problems wearing one word, and they need opposite remedies.
    ok(funnel.stage(row(phase46.UNMEASURED,
                        research_reason=phase45.NO_DIRECTION)) == phase48.WARMING,
       "an instant with a book but no direction is warm-up, not a missing quote")
    ok(funnel.stage(row(phase46.UNMEASURED,
                        research_reason=phase45.NO_RANGE)) == phase48.WARMING,
       "an instant with no trailing range yet is warm-up")
    ok(funnel.stage(row(phase46.UNMEASURED,
                        research_reason=phase45.NO_QUOTE)) == phase48.NO_BOOK,
       "an instant with no quote for the vehicle is a feed problem")
    ok(funnel.stage(row(phase46.UNMEASURED,
                        research_reason=phase45.ONE_SIDED_BOOK)) == phase48.NO_BOOK,
       "a one-sided book is a feed problem, not warm-up")
    ok(funnel.stage(row(phase46.UNMEASURED,
                        research_reason="SOMETHING_NEW")) == phase48.UNMEASURED_OTHER,
       "an unmeasured reason this phase has never seen is reported as other "
       "rather than silently folded into a stage it might not belong to")
    ok(funnel.stage(row(phase46.UNMEASURED)) == phase48.NO_BOOK,
       "an unmeasured row with no reason at all defaults to the feed side, "
       "which is the reading that does not credit the definition")
    ok(funnel.stage(row("A_STATE_THAT_DOES_NOT_EXIST")) == phase48.UNMEASURED_OTHER,
       "an unknown state is bucketed, never dropped: a funnel that silently "
       "loses rows stops adding up")
    ok(funnel.stage(row(phase46.UNMEASURED, overlay_reason=phase45.NO_DIRECTION))
       == phase48.WARMING,
       "the reason is taken from overlay_reason when research_reason is absent")

    # Order is load-bearing. Phase 46 asks "could the evidence speak" before
    # "what did it say", so a stale row carrying a cost-shaped reason is still
    # stale here — otherwise this phase would report a cost refusal the
    # classifier never made.
    ok(funnel.stage(row(phase46.STALE,
                        research_reason=phase45.BELOW_LIVE_GATE)) == phase48.STALE,
       "a stale row is stale even when its reason mentions the cost gate")

    ok(set(funnel._BOOK_REASONS).isdisjoint(funnel._WARMUP_REASONS),
       "no reason is both a feed problem and a warm-up problem")
    ok(phase48.FEED_STAGES.isdisjoint(phase48.DEFINITION_STAGES),
       "no stage is counted as both a feed failure and a refusal")
    ok(phase48.ADMITTED not in phase48.FEED_STAGES
       and phase48.ADMITTED not in phase48.DEFINITION_STAGES,
       "admission is neither a feed failure nor a refusal")
    ok(set(phase48.STAGES) - {phase48.OBSERVED}
       == phase48.FEED_STAGES | phase48.DEFINITION_STAGES | {phase48.ADMITTED},
       "every stage is on exactly one side of the feed/definition split, so "
       "the two subtotals cannot omit a stage")
    for stage_name in phase48.STAGES:
        if stage_name != phase48.OBSERVED:
            ok(stage_name in phase48.STAGE_NOTES,
               f"{stage_name} carries a note saying what it means")

    # ------------------------------------------------------------- the counts
    rows = (
        [row(phase46.COST_BLOCKED) for _ in range(40)]
        + [row(phase46.UNMEASURED, research_reason=phase45.NO_DIRECTION)
           for _ in range(30)]
        + [row(phase46.DISAGREES) for _ in range(10)]
        + [row(phase46.SUPPORTED) for _ in range(2)]
    )
    summary = funnel.summarise(rows)
    ok(summary["observed"] == 82, "every row given is observed")
    ok(sum(summary["stages"].values()) == 82,
       "the stages add up to the observations — no row counted twice and none "
       "lost")
    ok(summary["feed_could_not_speak"] + summary["definition_said_no"]
       + summary["admitted"] == 82,
       "feed failures, refusals and admissions partition the session")
    ok(summary["stages"][phase48.COST] == 40
       and summary["stages"][phase48.WARMING] == 30,
       "each stage holds its own rows")
    ok(summary["admitted"] == 2, "admissions are counted, not assumed absent")
    ok(isinstance(summary["shares_pct"], dict)
       and abs(sum(summary["shares_pct"].values()) - 100.0) < 0.2,
       "the shares are shares of the observations and sum to 100")

    thin = funnel.summarise([row(phase46.COST_BLOCKED) for _ in range(5)])
    ok(thin["shares_pct"] == phase48.INSUFFICIENT,
       "five rows do not get a percentage: 100% of five rows reads like a "
       "finding and is not one")
    ok(thin["dominant_blocker"] == phase48.DOMINANT_UNDECIDED,
       "five rows do not name a dominant blocker either")
    ok(funnel.summarise([])["observed"] == 0,
       "an empty group is a funnel of zero rather than an error")
    ok(funnel.summarise([])["dominant_blocker"] == phase48.DOMINANT_UNDECIDED,
       "an empty group names no blocker")

    # A dominant blocker is a sentence people act on, so it needs a margin.
    tie = funnel.summarise(
        [row(phase46.COST_BLOCKED) for _ in range(20)]
        + [row(phase46.DISAGREES) for _ in range(19)]
        + [row(phase46.STALE) for _ in range(2)])
    ok(tie["dominant_blocker"] == phase48.DOMINANT_UNDECIDED,
       "a near-tie is undecided rather than a winner by one row")
    clear = funnel.summarise(
        [row(phase46.COST_BLOCKED) for _ in range(60)]
        + [row(phase46.DISAGREES) for _ in range(10)])
    ok(clear["dominant_blocker"] == phase48.COST,
       "a clear majority is named")
    majority_only = funnel.summarise(
        [row(phase46.COST_BLOCKED) for _ in range(30)]
        + [row(phase46.DISAGREES) for _ in range(29)]
        + [row(phase46.STALE) for _ in range(1)])
    ok(majority_only["dominant_blocker"] == phase48.DOMINANT_UNDECIDED,
       "a stage short of half the session is not dominant even when it leads")
    admitted_heavy = funnel.summarise(
        [row(phase46.SUPPORTED) for _ in range(60)]
        + [row(phase46.COST_BLOCKED) for _ in range(5)])
    ok(admitted_heavy["dominant_blocker"] == phase48.DOMINANT_UNDECIDED,
       "admission is never reported as a blocker")

    # ------------------------------------------------------ cost distance
    ok(funnel.distance(
        {"expected_move_over_modelled_cost": 1.5, "gate_multiple": 3.0}) == 0.5,
       "an instant reaching half the gate is reported as half")
    ok(funnel.distance({"expected_move_over_modelled_cost": 1.5}) is None,
       "no gate recorded means no distance, not a distance against a guess")
    ok(funnel.distance({"gate_multiple": 3.0}) is None,
       "no ratio recorded means no distance")
    ok(funnel.distance(
        {"expected_move_over_modelled_cost": 1.0, "gate_multiple": 0.0}) is None,
       "a zero gate yields no distance rather than a division error")
    ok(funnel.distance(
        {"expected_move_over_modelled_cost": True, "gate_multiple": 3.0}) is None,
       "a boolean is not a ratio — bools are ints in Python and would price as 1")
    ok(funnel.distance(
        {"expected_move_over_modelled_cost": None, "gate_multiple": 3.0}) is None,
       "a missing ratio is missing, never zero")
    ok("expected_move_over_measured_cost" not in fields_read(funnel.distance),
       "the distance uses the modelled ratio the gate is applied to, and does "
       "not mix in the measured one: mixing the two bases is the defect that "
       "ruined the Phase 42 holdout")

    profile = funnel.distance_profile([0.1, 0.4, 0.6, 0.95])
    ok(profile["n"] == 4 and profile["median"] == 0.5,
       "the distance profile reports its sample and median")
    ok(profile["at_or_above"]["0.9"] == 1 and profile["at_or_above"]["0.25"] == 3,
       "the bins are cumulative counts of instants reaching that fraction")
    ok(funnel.distance_profile([])["n"] == 0,
       "no cost-refused instant means an empty profile, not a zero median")
    ok(funnel.distance_profile([])["median"] is None,
       "an empty profile has no median rather than 0.0")
    costed = funnel.summarise([
        row(phase46.COST_BLOCKED,
            expected_move_over_modelled_cost=1.5, gate_multiple=3.0),
        row(phase46.DISAGREES,
            expected_move_over_modelled_cost=0.1, gate_multiple=3.0),
    ])
    ok(costed["cost_distance"]["n"] == 1,
       "only the cost-refused instants enter the distance profile: an instant "
       "refused on direction was never measured against the gate")

    # ------------------------------------------------------------- grouping
    grouped = funnel.by_key(
        [row(phase46.COST_BLOCKED, instrument="CRUDEOIL"),
         row(phase46.SUPPORTED, instrument="NIFTY"),
         row(phase46.SUPPORTED, instrument="NIFTY")], "instrument")
    ok(set(grouped) == {"CRUDEOIL", "NIFTY"},
       "groups are kept apart, never pooled across the key")
    ok(grouped["NIFTY"]["observed"] == 2 and grouped["CRUDEOIL"]["admitted"] == 0,
       "each group is funnelled on its own rows")
    ok(list(grouped)[0] == "NIFTY",
       "groups are ordered by size, so the session's bulk is read first")
    ok("UNKNOWN" in funnel.by_key([row(phase46.SUPPORTED, instrument=None)],
                                  "instrument"),
       "a row with no value for the key is bucketed as UNKNOWN rather than "
       "dropped from the totals")
    vehicles = funnel.by_key(
        [row(phase46.SUPPORTED, vehicle=phase45.CE),
         row(phase46.DISAGREES, vehicle=phase45.PE)], "vehicle")
    ok(set(vehicles) == {phase45.CE, phase45.PE},
       "CE and PE stay separate in the funnel, as they do everywhere else")

    # --------------------------------------------------- purity of the module
    src = source(funnel)
    for token in ("sqlite3", "open(", "os.", "time.", "requests", "random"):
        ok(token not in src,
           f"the attribution module does not touch {token} — it is pure, so a "
           f"funnel can be computed from rows in a test without a database")
    ok("import" in src and "classify" not in src,
       "the funnel never calls the Phase 46 classifier: it groups the decision "
       "already recorded, so it cannot disagree with the journal")
    ok("NO_DIRECTION" not in src.split("import")[-1].split("\n\n")[0]
       or "from app.research.phase45 import" in src,
       "the reason vocabulary is imported from Phase 45 rather than respelled, "
       "so a corrected constant cannot leave the funnel counting nothing")
    tree = ast.parse(src)
    numbers = {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
        and not isinstance(n.value, bool)
    }
    ok(not ({3.0, 3, 5, 8} & numbers),
       "the funnel holds no cost multiple of its own: the gate it reports "
       "against is the one on the row")
    for field in FUTURE_FIELDS:
        ok(field not in src,
           f"the funnel never reads {field} — why an instant was refused is "
           f"decided at the instant, not by what happened next")

    # ------------------------------------------- the service: read-only, bound
    svc = source(service)
    # Executable SQL only, gathered from the AST: a word in a docstring is not
    # a statement, and a check that cannot tell the difference fails on its own
    # prose — the lesson the Phase 47 suite already learned.
    statements = [
        node.value.upper() for node in ast.walk(ast.parse(svc))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and any(verb in node.value.upper() for verb in (
            "SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE ", "DROP "))
    ]
    ok(statements, "the service does query the journal")
    ok(all(sql.lstrip().startswith(("SELECT", "AND ", "FROM")) for sql in statements),
       "every statement the service executes is a SELECT: this phase owns no "
       "table and adds no row to anyone else's")
    ok(not any(verb in sql for sql in statements for verb in (
        "INSERT ", "UPDATE ", "DELETE ", "CREATE ", "DROP ")),
       "no write verb appears in any executed statement")
    ok(not os.path.exists(
        os.path.join(os.path.dirname(p46store.db_path()), "phase48.db")),
       "no Phase 48 database is created beside the journal")
    ok(not (Path(__file__).parent / "app/research/phase48/store.py").exists(),
       "there is no Phase 48 store module — a diagnostic with a store invites "
       "recording a conclusion it has not earned")
    ok("?" in svc and "% (" not in svc,
       "the SQL is parameterised")
    for node in ast.walk(ast.parse(svc)):
        if isinstance(node, ast.JoinedStr):
            # f-strings are allowed only to build placeholder marks and column
            # lists — never to interpolate a value into SQL.
            text = ast.unparse(node)
            ok("instrument = ?" in text or "marks" in text or "clause" in text
               or "SELECT" not in text.upper(),
               "no value is interpolated into a query; only placeholders and "
               "a fixed clause are")
    ok("MAX_ROWS" in svc and "truncated" in svc,
       "the read is bounded and a truncated read says so, so a bound can "
       "never look like a smaller session")
    ok("p46service.definition()" in svc,
       "the definition is read from Phase 46, not restated here: rows under a "
       "different definition are a different measurement")

    payload = service.report(options_only=True)
    ok(payload["overall"]["observed"] == payload["rows_attributed"],
       "the funnel attributes exactly the rows it read")
    ok(payload["vehicles"] == [phase45.CE, phase45.PE],
       "the options-only funnel covers the vehicles the call board takes")
    ok(service.report()["vehicles"] == [phase45.FUTURES, phase45.CE, phase45.PE],
       "the default funnel covers all three vehicles: futures dying at a "
       "different stage is itself informative")
    for key in ("not_a_threshold_search", "not_a_result", "production_effect",
                "order_path", "read_only", "definition"):
        ok(bool(payload[key]), f"every payload carries {key}")
    ok(phase48.NO_ORDER_PATH == payload["order_path"],
       "the payload states there is no order path")

    # Determinism, stated so that a live backend appending to the journal
    # between the two reads is not mistaken for a phase that drifts. The
    # attribution itself is proved deterministic on a frozen row set; over the
    # live journal the only claim available is the one that is actually true —
    # a read can gain rows and no stage can lose them, because nothing here
    # reclassifies and nothing here is cached.
    frozen = [row(phase46.COST_BLOCKED, expected_move_over_modelled_cost=1.0,
                  gate_multiple=3.0),
              row(phase46.DISAGREES), row(phase46.SUPPORTED),
              row(phase46.UNMEASURED, research_reason=phase45.NO_DIRECTION)]
    ok(funnel.summarise(frozen) == funnel.summarise(list(frozen)),
       "the attribution of a frozen row set is identical on a second pass — "
       "the phase holds no state of its own to drift")
    # Pinned to the session already read: a session rolling over mid-check is a
    # different population, not a shrinking one.
    again = service.report(options_only=True, session=payload["session"])
    ok(again["overall"]["observed"] >= payload["overall"]["observed"],
       "a later read of a live journal never reports fewer observations")
    ok(all(again["overall"]["stages"][s] >= payload["overall"]["stages"][s]
           for s in phase48.STAGES if s in payload["overall"]["stages"]),
       "no stage count falls between two reads: rows are attributed once and "
       "never reclassified")
    ok(service.report(instrument="NO_SUCH_NAME")["overall"]["observed"] == 0,
       "an instrument with no rows reports zero observations rather than the "
       "whole session's")

    rendered = service.render(payload)
    ok("ADMISSION FUNNEL" in rendered and phase48.NOT_A_RESULT in rendered,
       "the rendered report carries the reading instruction, not just counts")
    ok(phase48.NOT_A_THRESHOLD_SEARCH in rendered,
       "the rendered report says the distance figures are not a proposal")
    for banned in ("PROFITABLE", "WINNING", "VERIFIED EDGE", "RECOMMEND"):
        ok(banned not in rendered.upper(),
           f"the report never says {banned}")

    sessions = service.sessions(limit=3, options_only=True)
    ok(isinstance(sessions["sessions"], list) and "definition" in sessions,
       "the per-session view is a list with its definition attached")
    ok(len(sessions["sessions"]) <= 3, "the session list honours its limit")
    ok(service.sessions(limit=0)["count"] >= 0,
       "a zero limit is clamped rather than passed to SQL")
    st = service.status()
    ok(st["read_only"] == phase48.READ_ONLY and st["stages"] == list(phase48.STAGES),
       "status names what the phase reads and the stages it reports")

    # ------------------------------------------------------------ the CLI
    cli_src = source(cli_mod)
    ok("record" not in cli_src.split("def main")[0].replace("recorded", ""),
       "the CLI has no record subcommand: there is nothing here to record")
    ok(cli_mod.main(["status"]) == 0, "the status subcommand runs")
    ok(cli_mod.main(["sessions", "--limit", "2"]) == 0,
       "the sessions subcommand runs")

    # --------------------------------------------------- isolation, walked
    graph = {
        name for name in sys.modules
        if name.startswith("app.research.phase48")
    }
    ok(graph, "the package is importable")
    reachable: set[str] = set()
    for name in list(sys.modules):
        mod = sys.modules.get(name)
        if mod is None or not name.startswith("app.research.phase48"):
            continue
        for attr in vars(mod).values():
            module_name = getattr(attr, "__module__", None) or getattr(
                attr, "__name__", "")
            if isinstance(module_name, str):
                reachable.add(module_name)
    for token in FORBIDDEN:
        ok(not any(token in name.lower() for name in reachable),
           f"nothing named {token} is reachable from the attribution package")

    # ------------------------------------- the API surface stays read-only
    main_py = (Path(__file__).parent / "app/main.py").read_text()
    block = main_py.split("PHASE 48")[-1] if "PHASE 48" in main_py else ""
    ok(block, "the phase 48 endpoints are present and documented in place")
    ok("@app.get(\"/api/phase48/funnel\")" in main_py,
       "the funnel is served on a GET")
    ok("@app.post(\"/api/phase48" not in main_py
       and "@app.put(\"/api/phase48" not in main_py
       and "@app.delete(\"/api/phase48" not in main_py,
       "there is no mutating phase 48 endpoint of any method")
    ok("_p46_instrument(instrument)" in block,
       "the instrument is validated before it reaches a query")
    ok("run_in_executor" in block,
       "the read runs off the event loop, so a large journal cannot stall the "
       "live feed")

    # ------------------------------------------------------------- frontend
    root = Path(__file__).parent.parent / "frontend"
    tsx_path = root / "components/ResearchFunnelBoard.tsx"
    api_ts = root / "lib/api.ts"
    page = root / "app/page.tsx"
    ok(tsx_path.exists(), "the funnel panel exists")
    if tsx_path.exists():
        tsx = tsx_path.read_text()
        code = "\n".join(
            ln for ln in tsx.splitlines()
            if not ln.lstrip().startswith(("*", "//", "/*"))
        )
        for verb in (">BUY<", "onClick", "<button", "<form", "method: \"POST\""):
            ok(verb not in code,
               f"the funnel panel has no {verb}: it is a diagnostic, and a "
               f"control on it could only mean changing a gate")
        ok("setData(null)" in tsx,
           "a failed refresh clears the funnel rather than leaving a previous "
           "session's attribution on screen claiming to be current")
        ok("not_a_threshold_search" in tsx,
           "the panel shows the not-a-threshold-search label beside the "
           "distance strip, where the temptation actually is")
        ok("feed_could_not_speak" in tsx and "definition_said_no" in tsx,
           "the panel draws the feed-versus-definition split explicitly: they "
           "look identical in an empty results column and need opposite fixes")
        ok("FUNNEL_UNAVAILABLE" in tsx,
           "an unreachable endpoint reads as unavailable rather than as a "
           "session in which nothing was observed")
    if api_ts.exists():
        api = api_ts.read_text()
        ok("getP48Funnel" in api, "the client exposes the funnel read")
        ok("funnel payload has no attribution" in api,
           "the client rejects an error body instead of passing it to render, "
           "which is the bug the overlay panel already had once")
        ok("phase48" in api and "method: \"POST\"" not in api.split("phase48")[-1],
           "there is no phase 48 write call in the client")
    if page.exists():
        body = page.read_text()
        signal_view = body.split("QuantumSignal")[0]
        ok("ResearchFunnelBoard" not in signal_view,
           "the funnel is not mounted in the production signal view")
        ok("ResearchFunnelBoard" in body,
           "the funnel is mounted in the research tab")

    # -------------------------------------------- nothing upstream moved
    ok(p41freeze.fingerprint()["definition"] == FROZEN_41,
       f"Phase 41 fingerprint unchanged ({FROZEN_41})")
    ok(p42freeze.fingerprint()["definition"] == FROZEN_42,
       f"Phase 42 fingerprint unchanged ({FROZEN_42})")
    ok(p43freeze.fingerprint()["components"]["definition"] == FROZEN_43,
       f"Phase 43 definition fingerprint unchanged ({FROZEN_43})")
    ok(p44freeze.fingerprint()["definition"] == FROZEN_44,
       f"Phase 44 fingerprint unchanged ({FROZEN_44})")
    ok(p45freeze.definition() == FROZEN_45,
       f"Phase 45 fingerprint unchanged ({FROZEN_45})")
    ok(not p44_armed(), "Phase 44 is still dormant")
    ok("phase48" not in source(overlay),
       "Phase 46 does not know this phase exists: the dependency runs one way, "
       "so the funnel cannot influence what gets classified")
    ok("phase48" not in main_py.split("PHASE 48")[0],
       "no production endpoint above this block calls the funnel")

    print()
    print(f"PHASE 48 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
