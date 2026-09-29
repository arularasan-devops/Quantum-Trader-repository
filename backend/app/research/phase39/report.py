"""Phase 39 report — one markdown document, and the json behind every figure.

The document is ordered so a reader meets the constraints before the ranking:
coverage first (what the store could answer), then cost, then the required move,
then movement, and only then MOVE/COST. A table of ratios read before its
sample counts is a shortlist; read after them it is a triage list, which is all
it is.

Every section that could be mistaken for a recommendation carries its own
sentence saying what it is not. That is repetition on purpose: the sections get
copied out of the document one at a time.
"""
from __future__ import annotations

import json

from app.research.phase39 import (
    GATE_MULTIPLE,
    MIN_INSTANTS_FOR_LABEL,
    MIN_INSTANTS_FOR_RANK,
    NOT_AN_EDGE,
    REACH_MULTIPLES,
    REFERENCE_HORIZON,
    VERSION,
)
from app.research.phase39 import feasibility as p39feas

HEADER_LINES = (
    "READ_ONLY. RESEARCH_ONLY. PAPER_ONLY.",
    "NO NEW DATA WAS COLLECTED. PHASE 35/36 AND PRODUCTION ARE UNCHANGED.",
    "",
    "THIS IS A COST-COVERAGE MEASUREMENT.",
    "IT IS NOT AN EDGE, A SIGNAL, OR A PROFITABILITY CLAIM.",
)

# The last lines of the document. Nothing is printed after them, so a reader
# who stops at the bottom stops at the caveat rather than at a number.
FOOTER_ACTIONS = ("CONTINUE PAPER", "COLLECT MORE DATA", "REQUIRES DATA FIX")


