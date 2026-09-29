"""The A+ research score — §20, §21, §32.

A+ is NOT the Signal Score. The Signal Score was measured on 61k candidate bars
and does not select: BUY cohorts returned -0.011R and the refusals -0.012R, a lift
of +0.001R at 0.08 sigma. Reusing it here would rank rows by a number already
known to be uninformative, and would then attribute the vehicle layer's findings
to it.

So the score is built from six components that are each measurable at signal
time, each shown individually, and each of which can independently disqualify a
candidate:

``market_edge``     the historical prior for this instrument — UNDERLYING_ONLY.
``vehicle_edge``    can the option pay for its own round trip (§9).
``tradability``     is there a real, quotable, two-sided book.
``entry_edge``      is this a price or a chase (§11), UNKNOWN unless measured.
``room``            does the plan's own expected move cover T1 on the option (§8).
``data_quality``    how close the book was to the decision (§3).

Two properties that matter more than the weights. First, ``data_quality`` is a
GATE, not just a term: a candidate scored off a stale book is REJECT_DATA
whatever the other five say, because the alternative is a board that looks
confident about a book nobody could have traded. Second, a missing component
lowers the score rather than being dropped from the average — otherwise the
thinnest, least measurable rows would score highest, which is how an unmeasurable
candidate reaches the top of a list.

The weights are stated, not fitted. Nothing in this module was tuned against
outcomes, and the reports print the weight table beside every ranking so a reader
can re-rank under their own.
"""
from __future__ import annotations

from app.research.phase17 import economics, entry as entry_mod, quality, reach, schema

A_PLUS = "A_PLUS"
WATCH = "WATCH"
REJECT_DATA = "REJECT_DATA"
REJECT_SPREAD = "REJECT_SPREAD"
REJECT_VEHICLE = "REJECT_VEHICLE"
REJECT_ENTRY = "REJECT_ENTRY"
REJECT_ROOM = "REJECT_ROOM"
REJECT_MARKET = "REJECT_MARKET"
LABELS: tuple[str, ...] = (
    A_PLUS, WATCH, REJECT_DATA, REJECT_SPREAD, REJECT_VEHICLE, REJECT_ENTRY,
    REJECT_ROOM, REJECT_MARKET,
)

# §32 lifecycle. Where A+ currently stands, and it starts at the bottom.
NOT_READY = "NOT_READY"
RESEARCH = "RESEARCH"
PAPER = "PAPER"
VALIDATED = "VALIDATED"
PRODUCTION_CANDIDATE = "PRODUCTION_CANDIDATE"
STATUSES: tuple[str, ...] = (
    NOT_READY, RESEARCH, PAPER, VALIDATED, PRODUCTION_CANDIDATE,
)

WEIGHTS: dict[str, float] = {
    "market_edge": 0.20,
    "vehicle_edge": 0.25,
    "tradability": 0.15,
    "entry_edge": 0.15,
    "room": 0.15,
    "data_quality": 0.10,
}

# §18 historical prior, UNDERLYING_ONLY, from the real Phase 15B run: development
# expectancy per instrument in R. NONE of these cleared the multiple-testing bar
# (3.68 sigma over 218 comparisons), so they are used only to ORDER instruments,
# never as evidence that any of them is profitable. Stocks are negative for a
# physical reason — 0.93 points of median travel against a ~2.4-point round trip.
PRIOR_DEV_R: dict[str, float] = {
    "MIDCPNIFTY": 0.0249,
    "NIFTY": 0.0064,
    "SENSEX": 0.0000,
    "FINNIFTY": -0.0010,
    "BANKNIFTY": -0.0050,
    "INFY": -0.0180,
    "TCS": -0.0230,
    "RELIANCE": -0.0290,
    "SBIN": -0.0300,
    "HDFCBANK": -0.0340,
    "ICICIBANK": -0.0420,
}
PRIOR_BASIS = "UNDERLYING_ONLY_NOT_SIGNIFICANT"

