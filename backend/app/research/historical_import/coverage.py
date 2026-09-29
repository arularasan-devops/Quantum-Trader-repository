"""§11/§16 — the historical coverage audit, and the eight questions it answers.

One table, one row per dataset, and every column is a property of the file
rather than a claim about the market: instrument, source, timeframe, span,
sessions, bars, coverage inside its own sessions, gaps, OHLC quality, whether
contract identity is present, whether a bid and an ask are present, and what
research the dataset is therefore fit for.

The column that matters most is the last one, and it is deliberately narrow.
No row of this table says a strategy works, and none of them can: a candle
carries no executable price, so "research suitability" tops out at *candle
research with a chronological split* and the execution question stays
:data:`EXECUTION_UNMEASURED`.

The research-gap section is answered the same way. A gap is closed when the
data needed to *measure* it exists — both legs of NIFTY↔BANKNIFTY over the
same sessions, say — not when a result has been produced. The distinction is
the whole point: this module reports what became answerable, and stops.
"""
from __future__ import annotations

from app.research.historical_import import (
    BID_ASK_PRESENT,
    CONTRACT_PRESENT,
    ELIGIBLE_FULL,
    ELIGIBLE_NONE,
    EXECUTION_UNMEASURED,
    OPTION_PRESENT,
    QUALITY_CLEAN,
    VERSION,
)
from app.research.historical_import import adapter, registry
from app.research.phase24.data import MIN_SESSIONS

# The gaps this import layer was built to close, stated before any data arrived
# so a dataset cannot be declared to have closed a gap invented after the fact.
# Each names the legs it needs and the timeframe they must share.
DECLARED_GAPS: tuple[dict, ...] = (
    {
        "gap": "NIFTY_VS_BANKNIFTY_RELATIVE_VALUE",
        "needs": ("NIFTY", "BANKNIFTY"),
        "why": "the requested pair; neither CRUDEOIL nor any other pair substitutes",
    },
    {
        "gap": "SENSEX_INDEX_BEHAVIOUR",
        "needs": ("SENSEX",),
        "why": "no five-year series existed for it at all",
    },
    {
        "gap": "GOLD_SILVER_MCX_REGIME_BEHAVIOUR",
        "needs": ("GOLD", "SILVER"),
        "why": "MCX beyond CRUDEOIL had captured weeks, not years",
    },
    {
        "gap": "NIFTY_FAMILY_CROSS_INSTRUMENT_SELECTION",
        "needs": ("NIFTY", "BANKNIFTY", "FINNIFTY"),
        "why": "cross-instrument opportunity selection needs the family together",
    },
)

MIN_OVERLAP_SESSIONS = 250  # a relative-value pair measured over less is descriptive


def rows() -> list[dict]:
    """The audit table: one row per registered dataset, rejections included."""
    out: list[dict] = []
    for rec in registry.datasets():
        detail = rec.get("detail") or {}
        out.append({
            "dataset_id": rec["dataset_id"],
            "instrument": rec.get("instrument"),
            "source": rec.get("source"),
            "timeframe": rec.get("timeframe"),
            "start": rec.get("start"),
            "end": rec.get("end"),
            "sessions": rec.get("sessions"),
            "bars": rec.get("rows"),
            "gap_pct_within_sessions": rec.get("coverage"),
            "ohlc_quality": rec.get("data_quality_status"),
            "contract_detail": rec.get("contract_detail_status"),
            "option_detail": rec.get("option_detail_status"),
            "bid_ask": rec.get("bid_ask_status"),
            "execution": adapter.execution_status(rec),
            "research_suitability": rec.get("research_eligibility"),
            "five_year": rec.get("research_eligibility") == ELIGIBLE_FULL,
            "timezone": rec.get("timezone"),
            "file_hash": (rec.get("file_hash") or "")[:12],
            "fingerprint": (detail.get("dataset_fingerprint") or "")[:12],
            "status": "IMPORTED",
        })
    for rec in registry.rejections():
        detail = rec.get("detail") or {}
        out.append({
            "dataset_id": None,
            "instrument": rec.get("instrument"),
            "source": rec.get("source"),
            "status": "IMPORT_REJECTED",
            "reject_reason": detail.get("reject_reason"),
            "rejected_file": detail.get("rejected_file"),
            "attempted_at": rec.get("import_timestamp"),
        })
    return out