def _fmt(value: object, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _cell(value: str) -> str:
    # An instrument|vehicle key contains the column separator, which would split
    # the cell and silently shift every column to its right.
    return str(value).replace("|", "\\|")


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(_cell(h) for h in headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    if not rows:
        out.append("| " + " | ".join(["—"] * len(headers)) + " |")
    return out


def _cost_section(state: dict) -> list[str]:
    rows, unpriced = [], []
    for key, row in sorted((state.get("cost") or {}).items()):
        if not row.get("priced"):
            unpriced.append(key)
            continue
        spread = row.get("spread_pct_of_mid") or {}
        req = row.get("required_pct_of_mid") or {}
        comp = row.get("components_pct_of_mid") or {}
        rows.append([
            key,
            str(row.get("executable_quotes")),
            str(len(row.get("sessions") or [])),
            _fmt((row.get("price_mid") or {}).get("median")),
            _fmt(spread.get("median")),
            _fmt(((comp.get("brokerage") or {}).get("median"))),
            _fmt(((comp.get("statutory") or {}).get("median"))),
            _fmt(((comp.get("slippage") or {}).get("median"))),
            _fmt(req.get("median")),
            _fmt((row.get("required_rupees_one_lot") or {}).get("median"), 0),
        ])
    return [
        "## 1. Round-trip cost, from measured books only",
        "",
        "Median values per instrument and vehicle, all as a percent of the "
        "entry mid. `required` is `spread + brokerage + statutory`, charged on "
        "break-even turnover — the favourable move the round trip needs before "
        "the leg is worth anything at all.",
        "",
        *_table(
            ["instrument|vehicle", "exec quotes", "sessions", "mid",
             "spread %", "brokerage %", "statutory %", "slippage %",
             "required move %", "required ₹/lot"],
            rows,
        ),
        "",
        "A wide spread and a large flat fee are different problems with "
        "different answers, so they are never summed into one cost column.",
        "",
        f"{len(unpriced)} further instrument/vehicle pair(s) in the store "
        "could not be priced at all and are absent from this table only, not "
        "from the study — they are listed with their refusal reasons in §2. "
        "An unpriced pair is unmeasured, which is not the same finding as "
        "uneconomic.",
    ]


def _coverage_section(state: dict) -> list[str]:
    cov = state.get("coverage") or {}
    lines = [
        "## 2. What the store could and could not answer",
        "",
        f"- executable quotes read: {cov.get('executable_quotes')}",
        f"- instants sampled: {cov.get('sampled_instants')}",
        f"- instants walked forward: {cov.get('walked')}",
        f"- skipped, no direction recorded: {cov.get('skipped_no_direction')}",
        "- horizons left unanswered because no quote fell inside them: "
        + (", ".join(
            f"{k}={v}" for k, v in (cov.get("unanswered_horizons") or {}).items()
        ) or "none"),
        "",
        "Refusals by reason:",
        "",
    ]
    reasons = cov.get("unwalkable_by_reason") or {}
    lines += _table(
        ["reason", "instants"],
        [[k, str(v)] for k, v in reasons.items()],
    )
    lines += [
        "",
        "A refused instant is left out, never repaired. A quote with one side, "
        "a crossed book, a missing lot size or no forward path in its own "
        "session is unmeasurable — not zero cost and not zero movement.",
        "",
        "Lot size per walked leg came from: "
        + (", ".join(
            f"{k}={v}" for k, v in (cov.get("lot_size_sources") or {}).items()
        ) or "no walked legs"),
        "",
        "A lot size is a contract fact, not a price, so a declared one is used "
        "where a declared price would be refused — but the source is printed "
        "because a registry lot is only as current as the registry, and SEBI "
        "revises them. Nothing else in this study is ever supplied from a "
        "table: no midpoint, no last-traded price, no neighbouring timestamp.",
    ]
    quote_cov = state.get("quote_coverage") or {}
    if quote_cov:
        lines += [
            "",
            "### Why non-executable quotes were refused",
            "",
            *_table(
                ["instrument|vehicle", "quotes", "top refusal reasons"],
                [
                    [
                        key,
                        str(row.get("quotes")),
                        ", ".join(
                            f"{k}={v}" for k, v in list(
                                (row.get("not_executable_by_reason") or {}).items(),
                            )[:3]
                        ) or "—",
                    ]
                    for key, row in sorted(quote_cov.items())
                ],
            ),
        ]
    return lines


def _movement_section(state: dict) -> list[str]:
    rows = []
    for key, row in sorted((state.get("pairs") or {}).items()):
        ref = row.get("reference") or {}
        mfe = ref.get("favourable_move_pct") or {}
        end = ref.get("end_of_horizon_move_pct") or {}
        rows.append([
            key,
            str(ref.get("n")),
            _fmt(mfe.get("p25")),
            _fmt(mfe.get("median")),
            _fmt(mfe.get("p75")),
            _fmt(mfe.get("p90")),
            _fmt(end.get("median")),
            _fmt((ref.get("required_move_pct") or {}).get("median")),
        ])
    return [
        f"## 3. Movement already on disk, at the {REFERENCE_HORIZON}-minute "
        "reference horizon",
        "",
        "Favourable excursion is measured mid-to-mid, so it carries no "
        "execution cost and can be compared against a required move that "
        "carries all of it. `end` is the signed mid move when the horizon "
        "arrived; where it is far below the favourable column, the movement "
        "existed and did not last.",
        "",
        *_table(
            ["instrument|vehicle", "n", "mfe p25", "mfe median", "mfe p75",
             "mfe p90", "end median", "required %"],
            rows,
        ),
        "",
        "MFE is the best exit available with hindsight. It is a ceiling on what "
        "any exit rule could have taken, not what one would have taken.",
    ]


def _ranking_section(state: dict) -> list[str]:
    rank = state.get("ranking") or {}
    multiples = ", ".join(f"{m}x" for m in REACH_MULTIPLES)

    def block(title: str, rows: list[dict], note: str) -> list[str]:
        return [
            f"### {title}",
            "",
            *_table(
                ["#", "instrument|vehicle", "MOVE/COST", "n", "sessions",
                 "required %", "mfe %", f"reach {multiples}", "capability"],
                [
                    [
                        str(i + 1), r["key"], _fmt(r.get("move_over_cost")),
                        str(r.get("n")), str(r.get("sessions")),
                        _fmt(r.get("required_move_pct_median")),
                        _fmt(r.get("favourable_move_pct_median")),
                        " / ".join(
                            _fmt((r.get("reach_rate_pct") or {}).get(f"{m}x"), 1)
                            for m in REACH_MULTIPLES
                        ),
                        r.get("capability", "—"),
                    ]
                    for i, r in enumerate(rows)
                ],
            ),
            "",
            note,
            "",
        ]

    return [
        "## 4. MOVE/COST ranking",
        "",
        "`MOVE/COST` is computed per instant as *favourable mid excursion ÷ "
        "required move*, and the figure below is the median of those per-instant "
        f"ratios at the pre-declared {REFERENCE_HORIZON}-minute horizon. Pairing "
        "the two inside the instant matters: a ratio of two separately-taken "
        "medians would pair the median move with the median spread, which no "
        "instant experienced.",
        "",
        f"Reach columns are the share of instants whose favourable excursion "
        f"reached {multiples} of their own required move. The {GATE_MULTIPLE}x "
        "column is the multiple the existing live flow gate already demands; it "
        "is described here, not chosen here.",
        "",
        *block(
            f"Ranked (n ≥ {MIN_INSTANTS_FOR_RANK} instants)",
            rank.get("ranked") or [],
            "Order is a capability ordering. A high ratio means the movement "
            "was large relative to the cost — it says nothing about whether "
            "any rule could pick the right instants, or the right side.",
        ),
        *block(
            f"Measured, below the ranking floor ({MIN_INSTANTS_FOR_LABEL}–"
            f"{MIN_INSTANTS_FOR_RANK} instants)",
            rank.get("measured_below_rank_floor") or [],
            "Labelled but deliberately not ranked beside the rows above: a "
            "ratio from a few dozen instants of one session describes those "
            "instants.",
        ),
        *block(
            f"Below the label floor (< {MIN_INSTANTS_FOR_LABEL} instants)",
            rank.get("below_label_floor") or [],
            "Not labelled at all. These pairs have not been shown to be "
            "uneconomic; they have been shown to be barely captured.",
        ),
    ]


def _horizon_section(state: dict) -> list[str]:
    rows = []
    for key, row in sorted((state.get("pairs") or {}).items()):
        best = row.get("best_horizon") or {}
        cells = [key]
        for h in ("5", "15", "30", "60", "120", p39feas.CLOSE):
            hr = (row.get("horizons") or {}).get(h) or {}
            cells.append(_fmt(hr.get("move_over_cost")))
        cells += [str(best.get("horizon") or "—"), _fmt(best.get("move_over_cost"))]
        rows.append(cells)
    return [
        "## 5. Horizon sensitivity (not used for ranking)",
        "",
        "MOVE/COST at a range of holds. The best column is reported because a "
        "ratio that exists at only one hold is a fact worth seeing — but it is "
        "**excluded from the ranking and the labels**, because choosing each "
        "pair's own best hold from these results is a threshold picked with "
        "hindsight, which this study is not allowed to do.",
        "",
        *_table(
            ["instrument|vehicle", "5m", "15m", "30m", "60m", "120m", "close",
             "best hold", "best ratio"],
            rows,
        ),
    ]


def _stress_section(state: dict) -> list[str]:
    rows = []
    for key, row in sorted((state.get("pairs") or {}).items()):
        cells = [key]
        for s in row.get("stress") or []:
            cells.append(f"{_fmt(s.get('move_over_cost'))} ({s.get('label')})")
        rows.append(cells)
    return [
        "## 6. Cost stress",
        "",
        "The same ratio when the round trip costs 1.5x and 2x its measured "
        "value. A pair that only covers its costs at exactly measured cost is "
        "not a pair to plan on, because real fills slip.",
        "",
        *_table(["instrument|vehicle", "1.0x", "1.5x", "2.0x"], rows),
    ]


def _reconciliation_section(state: dict) -> list[str]:
    rows = []
    for key, row in sorted((state.get("pairs") or {}).items()):
        rec = row.get("reconciliation") or {}
        rows.append([
            key,
            str(rec.get("n")),
            _fmt((rec.get("mid_frame_net_pct") or {}).get("median")),
            _fmt((rec.get("realised_frame_net_pct") or {}).get("median")),
            _fmt((rec.get("residual_pct") or {}).get("median")),
            _fmt((rec.get("residual_abs_pct") or {}).get("p90")),
            str(rec.get("legs_without_executable_exit")),
        ])
    return [
        "## 7. Mid frame against the executable frame",
        "",
        "The mid frame measures movement mid-to-mid and charges the whole "
        "quoted width as cost. The executable frame is Phase 36's arithmetic — "
        "ask in, bid out — recomputed on these same legs. They differ by "
        "however much the quoted width changed over the hold, and that residual "
        "is the error bar on every mid-frame number above.",
        "",
        *_table(
            ["instrument|vehicle", "n", "mid net %", "executable net %",
             "residual median %", "|residual| p90 %", "no executable exit"],
            rows,
        ),
    ]


def _bullets(keys: list[str]) -> list[str]:
    return [f"- {k}" for k in keys] if keys else ["- none"]


def _triage_section(state: dict) -> list[str]:
    tri = state.get("triage") or {}
    return [
        "## 8. Triage — the question this phase was commissioned for",
        "",
        "**Keep accumulating paper evidence** — typical favourable movement "
        "covered the round trip at least once at the reference horizon:",
        "",
        *_bullets(tri.get("keep_accumulating") or []),
        "",
        "**Deprioritise** — even a hindsight-perfect exit did not cover the "
        "round trip for most instants. This is the decisive direction of the "
        "measurement: it rules economics out, it never rules an edge in:",
        "",
        *_bullets(tri.get("deprioritise") or []),
        "",
        "**Undecided** — too few executable instants to answer even the cost "
        "question, including every pair whose book was never executable at "
        "all. Nothing has been shown about these either way — this list is "
        "where an instrument goes when the capture, not the instrument, is "
        "what is thin:",
        "",
        # Run together rather than bulleted: on a full store this is most of
        # the universe, and a page of bullets would bury the two lists above.
        ", ".join(tri.get("undecided") or []) or "none",
    ]


def _limits_section(state: dict) -> list[str]:
    return [
        "## 9. What this cannot say",
        "",
        "- **It cannot say anything is profitable.** The numerator is a "
        "hindsight ceiling; no rule here picks entries, sides or exits.",
        "- **It cannot rank instruments by expected return.** A pair can top "
        "this table on offered movement and still have lost money on every "
        "session captured — Phase 36 found exactly that on CRUDEOIL, where all "
        "three vehicles were net-negative at this same horizon.",
        "- **It does not choose a threshold.** The required move is arithmetic "
        "from the cost model; the reference horizon was declared before the "
        "first run; reach multiples are reported as a distribution.",
        "- **It does not measure adverse movement.** A pair whose favourable "
        "excursion clears its costs may reach its stop first, which is a "
        "different study on the same store.",
        "- **Sessions are few.** Every figure is bounded by "
        f"{len(state.get('sessions') or [])} captured session(s); a spread "
        "measured on one day is that day's liquidity.",
        "- **A registry lot is not a verified lot.** Where the lot came from "
        "the contract registry rather than the book (see §2), a lot revision "
        "since that table was written would move the per-lot brokerage, and "
        "with it the required move on cheap contracts where a flat fee is most "
        "of the cost. The prices are measured; that one input may not be.",
        "- **Nothing here was enabled.** No gate, sizing rule, strike rule or "
        "order path changed, and no vehicle was turned on or off.",
    ]


def sections(state: dict) -> list[str]:
    return [
        *_cost_section(state), "",
        *_coverage_section(state), "",
        *_movement_section(state), "",
        *_ranking_section(state), "",
        *_horizon_section(state), "",
        *_stress_section(state), "",
        *_reconciliation_section(state), "",
        *_triage_section(state), "",
        *_limits_section(state),
    ]


def render(state: dict) -> str:
    tri = state.get("triage") or {}
    lines = [
        "# COST-TO-EDGE FEASIBILITY",
        "",
        *HEADER_LINES,
        "",
        f"SESSION_COUNT = {len(state.get('sessions') or [])}",
        f"PAIRS_MEASURED = {len(state.get('pairs') or {})}",
        f"REFERENCE_HORIZON = {REFERENCE_HORIZON} minutes (pre-declared)",
        f"PHASE39_VERSION = {VERSION}",
        "",
        NOT_AN_EDGE,
        "",
        *sections(state),
        "",
        "---",
        "",
        f"CAPABLE OF PAYING ITS OWN COSTS: {len(tri.get('keep_accumulating') or [])}",
        f"CANNOT PAY ITS OWN COSTS: {len(tri.get('deprioritise') or [])}",
        f"UNDECIDED, NEEDS MORE CAPTURE: {len(tri.get('undecided') or [])}",
        f"CURRENT ACTION: {state.get('action')}",
    ]
    return "\n".join(lines) + "\n"


def payload(state: dict) -> str:
    return json.dumps(state, indent=2, default=str, sort_keys=True)
