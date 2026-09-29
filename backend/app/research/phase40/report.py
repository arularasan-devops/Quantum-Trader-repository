"""Phase 40 §15 — the two artefacts, written from the payload only.

The report never recomputes a number. Every figure printed comes from
:func:`app.research.phase40.service.run`, so the Markdown and the JSON cannot
disagree, and a reader checking one against the other is checking the same
arithmetic twice rather than two implementations of it.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.research.phase40 import (
    ADVERSE_FIRST,
    ARTEFACT_DIR,
    BOARD,
    ENGINE,
    FAVOURABLE_FIRST,
    GIVEN_BACK,
    INSUFFICIENT,
    JSON_NAME,
    MATERIALITY_Z,
    MD_NAME,
    NEITHER_REACHED,
    NO_RACEABLE_LEG,
    NOT_A_STRATEGY,
    NOT_IN_STORE,
    RECOVERED,
    RETAINED,
    STAYED_ADVERSE,
    UNCOVERED_WINDOW,
    UNMEASURED,
    VERSION,
)

DASH = "—"


def _f(value: object, suffix: str = "") -> str:
    if value is None:
        return DASH
    if isinstance(value, float):
        return f"{value:g}{suffix}"
    return f"{value}{suffix}"


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|")


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(_cell(h) for h in header) + " |",
           "|" + "|".join(["---"] * len(header)) + "|"]
    if not rows:
        out.append("|" + "|".join([f" {DASH} "] * len(header)) + "|")
    for row in rows:
        out.append("| " + " | ".join(_cell(c) for c in row) + " |")
    out.append("")
    return out


def _horizon_table(block: dict) -> list[str]:
    rows = []
    for row in block["horizons"]:
        rows.append([
            row["horizon"], row["classified"],
            _f(row["favourable_first_pct"], "%"),
            _f(row["adverse_first_pct"], "%"),
            _f(row["neither_pct"], "%"),
            _f(row["uncovered_pct"], "%"),
            _f(row["median_time_to_favourable_min"]),
            _f(row["p75_time_to_favourable_min"]),
            _f(row["median_time_to_adverse_min"]),
            _f(row["p75_time_to_adverse_min"]),
            _f(row["mfe_pct_median"], "%"),
            _f(row["mae_pct_median"], "%"),
            _f(row["gross_pct_median"], "%"),
            _f(row["net_pct_median"], "%"),
            _f(row["profit_factor"]),
            _f(row["win_pct"], "%"),
            _f(row["cost_pct_median"], "%"),
            _f(row["cost_over_move_median"]),
        ])
    return _table(
        ["hold", "classified", "fav-first", "adv-first", "neither",
         "uncovered", "t-fav med", "t-fav p75", "t-adv med", "t-adv p75",
         "MFE", "MAE", "gross", "net", "PF", "win", "cost", "cost/move"],
        rows,
    )


def _giveback_table(block: dict) -> list[str]:
    rows = []
    for row in block["giveback"]:
        rows.append([
            row["horizon"], row["favourable_first"], row[RETAINED],
            row[GIVEN_BACK], _f(row["given_back_pct"], "%"),
            _f(row["peak_pct_median"], "%"),
            _f(row["time_to_peak_min_median"]),
            _f(row["end_pct_median"], "%"),
            _f(row["given_back_fraction_of_mfe_pct_median"], "%"),
            _f(row["net_at_peak_pct_median"], "%"),
            _f(row["net_at_horizon_pct_median"], "%"),
            _f(row["returned_to_entry_pct"], "%"),
            _f(row["turned_negative_pct"], "%"),
        ])
    return _table(
        ["hold", "fav-first", "retained", "given back", "given back %",
         "peak", "t-peak", "end", "% of peak lost", "net at peak",
         "net at hold", "returned to entry", "turned negative"],
        rows,
    )


def _adverse_table(block: dict) -> list[str]:
    rows = []
    for row in block["adverse_first"]:
        rows.append([
            row["horizon"], row["adverse_first"], row[RECOVERED],
            row[STAYED_ADVERSE], _f(row["recovered_pct"], "%"),
            _f(row["median_time_to_adverse_min"]),
            _f(row["mfe_before_adverse_pct_median"], "%"),
            _f(row["mae_pct_median"], "%"),
            _f(row["median_wait_to_favourable_min"]),
            _f(row["p75_wait_to_favourable_min"]),
            _f(row["net_pct_median"], "%"),
            _f(row["recovered_net_pct_median"], "%"),
            _f(row["stayed_net_pct_median"], "%"),
        ])
    return _table(
        ["hold", "adv-first", "later favourable", "never favourable",
         "recovered %", "t-adv med", "MFE before adverse", "MAE",
         "wait to fav med", "wait p75", "net", "net if recovered",
         "net if not"],
        rows,
    )


def _waterfall(block: dict) -> list[str]:
    w = block["waterfall"]
    fav, adv = w["favourable_first"], w["adverse_first"]
    return [
        f"Favourable-first legs at the {w['horizon']}-minute hold "
        f"(n={fav['n']}), median, per one lot:",
        "",
        "```",
        f"FAVOURABLE MOVE   {_f(fav['favourable_move_pct'], '%')}"
        f"   {_f(fav['peak_rupees_one_lot'], ' INR')}",
        "      v",
        f"PEAK              {_f(fav['peak_pct'], '%')}",
        "      v",
        f"GIVEBACK         -{_f(fav['giveback_pct'], '%')}"
        f"  -{_f(fav['giveback_rupees_one_lot'], ' INR')}",
        "      v",
        f"COST             -{_f(fav['cost_pct'], '%')}"
        f"  -{_f(fav['cost_rupees_one_lot'], ' INR')}",
        "      v",
        f"FINAL NET         {_f(fav['final_net_pct'], '%')}"
        f"   {_f(fav['final_net_rupees_one_lot'], ' INR')}",
        "```",
        "",
        "The peak and the giveback are the same measurement read twice: what "
        "the leg offered, and what was left of it. The peak is not an "
        "achievable exit — nothing here knows when it is — so the giveback is "
        "the size of the question, not the size of a missed profit.",
        "",
        f"Adverse-first legs at the same hold (n={adv['n']}), median, per one "
        f"lot:",
        "",
        "```",
        "ENTRY             0%",
        "      v",
        f"ADVERSE MOVE      {_f(adv['adverse_move_pct'], '%')}"
        f"   {_f(adv['adverse_rupees_one_lot'], ' INR')}",
        "      v",
        f"RECOVERY          {_f(adv['recovered_pct_of_legs'], '%')} of these "
        f"legs later reached the favourable threshold",
        "      v",
        f"FINAL NET         {_f(adv['final_net_pct'], '%')}"
        f"   {_f(adv['final_net_rupees_one_lot'], ' INR')}",
        "```",
        "",
        "No stop is applied anywhere in this branch. The adverse threshold is "
        "a measuring line, not an order: a leg that crossed it is followed to "
        "the horizon exactly as one that did not, because applying a stop here "
        "would be testing an exit rule, which §14 forbids.",
        "",
    ]


def _verdict_block(name: str, verdict: dict, alt: dict | None = None) -> list[str]:
    out = [
        f"**{name}: {verdict['verdict']}**",
        "",
        f"- {verdict['because']}",
        f"- classified legs {verdict['classified']}, uncovered windows "
        f"{verdict['uncovered']}, sessions {verdict['sessions']}",
        f"- median time to favourable "
        f"{_f(verdict['median_time_to_favourable_min'])} min, to adverse "
        f"{_f(verdict['median_time_to_adverse_min'])} min",
        f"- median net at the hold {_f(verdict['net_pct_median'], '%')}",
    ]
    if alt is not None:
        agree = alt["verdict"] == verdict["verdict"]
        out += [
            f"- on non-overlapping windows only (n={alt['classified']}): "
            f"{alt['verdict']}, favourable-first "
            f"{_f(alt['favourable_first_pct'], '%')} against adverse-first "
            f"{_f(alt['adverse_first_pct'], '%')}"
            + ("" if agree else " — **the two disagree, and the overlapping "
                                "figure is the one to distrust**"),
        ]
    out.append("")
    return out


def render(payload: dict) -> str:
    lines: list[str] = [
        "# FAVOURABLE-FIRST VS ADVERSE-FIRST",
        "",
        "READ_ONLY. RESEARCH_ONLY. PAPER_ONLY.",
        "NO NEW DATA WAS COLLECTED. NO EXIT, ENTRY, STOP, TARGET, SIZING OR "
        "ORDER PATH WAS TESTED OR CHANGED.",
        "PHASE 35/36 AND PRODUCTION ARE UNCHANGED.",
        "",
        NOT_A_STRATEGY,
        "",
        f"PHASE40_VERSION = {VERSION}",
        f"REFERENCE_HORIZON = {payload['reference_horizon']} minutes "
        f"(pre-declared, Phase 39's)",
        f"SESSIONS = {len(payload['sessions'])} "
        f"({', '.join(payload['sessions']) or DASH})",
        f"LEGS_RACED = {payload['legs']}",
        "",
        "## 1. Executive summary",
        "",
    ]
    lines += _verdict_block(
        "PRIMARY PROBLEM", payload["primary"],
        payload["primary_non_overlapping"],
    )
    lines += [
        "The two thresholds are the same size, and that size is Phase 39's "
        "own break-even move — spread plus brokerage plus statutory charges, "
        "per instant, from that instant's book. Neither was chosen after "
        "seeing an outcome, and neither is larger than the other: an "
        "asymmetric pair would decide the race before running it.",
        "",
        "The race is run on executable prices, and on one side of the book at "
        "both ends: where the leg could have been closed at the entry instant "
        "against where it could have been closed later — bid-to-bid for a "
        "long, ask-to-ask for a short future. No midpoint, LTP, nearest "
        "timestamp, interpolated or synthetic price is read.",
        "",
        "That origin corrects an error in the first version of this phase, "
        "and it reversed its answer. The threshold already contains the "
        "quoted width, so racing from the entry *fill* put the width inside "
        "the movement as well: a market that never moved at all stood a full "
        "spread down and tripped the adverse threshold on its first quote. "
        "The favourable side paid the spread twice and the adverse side got "
        "it free, which manufactured adverse-first legs out of a flat book. "
        "Measured exit side to exit side, the width is charged once — in the "
        "threshold — and a market that did not move scores zero. The money "
        "columns are still counted from the fill, because that is what was "
        "paid, so a leg can clear its favourable threshold and still be "
        "worth little after fees.",
        "",
        "A quote is a single book, so the first-event answer needs no "
        "assumption about the order of the high and the low inside a bar — the "
        "usual way a first-event count flatters itself.",
        "",
        "A leg whose first event happened before its quotes ran out keeps its "
        "place in the shares at every longer hold — nothing arriving later can "
        "precede it — but its money is excluded from that hold's economics and "
        "counted as `truncated`, because a net measured at minute two is not a "
        "thirty-minute net.",
        "",
        f"Where a window ran past the last captured quote the leg is "
        f"{UNCOVERED_WINDOW} and is excluded from the shares, because missing "
        f"data is not evidence that the market stood still.",
        "",
    ]

    cross = payload["cross_instrument"]
    lines += ["## 2. Cross-instrument comparison", ""]
    lines += _table(
        ["instrument", "legs", "sessions", "fav-first", "adv-first",
         "given back", "net median", "verdict"],
        [[
            c["instrument"], c["n"], c["sessions"],
            _f(c["favourable_first_pct"], "%"),
            _f(c["adverse_first_pct"], "%"), _f(c["given_back_pct"], "%"),
            _f(c["net_pct_median"], "%"), c["verdict"],
        ] for c in cross],
    )
    if len(cross) <= 1:
        lines += [
            "One instrument is present in this store, so there is no "
            "cross-instrument finding.",
            "",
        ]

    unmeasured = payload["unmeasured_instruments"]
    if unmeasured:
        lines += ["### Named but unmeasured", ""]
        lines += _table(
            ["instrument", "status", "why", "verdict"],
            [[u["instrument"], u["status"], u["reason"], u["verdict"]]
             for u in unmeasured],
        )
        lines += [
            f"`{NOT_IN_STORE}` means the capture never held the instrument "
            f"and `{NO_RACEABLE_LEG}` means it did and no instant could be "
            "priced and followed. Neither is a finding "
            "about the instrument, and neither is thin data — the rows are "
            "printed so that an absent instrument cannot be read as one that "
            "was examined and failed.",
            "",
        ]

    for inst, block in payload["instruments"].items():
        lines += [f"## 3. {inst}", ""]
        lines += _verdict_block(f"{inst} verdict", block["verdict"])
        lines += ["### Thresholds as applied", ""]
        lines += _table(
            ["vehicle", "n", "COST_CLEARING_MOVE", "adverse move", "source"],
            [[
                vehicle, t["n"],
                _f(t["cost_clearing_move_pct_median"], "%"),
                _f(t["adverse_move_pct_median"], "%"), t["source"],
            ] for vehicle, t in sorted(block["thresholds"].items())],
        )
        for vehicle, vb in block["vehicles"].items():
            lines += [f"### {inst} {vehicle}", ""]
            lines += _verdict_block(
                f"{vehicle} verdict", vb["verdict"],
                vb["verdict_non_overlapping"],
            )
            lines += ["#### §6 first event and economics by hold", ""]
            lines += _horizon_table(vb)
            lines += [
                f"`{FAVOURABLE_FIRST}`, `{ADVERSE_FIRST}` and "
                f"`{NEITHER_REACHED}` are shares of the classified legs; "
                "`uncovered` is a share of all legs. Every price column is the "
                "executable frame — the fill Phase 35 says was available in, "
                "the side that could have been hit out — so MFE, MAE and gross "
                "already carry the quoted width, and net charges brokerage, "
                "statutory and slippage on top of it. No midpoint is read "
                "anywhere in this phase.",
                "",
                "#### §7 giveback, for favourable-first legs only",
                "",
            ]
            lines += _giveback_table(vb)
            lines += [
                f"`{RETAINED}` and `{GIVEN_BACK}` split on the sign of the "
                f"net at the hold — the leg either ended above its own round "
                f"trip or it did not. That is a measured sign rather than a "
                f"chosen giveback percentage; the fraction of the peak lost "
                f"is reported beside it as a distribution.",
                "",
                "#### §8 adverse-first, and what happened afterwards",
                "",
            ]
            lines += _adverse_table(vb)
            lines += [
                "Both readings of an adverse-first leg are printed and "
                "neither is assumed: an entry that was early and an entry "
                "that was wrong but rescued by a later swing produce the same "
                "row here, and this phase cannot separate them.",
                "",
                "#### §12 money flow",
                "",
            ]
            lines += _waterfall(vb)

        lines += [f"### {inst} — ENGINE_SELECTED vs BOARD_ONLY (§10)", ""]
        cohorts = block["cohorts"]
        lines += [f"- {cohorts['note']}", ""]
        rows = []
        for cohort in (ENGINE, BOARD):
            cb = cohorts[cohort]
            if cb["n"] == 0:
                rows.append([cohort, 0, UNMEASURED, DASH, DASH, DASH, DASH])
                continue
            for vehicle, sub in cb["by_vehicle"].items():
                row, give = sub["row"], sub["giveback"]
                rows.append([
                    cohort, vehicle, row["classified"],
                    _f(row["favourable_first_pct"], "%"),
                    _f(row["adverse_first_pct"], "%"),
                    _f(give["given_back_pct"], "%"),
                    _f(row["net_pct_median"], "%"),
                ])
        lines += _table(
            ["cohort", "vehicle", "classified", "fav-first", "adv-first",
             "given back", "net median"], rows,
        )

        vc = block["vehicle_comparison"]
        lines += [
            f"### {inst} — vehicle comparison at the same instant (§11)",
            "",
            f"Grouped by observation id, which is Phase 35's identity for one "
            f"decision instant: {vc['instants_with_all_vehicles']} of "
            f"{vc['instants_seen']} instants had all three vehicles quoted "
            f"and covered to the hold. Three separately-sampled populations "
            f"laid side by side would not be a comparison.",
            "",
        ]
        lines += _table(
            ["vehicle", "n", "fav-first", "adv-first", "neither", "MFE",
             "MAE", "net median", "given back", "% of peak lost", "cost"],
            [[
                vehicle, r["n"], _f(r["favourable_first_pct"], "%"),
                _f(r["adverse_first_pct"], "%"), _f(r["neither_pct"], "%"),
                _f(r["mfe_pct_median"], "%"), _f(r["mae_pct_median"], "%"),
                _f(r["net_pct_median"], "%"), _f(r["given_back_pct"], "%"),
                _f(r["given_back_fraction_of_mfe_pct_median"], "%"),
                _f(r["cost_pct_median"], "%"),
            ] for vehicle, r in vc["vehicles"].items()],
        )
        lines += [
            "No vehicle is promoted, preferred or enabled by this table. A "
            "vehicle that reaches its threshold first more often may still be "
            "the worse one to hold, and this diagnostic measures only the "
            "order of two events.",
            "",
        ]

    lines += [
        "## 4. What this cannot say",
        "",
        "- **It does not test an exit.** No trailing stop, no new target, no "
        "new stop and no scale-out was simulated. Applying one would answer a "
        "different question and would need its own holdout.",
        "- **It does not propose an entry.** The instants are Phase 39's "
        "deterministic stride through executable quotes, not a signal.",
        "- **A peak is not an exit.** The giveback is measured against a peak "
        "no rule could have known in advance, so it bounds what any exit "
        "could have saved rather than describing money that was there for the "
        "taking.",
        "- **The legs are not independent.** Overlapping windows from few "
        "sessions describe few price swings many times, so the "
        f"z-comparisons at {MATERIALITY_Z} are optimistic. The "
        "non-overlapping subsample is printed for exactly this reason and no "
        "amount of arithmetic can substitute for more sessions.",
        f"- **`{INSUFFICIENT}` is a real answer here.** An instrument with too "
        "few classified legs is not called MIXED to fill the row.",
        "- **Nothing was enabled.** No gate, sizing rule, strike rule, signal "
        "or order path was read or written.",
        "",
    ]
    return "\n".join(lines)


def write(payload: dict, *, root: Path | str = ".") -> dict:
    out = Path(root) / ARTEFACT_DIR
    out.mkdir(parents=True, exist_ok=True)
    md, js = out / MD_NAME, out / JSON_NAME
    md.write_text(render(payload), encoding="utf-8")
    js.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str),
                  encoding="utf-8")
    return {"md": str(md), "json": str(js)}