def gaps_closed() -> list[dict]:
    """Which pre-declared research gap each import made *answerable*."""
    available = {
        inst: src for inst, src in adapter.available().items()
        if src.get("origin")
    }

    def sessions_of(inst: str) -> int:
        src = available.get(inst.upper())
        if not src:
            return 0
        if src["origin"] == "REPOSITORY_FIVE_YEAR_SERIES":
            # The shipped series are the ones every earlier study ran on; their
            # length is not re-measured here, only their presence.
            return MIN_SESSIONS
        return int(src.get("sessions") or 0)

    out: list[dict] = []
    for spec in DECLARED_GAPS:
        have = {leg: sessions_of(leg) for leg in spec["needs"]}
        missing = [leg for leg, n in have.items() if n == 0]
        thin = [leg for leg, n in have.items()
                if 0 < n < MIN_OVERLAP_SESSIONS]
        if missing:
            state = "STILL_OPEN_MISSING_" + "_".join(sorted(missing))
        elif thin:
            state = "PARTIALLY_ANSWERABLE_DESCRIPTIVE_ONLY_" + "_".join(sorted(thin))
        else:
            state = "ANSWERABLE_THE_DATA_TO_MEASURE_IT_NOW_EXISTS"
        out.append({
            "gap": spec["gap"],
            "needs": list(spec["needs"]),
            "sessions_per_leg": have,
            "state": state,
            "why_it_matters": spec["why"],
            "execution": EXECUTION_UNMEASURED,
            "note": (
                "answerable means the measurement can be run, not that a result "
                "exists or that any candidate survived"
            ),
        })
    return out


def report() -> dict:
    """The coverage report: the table, the eight answers, and what is still absent."""
    table = rows()
    imported = [r for r in table if r["status"] == "IMPORTED"]
    return {
        "importer_version": VERSION,
        "datasets": table,
        "questions": {
            "1_what_historical_instruments_do_we_have": sorted(
                {r["instrument"] for r in imported}
            ) or ["NONE_IMPORTED_YET"],
            "2_which_have_five_year_coverage": sorted(
                {r["instrument"] for r in imported if r["five_year"]}
            ) or ["NONE"],
            "3_which_have_only_weeks_or_months": sorted(
                {r["instrument"] for r in imported
                 if not r["five_year"]
                 and r["research_suitability"] != ELIGIBLE_NONE}
            ) or ["NONE"],
            "4_which_are_suitable_for_candle_research": sorted(
                {r["dataset_id"] for r in imported
                 if r["research_suitability"] != ELIGIBLE_NONE}
            ) or ["NONE"],
            "5_which_contain_contract_identity": sorted(
                {r["dataset_id"] for r in imported
                 if r["contract_detail"] == CONTRACT_PRESENT}
            ) or ["NONE"],
            "6_which_contain_option_strike_and_expiry": sorted(
                {r["dataset_id"] for r in imported
                 if r["option_detail"] == OPTION_PRESENT}
            ) or ["NONE"],
            "7_which_contain_bid_ask": sorted(
                {r["dataset_id"] for r in imported
                 if r["bid_ask"] == BID_ASK_PRESENT}
            ) or ["NONE_EVERY_IMPORT_IS_OHLC_ONLY_SO_EXECUTION_IS_UNMEASURED"],
            "8_which_research_gaps_are_now_closed": gaps_closed(),
        },
        "totals": {
            "datasets": len(imported),
            "rejected_imports": len(table) - len(imported),
            "instruments": len({r["instrument"] for r in imported}),
            "bars": sum(int(r.get("bars") or 0) for r in imported),
            "sessions": sum(int(r.get("sessions") or 0) for r in imported),
            "clean_datasets": sum(1 for r in imported
                                  if r["ohlc_quality"] == QUALITY_CLEAN),
        },
        "series_available_to_research": adapter.available(),
        "standing_limits": [
            "an imported candle carries no bid, ask or size, so every execution "
            "economics answer on it is EXECUTION_UNMEASURED",
            "a dataset is fit for candle research at best; the promotion path to "
            "anything live is unchanged and starts at HISTORICAL_LEAD",
            "coverage is measured inside the sessions a dataset contains; an "
            "absent weekday is NOT_GRADED because a candle file has no calendar",
        ],
    }
