"""§4 — the derived adjusted series, and the audit that decides whether to trust it.

The raw store holds what the exchange printed. This module builds the research
series *from* it, and the build is a back-adjustment: prices before an ex-date are
multiplied by the cumulative factor of every quantified action after them, so the
most recent print always equals the raw print. Volume moves the other way (a 1:10
split multiplies share count, so pre-split volume is scaled up by ``1/factor``)
and turnover is left alone, because rupees traded are unaffected by a split.

The reason the adjustment is trustworthy here — and was not on the previous
source — is that it is *checkable against the raw prints*. A published factor is
compared with the observed ratio directly, whatever its size; the discontinuity
band only decides whether a boundary carrying *no* action needs explaining:

``EXPLAINED``
    a quantified action whose ratio matches the observed jump within tolerance.
``CA_RATIO_MISMATCH``
    an action exists but its published ratio disagrees with the prices. Adjusting
    on a wrong factor is worse than not adjusting, so the factor is applied *and*
    the break is recorded as unresolved, which stops level rules from spanning it.
``CA_UNQUANTIFIED``
    a rights issue, merger, demerger, capital reduction or scheme. The price moved
    for a reason the published text does not quantify, so no factor is invented.
``UNEXPLAINED_DISCONTINUITY``
    a jump with no action at all. Reported, never smoothed. It is either an
    exchange data problem or a genuine limit move, and a loader that quietly
    divided it away would be inventing a return.

The inverse check matters just as much and is what failed on the previous source:
a quantified action whose implied move is simply absent — both close and open
ratios flat across the ex-date — means the prices were already adjusted upstream.
That is reported as ``CA_WITHOUT_DISCONTINUITY``, and on a genuinely raw feed the
count should be near zero. It is deliberately distinct from
``CA_RATIO_MISMATCH``, where the prices did move but not by the published amount.

Each adjusted row therefore carries ``lookback_valid_from``: the date of the most
recent unresolved break in that symbol. Downstream §5/§8 level rules (20-day
high, 52-week high, previous-day low) must start their window at or after it. That
one field is what keeps a 1:10 split from silently rewriting five years of
"52-week high" for a name — the case that made the Angel series unusable.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, asdict
from datetime import date

from . import (
    CA_EX_DATE_MAX_CALENDAR_DAYS,
    CA_EX_DATE_SEARCH_SESSIONS,
    CA_RATIO_MISMATCH,
    CA_RATIO_TOLERANCE,
    CA_UNQUANTIFIED,
    DISCONTINUITY_RATIO_HIGH,
    DISCONTINUITY_RATIO_LOW,
    UNEXPLAINED_DISCONTINUITY,
    CorporateAction,
)
from .bhav import RawRow
from .corpact import combined_factor, index_by_symbol_date

EXPLAINED = "EXPLAINED"
CA_WITHOUT_DISCONTINUITY = "CA_WITHOUT_DISCONTINUITY"


@dataclass(frozen=True)
class Break:
    symbol: str
    on_date: str
    prev_close_raw: float
    close_raw: float
    observed_ratio: float
    observed_open_ratio: float
    expected_factor: float | None
    status: str
    action_types: tuple[str, ...]
    purposes: tuple[str, ...]
    matched_basis: str = ""
    published_ex_date: str = ""
    session_offset: int = 0

    @property
    def resolved(self) -> bool:
        return self.status == EXPLAINED

    def as_dict(self) -> dict:
        out = asdict(self)
        out["action_types"] = list(self.action_types)
        out["purposes"] = list(self.purposes)
        return out


@dataclass(frozen=True)
class AdjustedRow:
    trade_date: str
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover_inr: float
    delivery_qty: int | None
    delivery_pct: float | None
    cum_factor: float
    lookback_valid_from: str
    raw_close: float

    def as_dict(self) -> dict:
        return asdict(self)


def observed_ratio(previous: RawRow, current: RawRow) -> float:
    """Session-to-session close ratio on the raw basis.

    ``prev_close`` from the current row is preferred as the denominator when it is
    published: it is the exchange's own statement of the basis the session opened
    from, and on an ex-date NSE carries it across *unadjusted* (959.40 before a
    1:10 split), which is exactly the property that makes the jump visible.
    """
    base = current.prev_close if current.prev_close > 0 else previous.close
    if base <= 0:
        return 1.0
    return current.close / base


def observed_open_ratio(previous: RawRow, current: RawRow) -> float:
    """The same ratio measured at the open, which is where an action takes effect.

    The close-to-close ratio carries the session's own move on top of the action:
    HNDFDS opened on its 1:5 ex-date at the split basis and then rallied, so the
    close-based ratio was 0.240 against a published 0.200 and looked like a wrong
    ratio. Measured open-to-prev-close it is 0.203. Both are reported; a match on
    either is enough to call the break explained, and a mismatch on both is a real
    disagreement between the action table and the prices.
    """
    base = current.prev_close if current.prev_close > 0 else previous.close
    if base <= 0 or current.open <= 0:
        return 1.0
    return current.open / base


def _as_date(text: str) -> date:
    return date.fromisoformat(text)


def is_discontinuity(ratio: float) -> bool:
    return ratio < DISCONTINUITY_RATIO_LOW or ratio > DISCONTINUITY_RATIO_HIGH


def ratio_candidates(previous: RawRow, current: RawRow) -> dict[str, float]:
    """Every basis the boundary can honestly be measured on.

    ``PREVCLOSE`` is usually carried across an ex-date unadjusted, which is what
    makes a split visible — but not always. SETFGOLD's 1:100 split printed a
    prior close of 4259.95 and an ex-date close of 42.35 while publishing a
    *already-divided* PREVCLOSE, so measured on that basis the boundary looked
    flat (0.988) and a perfectly correct action table looked like evidence of an
    upstream adjustment. The prior session's own close is therefore measured too,
    and the basis that matched is recorded on the break.
    """
    out: dict[str, float] = {}
    for label, base in (("PREVCLOSE", current.prev_close), ("PRIOR_CLOSE", previous.close)):
        if base <= 0:
            continue
        out[f"{label}_TO_CLOSE"] = current.close / base
        if current.open > 0:
            out[f"{label}_TO_OPEN"] = current.open / base
    return out


def _same_direction_jump(previous: RawRow, current: RawRow, factor: float) -> bool:
    """The boundary left the operating band on the same side as the published factor."""
    if not is_discontinuity(factor):
        return False
    ratios = ratio_candidates(previous, current)
    if not ratios:
        return False
    observed = min(ratios.values()) if factor < 1.0 else max(ratios.values())
    return is_discontinuity(observed) and (observed < 1.0) == (factor < 1.0)


def _unmoved(ratios: dict[str, float]) -> bool:
    """Every basis sat flat across the boundary, within the action tolerance."""
    return all(abs(value - 1.0) <= CA_RATIO_TOLERANCE for value in ratios.values())


def classify_break(
    symbol: str,
    previous: RawRow,
    current: RawRow,
    actions: list[CorporateAction],
    *,
    published_ex_date: str = "",
    session_offset: int = 0,
) -> Break | None:
    """Explain one session boundary, or return ``None`` if nothing happened."""
    ratio = observed_ratio(previous, current)
    open_ratio = observed_open_ratio(previous, current)
    ratios = ratio_candidates(previous, current)
    factor, unquantified = combined_factor(actions)
    types = tuple(sorted({action.action_type for action in actions}))
    purposes = tuple(sorted({action.purpose for action in actions}))
    jumped = is_discontinuity(ratio) or is_discontinuity(open_ratio)

    if factor is not None:
        # The published factor is checked against the prices on its own terms,
        # independently of the discontinuity band. A bonus 1:5 is a 16.7% move,
        # inside the band, so demanding a "jump" first made every small action
        # unexplainable by construction: PFC's 1:4 printed 0.801 against a
        # published 0.800 and was still reported as an action with no price
        # effect.
        basis, residual = "", None
        for label, value in ratios.items():
            candidate = abs(value / factor - 1.0)
            if residual is None or candidate < residual:
                basis, residual = label, candidate
        if residual is not None and residual <= CA_RATIO_TOLERANCE:
            status = CA_UNQUANTIFIED if unquantified else EXPLAINED
        elif abs(factor - 1.0) > CA_RATIO_TOLERANCE and _unmoved(ratios):
            # The action implies a move no basis shows at all: the series
            # arrived pre-adjusted. Never treated as harmless.
            status, basis = CA_WITHOUT_DISCONTINUITY, ""
        else:
            status, basis = CA_RATIO_MISMATCH, ""
        return Break(
            symbol, current.trade_date, previous.close, current.close,
            ratio, open_ratio, factor, status, types, purposes, basis,
            published_ex_date or current.trade_date, session_offset,
        )

    if not jumped:
        return None

    if unquantified or actions:
        return Break(
            symbol, current.trade_date, previous.close, current.close,
            ratio, open_ratio, None, CA_UNQUANTIFIED, types, purposes, "",
            published_ex_date or current.trade_date, session_offset,
        )

    return Break(
        symbol, current.trade_date, previous.close, current.close,
        ratio, open_ratio, None, UNEXPLAINED_DISCONTINUITY, types, purposes, "",
        "", 0,
    )


@dataclass(frozen=True)
class Alignment:
    """Which boundary each published action group was attached to, and what was not.

    ``assigned`` maps a boundary index ``i`` (the transition ``rows[i-1] -> rows[i]``)
    to the group that explains it. ``unmapped`` holds every group that could not be
    placed — a symbol suspended around its own ex-date, or two groups claiming the
    same boundary. Those are reported, never folded into a neighbouring factor.
    """

    assigned: dict[int, tuple[list[CorporateAction], str, int]]
    unmapped: list[tuple[str, str, str]]


def align_actions(
    symbol: str,
    rows: list[RawRow],
    action_index: dict[tuple[str, str], list[CorporateAction]],
) -> Alignment:
    """Attach each published action group to the boundary its prices moved on.

    The PR bulletin's ``EX_DT`` is an announcement field and does not always name
    the session the basis changed on, so a bounded search either side is allowed
    and the offset used is recorded. Two guards keep that from inventing history:
    the anchor session must itself be near the published date (a name suspended
    for two months has no ex-date boundary to find, and BCG's August action would
    otherwise have been pinned to an October session), and one boundary carries
    at most one group (merging two published groups multiplied their factors and
    turned HAL's 1:2 split into a fabricated 1:4).
    """
    dates = [row.trade_date for row in rows]
    assigned: dict[int, tuple[list[CorporateAction], str, int]] = {}
    claimed: dict[int, int] = {}
    unmapped: list[tuple[str, str, str]] = []
    groups = sorted(
        (on_date, actions)
        for (sym, on_date), actions in action_index.items()
        if sym == symbol and actions
    )
    for on_date, actions in groups:
        anchor = bisect_left(dates, on_date)
        if anchor < 1 or anchor >= len(rows):
            unmapped.append((on_date, "OUTSIDE_TRADED_RANGE", ""))
            continue
        gap = abs((_as_date(dates[anchor]) - _as_date(on_date)).days)
        if gap > CA_EX_DATE_MAX_CALENDAR_DAYS:
            unmapped.append((on_date, "NO_SESSION_NEAR_PUBLISHED_EX_DATE", dates[anchor]))
            continue
        factor, _ = combined_factor(actions)
        chosen = anchor
        if factor is not None:
            best: tuple[int, float, int] | None = None
            low = max(1, anchor - CA_EX_DATE_SEARCH_SESSIONS)
            high = min(len(rows) - 1, anchor + CA_EX_DATE_SEARCH_SESSIONS)
            for index in range(low, high + 1):
                ratios = ratio_candidates(rows[index - 1], rows[index])
                residual = min(
                    (abs(value / factor - 1.0) for value in ratios.values()),
                    default=None,
                )
                if residual is None or residual > CA_RATIO_TOLERANCE:
                    continue
                # Nearest matching session wins, closest ratio breaks the tie.
                candidate = (abs(index - anchor), residual, index)
                if best is None or candidate < best:
                    best = candidate
            if best is not None:
                chosen = best[2]
            else:
                # No session in the window agrees with the published ratio. If
                # exactly one *did* change basis in the same direction, that is
                # where the action landed and the disagreement is a real one
                # between the table and the prices (VSTIND's 10:1 bonus printed
                # 0.107 against a published 0.091 on huge volume). Naming that
                # session is honest; calling the action unmarked when the price
                # plainly moved is not.
                jumps = [
                    index
                    for index in range(low, high + 1)
                    if _same_direction_jump(rows[index - 1], rows[index], factor)
                ]
                if len(jumps) == 1:
                    chosen = jumps[0]
        offset = chosen - anchor
        held = claimed.get(chosen)
        if held is not None:
            loser = (on_date, "BOUNDARY_CLAIMED_BY_ANOTHER_ACTION", dates[chosen])
            if abs(offset) < held:
                previous = assigned[chosen]
                unmapped.append((previous[1], "BOUNDARY_CLAIMED_BY_ANOTHER_ACTION", dates[chosen]))
            else:
                unmapped.append(loser)
                continue
        assigned[chosen] = (list(actions), on_date, offset)
        claimed[chosen] = abs(offset)
    return Alignment(assigned, unmapped)


def find_breaks(
    symbol: str,
    rows: list[RawRow],
    action_index: dict[tuple[str, str], list[CorporateAction]],
    *,
    alignment: Alignment | None = None,
) -> list[Break]:
    aligned = (alignment or align_actions(symbol, rows, action_index)).assigned
    breaks: list[Break] = []
    for index in range(1, len(rows)):
        actions, published, offset = aligned.get(index, ([], "", 0))
        found = classify_break(
            symbol,
            rows[index - 1],
            rows[index],
            actions,
            published_ex_date=published,
            session_offset=offset,
        )
        if found is not None:
            breaks.append(found)
    return breaks


def _orphan_barriers(
    rows: list[RawRow],
    action_index: dict[tuple[str, str], list[CorporateAction]],
    alignment: Alignment,
) -> set[str]:
    """Barrier dates for unplaced actions that carry a real price factor.

    A 1:5 bonus is a 16.7% move, inside the discontinuity band, so an unplaced
    one leaves no break to flag and the series would carry a silent phantom
    -16.7% return. Every such action bars lookback from the first session on or
    after its published date instead.
    """
    dates = [row.trade_date for row in rows]
    symbol = rows[0].symbol
    out: set[str] = set()
    for published, _reason, nearest in alignment.unmapped:
        actions = action_index.get((symbol, published), [])
        if not any(item.factor is not None for item in actions):
            continue
        if nearest:
            out.add(nearest)
            continue
        position = bisect_left(dates, published)
        if 0 <= position < len(dates):
            out.add(dates[position])
    return out


def build_adjusted(
    symbol: str,
    rows: list[RawRow],
    action_index: dict[tuple[str, str], list[CorporateAction]],
) -> tuple[list[AdjustedRow], list[Break]]:
    """Back-adjust one symbol and return the series plus its break ledger."""
    if not rows:
        return [], []
    alignment = align_actions(symbol, rows, action_index)
    breaks = find_breaks(symbol, rows, action_index, alignment=alignment)
    factor_at = {
        item.on_date: item.expected_factor
        for item in breaks
        if item.expected_factor is not None and item.status == EXPLAINED
    }
    # A published factor that disagrees with the observed prices is not applied:
    # either number would be a guess. The raw boundary is left standing and the
    # barrier below stops any lookback window from crossing it.
    unresolved_dates = sorted(
        {item.on_date for item in breaks if not item.resolved}
        | _orphan_barriers(rows, action_index, alignment)
    )

    # Backwards walk: cum_factor for a row is the product of factors of every
    # ex-date strictly after it.
    cum: list[float] = [1.0] * len(rows)
    running = 1.0
    for position in range(len(rows) - 1, -1, -1):
        cum[position] = running
        factor = factor_at.get(rows[position].trade_date)
        if factor is not None:
            running *= factor

    out: list[AdjustedRow] = []
    for position, row in enumerate(rows):
        scale = cum[position]
        valid_from = ""
        for on_date in unresolved_dates:
            if on_date <= row.trade_date:
                valid_from = on_date
        out.append(
            AdjustedRow(
                trade_date=row.trade_date,
                symbol=symbol,
                open=row.open * scale,
                high=row.high * scale,
                low=row.low * scale,
                close=row.close * scale,
                volume=int(round(row.volume / scale)) if scale else row.volume,
                turnover_inr=row.turnover_inr,
                delivery_qty=row.delivery_qty,
                delivery_pct=row.delivery_pct,
                cum_factor=scale,
                lookback_valid_from=valid_from,
                raw_close=row.close,
            )
        )
    return out, breaks


def build_all(
    symbol_rows: dict[str, list[RawRow]], actions: list[CorporateAction]
) -> tuple[dict[str, list[AdjustedRow]], list[Break]]:
    index = index_by_symbol_date(actions)
    series: dict[str, list[AdjustedRow]] = {}
    ledger: list[Break] = []
    for symbol, rows in symbol_rows.items():
        adjusted, breaks = build_adjusted(symbol, rows, index)
        series[symbol] = adjusted
        ledger.extend(breaks)
    ledger.sort(key=lambda item: (item.on_date, item.symbol))
    return series, ledger


def unmapped_actions(
    symbol_rows: dict[str, list[RawRow]], actions: list[CorporateAction]
) -> list[dict]:
    """Published action groups that could not be attached to any boundary.

    These are the actions the loader refuses to guess at, and they are written to
    their own artefact: an action silently dropped is indistinguishable from an
    action that never happened, which is the difference between a clean series
    and a phantom -90% return.
    """
    index = index_by_symbol_date(actions)
    out: list[dict] = []
    for symbol, rows in symbol_rows.items():
        if not rows:
            continue
        for published, reason, nearest in align_actions(symbol, rows, index).unmapped:
            out.append(
                {
                    "symbol": symbol,
                    "published_ex_date": published,
                    "reason": reason,
                    "nearest_session": nearest,
                }
            )
    out.sort(key=lambda item: (item["published_ex_date"], item["symbol"]))
    return out


def lookback_allowed(row: AdjustedRow, window_start: date) -> bool:
    """§5/§8 guard: may a level rule at ``row`` look back to ``window_start``?"""
    if not row.lookback_valid_from:
        return True
    return window_start.isoformat() >= row.lookback_valid_from
