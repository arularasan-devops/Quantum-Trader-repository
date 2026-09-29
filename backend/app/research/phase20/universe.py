"""Phase 20 §1 — the research universe, and what each name may be claimed to show.

A+ was becoming an index-only system by accident: three index roots had a prior,
so only they could rank, and every other optionable name in the registry was
invisible to research. This module widens the universe to the whole optionable
registry — indexes, MCX commodities and the liquid F&O equities — and, for each
name, states what evidence is *obtainable* so a report cannot claim more than the
data can carry.

Two separations do the work here:

* **market evidence vs vehicle evidence.** Five years of underlying history can
  say whether a market's setups had direction. It can say nothing about option
  economics, because expired option chains are not fetchable from this broker at
  any price — so historical option profitability is unclaimable for every name in
  the universe, not merely unproven. Option economics come only from live
  exact-timestamp capture and the costed paper book.
* **per instrument, never pooled.** Ranking is one record per instrument. A
  pooled statistic across a family is refused outright rather than reported with
  a caveat: one number over NIFTY and KALYANKJIL is what hid a -Rs 3.9L single
  stock loss inside a flat aggregate on 25 Aug.

Research only. Production Option/Futures/CAS selection does not read this module,
and admission here is not eligibility to trade anything.
"""
from __future__ import annotations

from app.analysis import instrument_family as fam
from app.market.instruments import REGISTRY
from app.research.phase17 import aplus as p17aplus

# Where an underlying history series for this name comes from, if anywhere. The
# rule mirrors what the Phase 14 collector can actually resolve — index names are
# a cash index row, equities their NSE ``-EQ`` row, and an MCX commodity has no
# cash series at all, since a same-named NSE row is a listed company and not the
# metal.
CASH_INDEX = "CASH_INDEX"
CASH_EQUITY = "CASH_EQUITY"
FUTURES_CONTRACT_ONLY = "FUTURES_CONTRACT_ONLY"
NO_HISTORY = "NO_HISTORY"

# The two questions research asks, kept apart because one is answerable from
# history and the other is not.
CLAIM_HISTORICAL_DIRECTION = "HISTORICAL_DIRECTION"
CLAIM_OPTION_ECONOMICS = "OPTION_ECONOMICS"
CLAIMS: tuple[str, ...] = (CLAIM_HISTORICAL_DIRECTION, CLAIM_OPTION_ECONOMICS)

# Where an option-economics claim may come from. There is exactly one source: the
# live both-side capture and the costed paper book built on it. This is a constant
# and not a threshold — no amount of underlying history converts into it.
OPTION_EVIDENCE_SOURCE = "LIVE_EXACT_TIMESTAMP_CAPTURE_AND_COSTED_PAPER"
HISTORICAL_OPTION_CHAINS_AVAILABLE = False

# A commodity's history reaches back only to the listing of the futures contract
# being read, so a five-year direction study is not available for MCX on the same
# terms as the indexes. Saying so per instrument is the point: the alternative is
# a five-year table with a silently three-month row in it.
FIVE_YEAR_HISTORY_BASES: frozenset[str] = frozenset({CASH_INDEX, CASH_EQUITY})

# The named study slices. Indexes and MCX are the five each that Phase 12 §9/§10
# already fixed; equities are every remaining optionable registry name, so adding
# a name to the registry adds it to research instead of quietly leaving it out.
INDEX_STUDY: tuple[str, ...] = fam.INDEX_STUDY
MCX_STUDY: tuple[str, ...] = fam.MCX_STUDY


def history_basis(instrument: str) -> str:
    """Which underlying series, if any, exists for this instrument."""
    key = (instrument or "").upper()
    if key not in REGISTRY:
        return NO_HISTORY
    family = fam.family(key)
    if family == fam.INDEX:
        return CASH_INDEX
    if family == fam.STOCK:
        return CASH_EQUITY
    if family == fam.MCX:
        return FUTURES_CONTRACT_ONLY
    return NO_HISTORY


def equity_study() -> tuple[str, ...]:
    """Every optionable equity in the registry, in name order."""
    return tuple(n for n in fam.members(fam.STOCK) if n not in INDEX_STUDY)


def may_claim(instrument: str, claim: str) -> tuple[bool, str]:
    """May ``claim`` be made about ``instrument`` at all, and on what basis.

    This is about the *availability of evidence*, never about a result: a True
    here means the question can be asked, not that the answer was favourable.
    """
    basis = history_basis(instrument)
    if claim == CLAIM_OPTION_ECONOMICS:
        # False for every name, including the indexes with five years of history.
        # An option's fill is its bid/ask at an instant, and no historical chain
        # exists to read one from.
        return False, ("HISTORICAL_OPTION_CHAINS_UNAVAILABLE: option economics "
                       f"come only from {OPTION_EVIDENCE_SOURCE}")
    if claim == CLAIM_HISTORICAL_DIRECTION:
        if basis in FIVE_YEAR_HISTORY_BASES:
            return True, f"UNDERLYING_ONLY_FROM_{basis}"
        if basis == FUTURES_CONTRACT_ONLY:
            return False, ("NO_CASH_SERIES: history reaches back only to the "
                           "listing of the futures contract, so a five-year "
                           "study is not comparable to the index study")
        return False, "NOT_A_REGISTERED_INSTRUMENT"
    return False, f"UNKNOWN_CLAIM_{claim}"


def plan(instrument: str) -> dict:
    """What this instrument is, and what research may ask of it."""
    key = (instrument or "").upper()
    refused, label = p17aplus.prior_vetoes(key)
    direction_ok, direction_basis = may_claim(key, CLAIM_HISTORICAL_DIRECTION)
    _, option_basis = may_claim(key, CLAIM_OPTION_ECONOMICS)
    return {
        "instrument": key,
        "family": fam.family(key),
        "registered": key in REGISTRY,
        "history_basis": history_basis(key),
        "five_year_direction_study": direction_ok,
        "direction_basis": direction_basis,
        "may_claim_historical_option_profit": False,
        "option_evidence_source": OPTION_EVIDENCE_SOURCE,
        "option_basis": option_basis,
        "prior_label": label,
        # An instrument with no prior is INSUFFICIENT_DATA and admitted: never
        # having been measured is not a finding against it, and refusing it on
        # that would keep the universe permanently at the three names that
        # happen to have a five-year number.
        "research_admitted": not refused,
        "refused_because": label if refused else None,
    }


def universe() -> list[dict]:
    """A plan per optionable registry name, indexes then MCX then equities."""
    order = [*INDEX_STUDY, *MCX_STUDY, *equity_study()]
    seen: set[str] = set()
    out: list[dict] = []
    for name in [*order, *sorted(REGISTRY)]:
        key = name.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(plan(key))
    return out


def capture_universe() -> tuple[str, ...]:
    """Instruments whose live both-side book research wants recorded.

    Every research-admitted optionable name, indexes and MCX first. Which of them
    actually produce rows is decided upstream by what the app is streaming, so
    this widens what MAY be captured without widening what is subscribed. An
    instrument refused on a validated negative prior is left out: recording a
    book we will not study is storage spent for nothing.
    """
    return tuple(p["instrument"] for p in universe() if p["research_admitted"])


def study_groups() -> dict[str, list[str]]:
    """The research universe split into families that are never pooled."""
    return {
        fam.INDEX: [p["instrument"] for p in universe()
                    if p["family"] == fam.INDEX],
        fam.MCX: [p["instrument"] for p in universe() if p["family"] == fam.MCX],
        fam.STOCK: [p["instrument"] for p in universe()
                    if p["family"] == fam.STOCK],
    }