# How much the pool can say about one prior. "NOT_VALIDATED" was one bucket and
# hid the distinction that matters: a mean the pool cannot separate from zero and
# a mean that is clearly negative but misses a deliberately strict bar are
# different findings, and only the first makes a refusal unsupported.
VALIDATED_NEGATIVE = "VALIDATED_NEGATIVE"
NEGATIVE_BUT_UNDER_BAR = "NEGATIVE_BUT_UNDER_BAR"
INDISTINGUISHABLE_FROM_ZERO = "INDISTINGUISHABLE_FROM_ZERO"
POSITIVE_BUT_UNDER_BAR = "POSITIVE_BUT_UNDER_BAR"
VALIDATED_POSITIVE = "VALIDATED_POSITIVE"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
PRIOR_LABELS: tuple[str, ...] = (
    VALIDATED_NEGATIVE, NEGATIVE_BUT_UNDER_BAR, INDISTINGUISHABLE_FROM_ZERO,
    POSITIVE_BUT_UNDER_BAR, VALIDATED_POSITIVE, INSUFFICIENT_DATA,
)

# What the real five-year multi-instrument pool said about each prior:
# 201,144 underlying outcomes, ~18k per instrument, tested pooled AND on a
# chronological holdout AND fold by fold. UNDERLYING_ONLY — it says nothing about
# option economics, which is why an admitted instrument still has to clear
# vehicle, spread, entry and room like everything else.
PRIOR_LABEL: dict[str, str] = {
    "MIDCPNIFTY": POSITIVE_BUT_UNDER_BAR,       # +0.0280 at 3.05 sigma
    "NIFTY": INDISTINGUISHABLE_FROM_ZERO,       # +0.0062 at 0.72 sigma
    "SENSEX": INDISTINGUISHABLE_FROM_ZERO,      # +0.0001 at 0.01 sigma
    "FINNIFTY": INDISTINGUISHABLE_FROM_ZERO,    # -0.0012 at 0.14 sigma
    "BANKNIFTY": INDISTINGUISHABLE_FROM_ZERO,   # -0.0051 at 0.59, holdout +0.0225
    "INFY": NEGATIVE_BUT_UNDER_BAR,             # -0.0178 at 2.08, holdout -0.0270
    "TCS": NEGATIVE_BUT_UNDER_BAR,              # -0.0227 at 2.68, holdout -0.0341
    "RELIANCE": NEGATIVE_BUT_UNDER_BAR,         # -0.0288 at 3.35, holdout -0.0523
    "SBIN": NEGATIVE_BUT_UNDER_BAR,             # -0.0300 at 3.50, holdout -0.0460
    "HDFCBANK": NEGATIVE_BUT_UNDER_BAR,         # cleared 3.95, folds flip 4/5
    "ICICIBANK": VALIDATED_NEGATIVE,            # -0.0422 at 4.97, folds 5/5
}
PRIOR_LABEL_BASIS = "MARKET_SCAN_POOL_5Y_UNDERLYING_ONLY_201144_ROWS"

# The only label that may refuse an instrument outright: a negative the pool
# validated pooled AND on the holdout AND fold by fold. Every other prior is a
# RANKING feature — it still moves ``market_edge``, so a bad prior demands far
# more of the room and vehicle terms to reach the floor, but it no longer decides
# eligibility on the sign of a number that did not survive the bar.
#
# The bar is applied in one direction only by design. NEGATIVE_BUT_UNDER_BAR is
# *not* a clean bill of health — INFY/TCS/RELIANCE/SBIN/HDFCBANK are negative in
# the holdout too (PF 0.91-0.95) — so they enter research ranked at the bottom
# rather than admitted as neutral, and a research candidate is not a production
# candidate in any case.
VETO_LABELS: frozenset[str] = frozenset({VALIDATED_NEGATIVE})

# Score floors. Seeds chosen before any outcome was read.
A_PLUS_MIN_SCORE = 70.0
WATCH_MIN_SCORE = 55.0


def _market_edge(instrument: str) -> tuple[float | None, str]:
    prior = PRIOR_DEV_R.get((instrument or "").upper())
    if prior is None:
        return None, "NO_PRIOR"
    # Map -0.05R..+0.05R onto 0..100. Linear and stated; the prior is an ordering
    # device and a finer mapping would imply precision the sample cannot carry.
    return max(0.0, min(100.0, (float(prior) + 0.05) * 1000.0)), "PRIOR_APPLIED"


