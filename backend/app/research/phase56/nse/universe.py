"""§2/§3 — the date-correct universe, reconstructed from dated exchange files.

A session's bhavcopy lists exactly the securities that traded that session. That
single property is what makes survivorship control real here rather than
declared: a name that merged in 2023 is present in every file up to its last
session and absent from every file after it, so its first and last trading dates
are *read* from the archive instead of inferred from a current listing. A study
ranking stocks as of 2022-06-30 therefore ranks the names that existed on
2022-06-30, including the ones that have since disappeared.

What the archive supports, and what it does not, stated plainly:

* ``first_session`` / ``last_session`` — supported, exactly.
* trading-vs-suspended on a given date — supported (present in that file or not).
* restricted series (``BE``/``BZ`` trade-to-trade) — supported per date.
* symbol change — supported via ISIN continuity: the same ISIN reappearing under
  a new ticker as the old one stops is a rename, not a death.
* *reason* a name stopped trading — only partially. A merger, demerger or scheme
  is visible when the action table carries one near the last session; otherwise
  the honest status is ``STOPPED_TRADING_REASON_UNKNOWN``, which is what is
  emitted. Guessing "delisted" would be inventing a fact about a company.
* historical index membership and sector — **not published in these archives**
  and therefore not claimed anywhere. Eligibility is the declared liquidity screen
  from §1, computed from strictly prior sessions.

The eligibility function takes the sessions *before* the decision date and
nothing else. Passing it a window that includes the decision date is a
programming error, not a configuration choice, so it raises.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from statistics import median

from . import (
    ACTION_DEMERGER,
    ACTION_MERGER,
    ACTION_SCHEME_SUSPENSION,
    ACTION_SYMBOL_CHANGE,
    ELIGIBLE_SERIES,
    UNIVERSE_LABEL,
    UNIVERSE_LOOKBACK_SESSIONS,
    UNIVERSE_MIN_MEDIAN_TURNOVER_INR,
    UNIVERSE_MIN_PRICE_INR,
    UNIVERSE_MIN_PRIOR_SESSIONS,
    UNIVERSE_TOP_N_BY_TURNOVER,
    CorporateAction,
)
from .bhav import RawRow

NOT_YET_IN_ARCHIVE = "NOT_YET_IN_ARCHIVE"
TRADING = "TRADING"
TRADING_RESTRICTED_SERIES = "TRADING_RESTRICTED_SERIES"
NOT_TRADED_THIS_SESSION = "NOT_TRADED_THIS_SESSION"
STOPPED_MERGER_OR_SCHEME = "STOPPED_MERGER_OR_SCHEME"
STOPPED_SYMBOL_CHANGE = "STOPPED_SYMBOL_CHANGE"
STOPPED_TRADING_REASON_UNKNOWN = "STOPPED_TRADING_REASON_UNKNOWN"

#: Window after a symbol's last session in which a merger/demerger/scheme action
#: is accepted as the explanation for it stopping.
STOP_REASON_WINDOW_DAYS = 45


@dataclass(frozen=True)
class SymbolLife:
    symbol: str
    first_session: str
    last_session: str
    sessions: int
    isins: tuple[str, ...]
    series_seen: tuple[str, ...]

    def as_dict(self) -> dict:
        out = asdict(self)
        out["isins"] = list(self.isins)
        out["series_seen"] = list(self.series_seen)
        return out


def build_lives(sessions: dict[str, list[RawRow]]) -> dict[str, SymbolLife]:
    """``session date -> rows`` in, one life record per symbol out."""
    first: dict[str, str] = {}
    last: dict[str, str] = {}
    count: dict[str, int] = {}
    isins: dict[str, set[str]] = {}
    series: dict[str, set[str]] = {}
    for day in sorted(sessions):
        for row in sessions[day]:
            symbol = row.symbol
            first.setdefault(symbol, day)
            last[symbol] = day
            count[symbol] = count.get(symbol, 0) + 1
            if row.isin:
                isins.setdefault(symbol, set()).add(row.isin)
            series.setdefault(symbol, set()).add(row.series)
    return {
        symbol: SymbolLife(
            symbol=symbol,
            first_session=first[symbol],
            last_session=last[symbol],
            sessions=count[symbol],
            isins=tuple(sorted(isins.get(symbol, ()))),
            series_seen=tuple(sorted(series.get(symbol, ()))),
        )
        for symbol in sorted(first)
    }


def detect_symbol_changes(lives: dict[str, SymbolLife]) -> dict[str, str]:
    """``old symbol -> new symbol`` where one ISIN continues under a new ticker."""
    by_isin: dict[str, list[SymbolLife]] = {}
    for life in lives.values():
        for isin in life.isins:
            by_isin.setdefault(isin, []).append(life)
    renames: dict[str, str] = {}
    for holders in by_isin.values():
        if len(holders) < 2:
            continue
        ordered = sorted(holders, key=lambda life: life.first_session)
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier.symbol == later.symbol:
                continue
            if later.first_session >= earlier.last_session:
                renames[earlier.symbol] = later.symbol
    return renames


def status_on(
    symbol: str,
    day: date,
    life: SymbolLife,
    traded_series: str | None,
    *,
    renames: dict[str, str],
    stop_actions: dict[str, str],
) -> str:
    """The §2 date-aware status, using only facts the archive supports."""
    stamp = day.isoformat()
    if stamp < life.first_session:
        return NOT_YET_IN_ARCHIVE
    if stamp > life.last_session:
        if symbol in renames:
            return STOPPED_SYMBOL_CHANGE
        reason = stop_actions.get(symbol)
        if reason:
            return STOPPED_MERGER_OR_SCHEME
        return STOPPED_TRADING_REASON_UNKNOWN
    if traded_series is None:
        return NOT_TRADED_THIS_SESSION
    if traded_series in ELIGIBLE_SERIES:
        return TRADING
    return TRADING_RESTRICTED_SERIES


def stop_reasons(
    lives: dict[str, SymbolLife], actions: list[CorporateAction]
) -> dict[str, str]:
    """Symbols whose disappearance a published action explains."""
    structural = (ACTION_MERGER, ACTION_DEMERGER, ACTION_SCHEME_SUSPENSION, ACTION_SYMBOL_CHANGE)
    by_symbol: dict[str, list[CorporateAction]] = {}
    for action in actions:
        if action.action_type in structural:
            by_symbol.setdefault(action.symbol, []).append(action)
    out: dict[str, str] = {}
    for symbol, life in lives.items():
        last = date.fromisoformat(life.last_session)
        for action in by_symbol.get(symbol, []):
            delta = (action.ex_date - last).days
            if -STOP_REASON_WINDOW_DAYS <= delta <= STOP_REASON_WINDOW_DAYS:
                out[symbol] = action.action_type
                break
    return out


# ------------------------------------------------------------- §5/§12 screen

@dataclass(frozen=True)
class Eligibility:
    as_of: str
    label: str
    eligible: tuple[str, ...]
    considered: int
    rejected_price: int
    rejected_turnover: int
    rejected_history: int
    rejected_series: int
    rejected_rank: int

    def as_dict(self) -> dict:
        out = asdict(self)
        out["eligible"] = list(self.eligible)
        return out


def eligible_on(
    as_of: date,
    prior_sessions: dict[str, list[RawRow]],
    *,
    lookback: int = UNIVERSE_LOOKBACK_SESSIONS,
    min_turnover: float = UNIVERSE_MIN_MEDIAN_TURNOVER_INR,
    min_price: float = UNIVERSE_MIN_PRICE_INR,
    min_history: int = UNIVERSE_MIN_PRIOR_SESSIONS,
    top_n: int = UNIVERSE_TOP_N_BY_TURNOVER,
) -> Eligibility:
    """The declared universe for ``as_of``, from strictly earlier sessions.

    ``prior_sessions`` maps session date to rows and must contain no date on or
    after ``as_of``; that is asserted rather than assumed, because a screen that
    can see the decision day is a look-ahead leak that no downstream correction
    detects.
    """
    stamp = as_of.isoformat()
    for day in prior_sessions:
        if day >= stamp:
            raise ValueError(f"look-ahead: session {day} is not strictly before {stamp}")

    per_symbol: dict[str, list[RawRow]] = {}
    for day in sorted(prior_sessions):
        for row in prior_sessions[day]:
            per_symbol.setdefault(row.symbol, []).append(row)

    rejected_price = rejected_turnover = rejected_history = rejected_series = 0
    scored: list[tuple[float, str]] = []
    for symbol, rows in per_symbol.items():
        rows.sort(key=lambda r: r.trade_date)
        if len(rows) < min_history:
            rejected_history += 1
            continue
        window = rows[-lookback:]
        if window[-1].series not in ELIGIBLE_SERIES:
            rejected_series += 1
            continue
        if window[-1].close < min_price:
            rejected_price += 1
            continue
        turnover = median(row.turnover_inr for row in window)
        if turnover < min_turnover:
            rejected_turnover += 1
            continue
        scored.append((turnover, symbol))

    scored.sort(key=lambda item: (-item[0], item[1]))
    kept = [symbol for _, symbol in scored[:top_n]]
    return Eligibility(
        as_of=stamp,
        label=UNIVERSE_LABEL,
        eligible=tuple(kept),
        considered=len(per_symbol),
        rejected_price=rejected_price,
        rejected_turnover=rejected_turnover,
        rejected_history=rejected_history,
        rejected_series=rejected_series,
        rejected_rank=max(0, len(scored) - len(kept)),
    )
