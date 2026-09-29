"""Phase 34 harvest — pull real option premium candles while the contracts live.

Scope is deliberately narrow. Only strikes within ``STRIKE_WINDOW_PCT`` of the
underlying are collected, because those are the only ones a signal would ever take
and a full chain would cost tens of thousands of rate-limited calls for strikes
that never traded.

Read-only against the broker: the historical endpoint and the scrip master. No
order is ever placed, and nothing here is called by the live engine.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import time

import httpx

from app.backtest.angel_history import (
    SCRIP_MASTER_URL,
    _fetch_chunk,
    _parse_expiry,
)
from app.research.phase24 import data as p24data
from app.research.phase32 import universe as p32universe
from app.research.phase34 import (
    CHUNK_DAYS,
    MAX_DTE_DAYS,
    MAX_EMPTY_WINDOWS,
    MIN_CALL_INTERVAL_SEC,
    STRIKE_WINDOW_PCT,
)
from app.research.phase34 import store as p34store

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def scrip_option_rows(roots: tuple[str, ...]) -> list[dict]:
    """Every listed option contract for ``roots``, from today's scrip master."""
    want = {r.upper() for r in roots}
    scrip = httpx.get(SCRIP_MASTER_URL, timeout=180.0).json()
    out: list[dict] = []
    for r in scrip:
        root = (r.get("name") or "").upper()
        if root not in want:
            continue
        symbol = (r.get("symbol") or "").upper()
        if symbol.endswith("CE"):
            otype = "CE"
        elif symbol.endswith("PE"):
            otype = "PE"
        else:
            continue
        expiry = _parse_expiry(r.get("expiry", ""))
        if expiry is None:
            continue
        try:
            strike = float(r.get("strike") or 0.0) / 100.0
        except (TypeError, ValueError):
            continue
        if strike <= 0:
            continue
        try:
            lot = int(float(r.get("lotsize") or 0))
        except (TypeError, ValueError):
            lot = 0
        out.append(
            {
                "token": r.get("token"),
                "symbol": symbol,
                "root": root,
                "exchange": (r.get("exch_seg") or "").upper(),
                "option_type": otype,
                "strike": strike,
                "expiry": expiry.isoformat(),
                "lot_size": lot,
            }
        )
    return out


def snapshot_master(roots: tuple[str, ...], *, today: dt.date | None = None) -> int:
    """Register today's listed contracts so their tokens survive expiry."""
    rows = scrip_option_rows(roots)
    con = p34store.connect()
    try:
        return p34store.record_contracts(con, rows, today or dt.date.today())
    finally:
        con.close()


def reference_price(root: str) -> float | None:
    """A recent underlying price for ``root``, used only to centre the strike window.

    Taken from history already on disk; no live call, so the harvest works after
    the close. Returns ``None`` when the root has no stored series, in which case
    the caller must not guess a centre.
    """
    series = p24data.load_series(root.upper())
    if series is not None and len(series.close):
        return float(series.close[-1])
    short = p32universe.load_short_capture(root.upper())
    if short is not None and len(short.close):
        return float(short.close[-1])
    return None


def select_contracts(con: sqlite3.Connection, roots: tuple[str, ...],
                     centres: dict[str, float], *, today: dt.date) -> list[dict]:
    """Front contracts inside the strike window of their root's reference price.

    Expired contracts are kept: their token is still in the registry and their
    candles are exactly the history that cannot be re-listed later.
    """
    out: list[dict] = []
    for row in p34store.contract_rows(con, roots):
        centre = centres.get(row["root"])
        if not centre:
            continue
        if abs(row["strike"] - centre) / centre * 100.0 > STRIKE_WINDOW_PCT:
            continue
        dte = (dt.date.fromisoformat(row["expiry"]) - today).days
        if dte > MAX_DTE_DAYS:
            continue
        out.append(row)
    return out


def windows(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    out: list[tuple[dt.date, dt.date]] = []
    cur = start
    while cur <= end:
        stop = min(cur + dt.timedelta(days=CHUNK_DAYS - 1), end)
        out.append((cur, stop))
        cur = stop + dt.timedelta(days=1)
    return out


def harvest(smart, roots: tuple[str, ...], *, lookback_days: int = 365,
            today: dt.date | None = None, max_calls: int = 400,
            progress: bool = True) -> dict:
    """Download 1-minute premium candles for the in-window contracts.

    Resumable and bounded: ``max_calls`` caps one run so a session cannot be spent
    entirely inside the rate limiter, and every window already fetched is skipped,
    including windows that legitimately returned nothing.
    """
    day = today or dt.date.today()
    con = p34store.connect()
    calls = 0
    bars = 0
    skipped = 0
    try:
        centres = {}
        for root in roots:
            price = reference_price(root)
            if price:
                centres[root.upper()] = price
        picked = select_contracts(con, roots, centres, today=day)
        for row in picked:
            expiry = dt.date.fromisoformat(row["expiry"])
            start = day - dt.timedelta(days=lookback_days)
            end = min(expiry, day)
            if end < start:
                continue
            empties = 0
            # Newest window first: a contract's history ends at expiry and begins
            # whenever it first traded, so walking backwards finds the real start
            # and stops there instead of probing pre-listing dates.
            for win_start, win_end in reversed(windows(start, end)):
                if calls >= max_calls:
                    return {
                        "status": "CALL_BUDGET_REACHED",
                        "calls": calls, "bars": bars, "skipped": skipped,
                        "contracts_in_window": len(picked),
                        "centres": centres,
                    }
                if p34store.window_done(con, row["token"], win_start, win_end):
                    skipped += 1
                    continue
                params = {
                    "exchange": "NFO",
                    "symboltoken": row["token"],
                    "interval": "ONE_MINUTE",
                    "fromdate": win_start.strftime("%Y-%m-%d 09:15"),
                    "todate": win_end.strftime("%Y-%m-%d 15:30"),
                }
                got = _fetch_chunk(smart, params)
                calls += 1
                n = p34store.save_bars(con, row["token"], got)
                bars += n
                # A window still in progress is not final, so it is refetched on a
                # later run rather than frozen with a partial session in it.
                if win_end < day:
                    p34store.log_window(con, row["token"], win_start, win_end, n)
                if progress and n:
                    print(f"  {row['symbol']} {win_start}..{win_end}: +{n} bars",
                          flush=True)
                # Emptiness is decided by what the broker returned, not by what was
                # newly inserted: a re-fetched window that was already stored adds no
                # rows and must not be mistaken for a contract that never traded.
                empties = empties + 1 if not got else 0
                if empties >= MAX_EMPTY_WINDOWS:
                    break
                time.sleep(MIN_CALL_INTERVAL_SEC)
        return {
            "status": "COMPLETE",
            "calls": calls, "bars": bars, "skipped": skipped,
            "contracts_in_window": len(picked),
            "centres": centres,
        }
    finally:
        con.close()