def prior_label(instrument: str) -> str:
    """What the pool could say about this instrument's prior.

    An instrument the pool never measured is INSUFFICIENT_DATA, never a pass:
    "we did not look" is not a finding in either direction.
    """
    return PRIOR_LABEL.get((instrument or "").upper(), INSUFFICIENT_DATA)


def prior_vetoes(instrument: str) -> tuple[bool, str]:
    """May this instrument's prior refuse it outright, and under which label.

    Research eligibility only — production Option/Futures/CAS signals do not read
    this. A prior refuses only where the five-year pool showed the negative
    holding out of sample; elsewhere it is ordering information.
    """
    label = prior_label(instrument)
    return label in VETO_LABELS, label


def market_edge(instrument: str) -> tuple[float | None, str]:
    """The instrument's fixed score contribution, exposed for auditing.

    A caller outside the grader needs this to state an instrument's permanent
    ceiling without re-deriving the mapping and risking a second answer.
    """
    return _market_edge(instrument)


def _vehicle_edge(econ: dict | None) -> tuple[float | None, str]:
    if not econ:
        return None, "NO_ECONOMICS"
    cls = econ.get("vehicle_class")
    if cls == economics.GREEN:
        return 100.0, cls
    if cls == economics.YELLOW:
        return 60.0, cls
    if cls == economics.RED:
        return 0.0, cls
    return None, economics.UNKNOWN


def _tradability(q: schema.Quote | None, econ: dict | None) -> tuple[float | None, str]:
    if q is None or not q.has_book:
        return None, "NO_BOOK"
    spct = econ.get("spread_pct") if econ else q.spread_pct
    if not isinstance(spct, (int, float)):
        return None, "NO_SPREAD"
    # 0.5% of premium is a clean index book; 5% is untradable.
    s = float(spct)
    return max(0.0, min(100.0, 100.0 - (s - 0.5) * (100.0 / 4.5))), f"SPREAD_{s:.2f}PCT"


def _entry_edge(ent: dict | None) -> tuple[float | None, str]:
    if not ent:
        return None, "NO_ENTRY_DATA"
    cls = ent.get("entry_quality")
    table = {
        entry_mod.IDEAL: 100.0,
        entry_mod.GOOD: 80.0,
        entry_mod.ACCEPTABLE: 60.0,
        entry_mod.CHASED: 25.0,
        entry_mod.SEVERELY_CHASED: 0.0,
    }
    if cls in table:
        return table[cls], cls
    return None, entry_mod.UNKNOWN


def _room(rch: dict | None) -> tuple[float | None, str]:
    if not rch or not isinstance(rch.get("t1_score"), (int, float)):
        return None, "NO_T1_SCORE"
    return float(rch["t1_score"]), str(rch.get("t1_rank"))


def _data_quality(q: schema.Quote | None, obs_quality: str) -> tuple[float, str]:
    table = {
        quality.EXACT: 100.0,
        quality.GOOD: 80.0,
        quality.DEGRADED: 40.0,
        quality.STALE: 0.0,
        quality.MISSING: 0.0,
    }
    return table.get(obs_quality, 0.0), obs_quality


def score(
    obs: schema.Observation,
    *,
    market_edge_override: tuple[float | None, str] | None = None,
) -> dict:
    """A+ research score and label for one observation.

    ``obs.economics``/``entry``/``reach`` must already be attached; this module
    does not recompute them, so a board row and its evidence row can never
    disagree.

    ``market_edge_override`` supplies the component for instruments the five-year
    cash pool cannot speak for. It exists for MCX (§4): there is no cash series
    behind CRUDEOIL, so :data:`PRIOR_DEV_R` has no entry and every commodity row
    on 1 Sep scored ``market_edge: None`` — unmeasured at full weight, which is
    honest but permanently uninformative. The override carries its own basis word
    in the note, and it is NOT the cash prior: the two are not interchangeable
    and every consumer is told which one it is holding via ``market_edge_basis``.
    """
    q = obs.selected
    econ = obs.economics
    ent = obs.entry
    rch = obs.reach

    comps: dict[str, dict] = {}
    values: dict[str, float | None] = {}

    edge = market_edge_override or _market_edge(obs.instrument)
    for name, (val, note) in (
        ("market_edge", edge),
        ("vehicle_edge", _vehicle_edge(econ)),
        ("tradability", _tradability(q, econ)),
        ("entry_edge", _entry_edge(ent)),
        ("room", _room(rch)),
        ("data_quality", _data_quality(q, obs.data_quality)),
    ):
        values[name] = None if val is None else round(float(val), 2)
        comps[name] = {
            "value": values[name],
            "weight": WEIGHTS[name],
            "note": note,
            "measured": val is not None,
        }

    # A missing component contributes zero at full weight. See the module
    # docstring: dropping it would reward unmeasurable rows.
    total = round(
        sum(WEIGHTS[k] * (values[k] or 0.0) for k in WEIGHTS), 2
    )
    missing = [k for k, v in values.items() if v is None]

    label, reasons = _label(obs, econ, ent, rch, total, missing)
    t1_basis = str((rch or {}).get("t1_basis") or reach.BASIS_NONE)
    return {
        "a_plus_score": total,
        "a_plus_label": label,
        "components": comps,
        "missing_components": missing,
        "reasons": reasons,
        "weights": dict(WEIGHTS),
        "market_edge_basis": (
            str(edge[1]) if market_edge_override else PRIOR_BASIS
        ),
        "market_edge_is_cash_prior": market_edge_override is None,
        "prior_basis": PRIOR_BASIS,
        "prior_label": prior_label(obs.instrument),
        "prior_label_basis": PRIOR_LABEL_BASIS,
        "prior_is_ranking_only": not prior_vetoes(obs.instrument)[0],
        "t1_basis": t1_basis,
        # A row measured against a DERIVED target is a research grade only. It can
        # still be A+ on the board, so the candidate pool can be graded at all,
        # but it never enters the costed book and never counts toward promotion —
        # the target it was scored against was not one the engine committed to.
        # Deliberately independent of the label: this says whether the EVIDENCE is
        # of promotable quality, and the caller checks the label separately.
        "promotable": t1_basis == reach.BASIS_ENGINE,
        "research_only": True,
        "score_floor_a_plus": A_PLUS_MIN_SCORE,
        "score_floor_watch": WATCH_MIN_SCORE,
    }


def _label(obs: schema.Observation, econ: dict | None, ent: dict | None,
           rch: dict | None, total: float, missing: list[str]
           ) -> tuple[str, list[str]]:
    """Gates first, score second. Order matters: the first failing gate is the
    label, so a reader sees WHY a candidate is off the board rather than a number
    just below a threshold."""
    reasons: list[str] = []
    if not quality.usable(obs.data_quality):
        return REJECT_DATA, [f"DATA_QUALITY_{obs.data_quality}"]
    if not obs.both_sides:
        reasons.append("NO_OPPOSITE_BOOK")
    if econ:
        if econ.get("vehicle_class") == economics.RED:
            return REJECT_VEHICLE, ["VEHICLE_RED", *econ.get("reasons", [])]
        if econ.get("cost_status") != schema.COST_MEASURED:
            return REJECT_DATA, ["COST_UNKNOWN", *econ.get("reasons", [])]
        spct = econ.get("spread_pct")
        if isinstance(spct, (int, float)) and float(spct) > economics.RED_SPREAD_PCT:
            return REJECT_SPREAD, [f"SPREAD_{float(spct):.2f}PCT"]
    else:
        return REJECT_DATA, ["NO_ECONOMICS"]
    if ent and ent.get("entry_quality") in (
        entry_mod.CHASED, entry_mod.SEVERELY_CHASED
    ):
        return REJECT_ENTRY, [str(ent.get("entry_quality"))]
    if rch and rch.get("t1_rank") in (reach.RANK_D, reach.RANK_E):
        return REJECT_ROOM, [str(rch.get("t1_rank"))]
    vetoed, label = prior_vetoes(obs.instrument)
    if vetoed:
        # A refusal only where the five-year pool showed the negative surviving a
        # chronological holdout. A prior the pool cannot separate from zero orders
        # the candidate through ``market_edge`` instead of refusing it.
        prior = PRIOR_DEV_R.get((obs.instrument or "").upper())
        reason = f"PRIOR_{label}"
        if isinstance(prior, (int, float)):
            reason = f"{reason}_{float(prior):+.4f}"
        return REJECT_MARKET, [reason]
    if missing:
        reasons.append("MISSING_" + ",".join(sorted(missing)).upper())
    if total >= A_PLUS_MIN_SCORE and not missing:
        return A_PLUS, reasons
    if total >= WATCH_MIN_SCORE:
        return WATCH, reasons or ["BELOW_A_PLUS_FLOOR"]
    # Below the watch floor it is still a recorded candidate — every eligible row
    # is kept as evidence (§5) — but it does not appear on either list.
    return REJECT_ROOM if rch else REJECT_DATA, reasons or ["BELOW_WATCH_FLOOR"]


def board(observations: list[schema.Observation], *, preferred: int = 5,
          watch: int = 5) -> dict:
    """§21. At most ``preferred`` A+ rows and ``watch`` watch rows.

    An empty board is a valid and expected result. Nothing is promoted to fill a
    slot: the ranking is over rows that already passed their own gates, so a day
    with no qualifying vehicle produces no rows, which is the answer.
    """
    scored: list[tuple[float, schema.Observation, dict]] = []
    for obs in observations:
        sc = obs.aplus if isinstance(obs.aplus, dict) else score(obs)
        scored.append((float(sc.get("a_plus_score") or 0.0), obs, sc))
    scored.sort(key=lambda t: -t[0])

    def row(obs: schema.Observation, sc: dict) -> dict:
        q = obs.selected
        rch = obs.reach or {}
        econ = obs.economics or {}
        ent = obs.entry or {}
        return {
            "observation_id": obs.observation_id,
            "instrument": obs.instrument,
            "direction": obs.direction,
            "vehicle": obs.selected_vehicle,
            "symbol": q.symbol if q else None,
            "strike": q.strike if q else None,
            "expiry": q.expiry if q else None,
            "days_to_expiry": q.days_to_expiry if q else None,
            "premium": q.premium if q else None,
            "bid": q.bid if q else None,
            "ask": q.ask if q else None,
            "spread": q.spread if q else None,
            "spread_pct": q.spread_pct if q else None,
            "entry": obs.plan.entry,
            "stop": obs.plan.stop,
            "target1": obs.plan.target1,
            "target2": obs.plan.target2,
            "target3": obs.plan.target3,
            "a_plus_score": sc.get("a_plus_score"),
            "a_plus_label": sc.get("a_plus_label"),
            "components": sc.get("components"),
            "t1_score": rch.get("t1_score"),
            "t1_rank": rch.get("t1_rank"),
            "t1_basis": sc.get("t1_basis"),
            "promotable": sc.get("promotable"),
            "not_a_probability": True,
            "vehicle_class": econ.get("vehicle_class"),
            "cost_points": econ.get("cost_points"),
            "cost_over_expected_move": econ.get("cost_over_expected_move"),
            "entry_quality": ent.get("entry_quality"),
            "expected_hold_minutes": obs.plan.expected_hold_minutes,
            "data_quality": obs.data_quality,
            "reasons": sc.get("reasons"),
            "paper_only": True,
            "no_real_order": True,
        }

    a_rows = [row(o, s) for v, o, s in scored if s.get("a_plus_label") == A_PLUS]
    w_rows = [
        row(o, s) for v, o, s in scored
        if s.get("a_plus_label") == WATCH
    ]
    return {
        "preferred": a_rows[: max(0, preferred)],
        "watch": w_rows[: max(0, watch)],
        "candidates_considered": len(observations),
        "a_plus_found": len(a_rows),
        "watch_found": len(w_rows),
        "empty_is_valid": True,
        "paper_only": True,
        "no_real_order": True,
        "note": (
            "Research ranking. A+ does not gate, alter or delay the production "
            "Signal, and no row here places an order."
        ),
    }
