"""§7/§10/§19 — many candidates observing the same live session at once.

The historical screen runs on candles, where there is no book and therefore no
executable price. This stage is the opposite: it runs over the Phase 35 raw
observation store, where a bid and an ask were actually recorded at the instant
the decision was taken. Nothing here writes to that store — it is opened
read-only and only ever read.

Three properties are the whole point of the stage.

**One observation, many independent verdicts.** Every candidate in
:data:`SHADOW` sees the identical bar window and the identical quote row, and no
candidate can see another's verdict, position, or the current champion. That
independence is what makes the set a family of hypotheses that
multiplicity correction can be applied to. Let one candidate condition on
another and the set collapses into a single adaptive strategy fitted in flight,
with none of the evidence a frozen definition would have carried.

**Bars are built forward only.** Decision windows are assembled minute by
minute from observations as they are read, so at the moment a candidate is
asked, the most recent bar is the one in progress and there is no later bar in
existence to consult. This is a structural guarantee rather than a careful
habit: :class:`LiveBars` has no method that returns anything after its cursor.

**A missing quote is a missing quote.** The states are distinct and none of
them collapses into another:

``MEASURED``
    a two-sided quote existed at the decision instant.
``UNMEASURED``
    a quote row existed but one or both sides were absent. The row's own reason
    is carried through.
``CAPTURE_GAP``
    no quote row at all for the vehicle this candidate needs.
``STALE``
    a quote existed but was older than :data:`MAX_QUOTE_AGE_SEC` at the
    decision instant, so filling on it would be filling on a price that had
    already gone.

None of these is ``NO_TRADE``. A candidate that wanted to act and could not be
priced has an *unmeasured* opportunity, and folding that into "it stood aside"
would let a capture fault quietly improve or worsen a candidate's record. It is
counted separately and reported separately, and no P&L is ever attached to it.
"""
from __future__ import annotations

import datetime as dt
import json
import zoneinfo

import numpy as np

from app.research.opportunity import (
    CAPTURE_GAP,
    FUTURES,
    MEASURED,
    SCHEMA_VERSION,
    SHADOW,
    SHADOW_FILE,
    STALE,
    UNDERLYING,
    UNMEASURED,
)
from app.research.opportunity import mechanisms as mech
from app.research.opportunity import registry, store
from app.research.phase35 import store as p35store

_IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# A quote older than this at the decision instant is STALE rather than
# MEASURED. Deliberately tight: the decision is taken on a one-minute bar, and a
# quote from further back than a few seconds is not the price that was there.
MAX_QUOTE_AGE_SEC = 5.0

# How many minute bars of context a candidate may ask for. The longest lookback
# in the grid plus room for the ATR window.
MAX_LOOKBACK = 120

SIGNAL = "SHADOW_SIGNAL"
NO_SIGNAL = "SHADOW_NO_SIGNAL"
NOT_APPLICABLE = "SHADOW_NOT_APPLICABLE"
UNPRICED = "SHADOW_UNMEASURED"

# Why an observation produced nothing for a candidate that was otherwise able to
# look at it. Warming up is not a gap: the bars genuinely do not exist yet.
WARMING = "WINDOW_NOT_YET_FULL_AT_THIS_POINT_IN_THE_SESSION"
OUT_OF_SCOPE = "OBSERVATION_IS_NOT_IN_THIS_CANDIDATES_INSTRUMENT_SCOPE"
NO_PAIR = "THE_SECOND_LEG_HAD_NO_OBSERVATION_AT_THIS_SAME_INSTANT"


class LiveBars:
    """One-minute bars accumulated in the order observations arrive.

    There is intentionally no way to read past the cursor. A window is the last
    ``lookback`` completed-or-in-progress minutes and nothing else, so a
    mechanism cannot consult a bar that had not happened when the decision was
    taken even if someone later writes one that tries to.
    """

    __slots__ = ("_bars", "_cap")

    def __init__(self, cap: int = MAX_LOOKBACK * 4) -> None:
        # instrument -> list of [minute_ts, open, high, low, close, volume]
        self._bars: dict[str, list[list[float]]] = {}
        self._cap = int(cap)

    def observe(self, instrument: str, ts: float, price: float,
                volume: float = 0.0) -> None:
        """Fold one observed price into the bar for its minute."""
        if price is None or not np.isfinite(price) or price <= 0:
            return
        minute = int(float(ts) // 60 * 60)
        rows = self._bars.setdefault(str(instrument), [])
        if rows and int(rows[-1][0]) == minute:
            bar = rows[-1]
            bar[2] = max(bar[2], price)
            bar[3] = min(bar[3], price)
            bar[4] = price
            bar[5] += float(volume or 0.0)
            return
        if rows and minute < int(rows[-1][0]):
            # Out-of-order observation. Dropped rather than merged: rewriting a
            # closed bar would change a window a candidate has already been
            # asked about, which makes two runs over the same store disagree.
            return
        rows.append([float(minute), price, price, price, price,
                     float(volume or 0.0)])
        if len(rows) > self._cap:
            del rows[:-self._cap]

    def window(self, instrument: str, lookback: int) -> mech.Window | None:
        rows = self._bars.get(str(instrument)) or []
        if len(rows) < max(3, int(lookback)):
            return None
        part = rows[-int(lookback):]
        arr = np.asarray(part, dtype=float)
        return mech.Window(
            instrument=str(instrument),
            ts=arr[:, 0].astype(np.int64), open=arr[:, 1], high=arr[:, 2],
            low=arr[:, 3], close=arr[:, 4], volume=arr[:, 5],
        )

    def last_ts(self, instrument: str) -> int | None:
        rows = self._bars.get(str(instrument)) or []
        return int(rows[-1][0]) if rows else None

    def instruments(self) -> list[str]:
        return sorted(self._bars)


def _required_vehicle(candidate: dict) -> str:
    vehicles = candidate.get("vehicle_scope") or [FUTURES]
    for v in vehicles:
        if v != UNDERLYING:
            return str(v)
    return UNDERLYING


def quote_evidence(quote: dict | None, decision_ts: float) -> dict:
    """The evidence state of one quote at one instant, and why."""
    if quote is None:
        return {"state": CAPTURE_GAP,
                "reason": "NO_QUOTE_ROW_FOR_THIS_VEHICLE_AT_THIS_OBSERVATION",
                "bid": None, "ask": None, "age_sec": None}
    bid, ask = quote.get("bid"), quote.get("ask")
    age = None
    if quote.get("ts") is not None:
        age = float(decision_ts) - float(quote["ts"])
    if bid is None or ask is None or float(bid) <= 0 or float(ask) <= 0:
        return {"state": UNMEASURED,
                "reason": str(quote.get("reason")
                              or "QUOTE_ROW_PRESENT_BUT_A_SIDE_WAS_ABSENT"),
                "bid": bid, "ask": ask, "age_sec": age}
    if age is not None and age > MAX_QUOTE_AGE_SEC:
        return {"state": STALE,
                "reason": f"QUOTE_WAS_{age:.1f}S_OLD_AT_THE_DECISION_INSTANT",
                "bid": bid, "ask": ask, "age_sec": age}
    return {"state": MEASURED, "reason": None, "bid": float(bid),
            "ask": float(ask), "age_sec": age}


def evaluate(candidate: dict, obs: dict, bars: LiveBars,
             quotes_by_vehicle: dict[str, dict]) -> dict:
    """One candidate's verdict on one observation. Reads no other candidate.

    The signature is the guarantee: everything this function can see is the
    candidate, the observation, the shared bar window and the shared quote rows.
    There is no parameter through which another candidate's state, the
    champion, or any later price could reach it.
    """
    instrument = str(obs.get("instrument"))
    scope = [str(s) for s in (candidate.get("instrument_scope") or [])]
    base = {
        "candidate_id": candidate["candidate_id"],
        "candidate_name": candidate.get("candidate_name"),
        "definition_fingerprint": candidate.get("definition_fingerprint"),
        "mechanism_family": candidate.get("mechanism_family"),
        "obs_id": obs.get("obs_id"),
        "session": obs.get("session"),
        "decision_ts": float(obs.get("ts") or 0.0),
        "instrument": instrument,
        "schema_version": SCHEMA_VERSION,
    }
    base["shadow_id"] = store.digest([base["candidate_id"], base["obs_id"]])
    if not scope or instrument != scope[0]:
        return {**base, "verdict": NOT_APPLICABLE, "reason": OUT_OF_SCOPE,
                "evidence": None}

    entry = candidate.get("entry_definition") or {}
    spec = mech.GRID.get(str(entry.get("mechanism")))
    if spec is None:
        return {**base, "verdict": NOT_APPLICABLE,
                "reason": "UNKNOWN_MECHANISM", "evidence": None}
    params = dict(entry.get("params") or {})
    lookback = int(params.get("lookback") or 30)
    w = bars.window(instrument, lookback)
    if w is None:
        return {**base, "verdict": NOT_APPLICABLE, "reason": WARMING,
                "evidence": None}

    pair = entry.get("pair_with")
    if pair:
        w2 = bars.window(str(pair), lookback)
        same_minute = (bars.last_ts(str(pair)) == bars.last_ts(instrument))
        if w2 is None or not same_minute:
            # A relationship measured on two different minutes is not the
            # relationship. Refused rather than approximated.
            return {**base, "verdict": NOT_APPLICABLE, "reason": NO_PAIR,
                    "evidence": None}
        decision = spec["fn"](w, w2, **params)
    else:
        decision = spec["fn"](w, **params)

    if decision is None:
        return {**base, "verdict": NO_SIGNAL, "reason": None, "evidence": None}

    vehicle = _required_vehicle(candidate)
    quote = quotes_by_vehicle.get(vehicle)
    ev = quote_evidence(quote, base["decision_ts"])
    row = {
        **base,
        "direction": decision.direction,
        "why": decision.why,
        "vehicle": vehicle,
        # The exact contract, so the paper engine can follow this same
        # instrument forward rather than a different strike or expiry that
        # happened to be quoted later.
        "symbol": (quote or {}).get("symbol"),
        "expiry": (quote or {}).get("expiry"),
        "strike": (quote or {}).get("strike"),
        "evidence": ev["state"],
        "evidence_reason": ev["reason"],
        "bid": ev["bid"],
        "ask": ev["ask"],
        "quote_age_sec": ev["age_sec"],
        "decision_price": w.price,
        "atr": mech.true_range(w),
    }
    if ev["state"] == MEASURED:
        row["verdict"] = SIGNAL
    else:
        # Wanted to act, could not be priced. Explicitly NOT a stand-aside:
        # no P&L is attached and it is counted in its own column.
        row["verdict"] = UNPRICED
        row["not_a_no_trade"] = (
            "the candidate wanted to act and the book could not price it. "
            "This is an unmeasured opportunity, not a decision to stand aside."
        )
    return row


def _quotes(con, obs_id: str) -> dict[str, dict]:
    return {str(q.get("vehicle")): q for q in p35store.quotes(con, obs_id)}


def run(*, session: str | None = None, limit: int | None = None,
        instrument: str | None = None) -> dict:
    """Fan every SHADOW candidate across the stored observations, in order.

    Returns counts only; the rows themselves go to
    :data:`~app.research.opportunity.SHADOW_FILE` keyed by candidate and
    observation, so re-running over the same store converges instead of
    doubling.
    """
    cands = registry.by_status(SHADOW)
    started = dt.datetime.now(_IST).isoformat()
    out = {
        "candidates": len(cands),
        "observations_read": 0,
        "rows_written": 0,
        "rows_already_present": 0,
        "signals": 0,
        "unpriced": 0,
        "no_signal": 0,
        "not_applicable": 0,
        "evidence": {},
        "reasons": {},
        "by_candidate": {},
        "started_at": started,
        "schema_version": SCHEMA_VERSION,
    }
    if not cands:
        out["note"] = ("no candidate is in SHADOW. Run the historical screen "
                       "first; an empty shadow stage is not a failure.")
        return out

    bars = LiveBars()
    seen = store.keys(SHADOW_FILE, "shadow_id")
    con = p35store.connect()
    try:
        stream = p35store.iter_observations(con, instrument=instrument,
                                            limit=limit)
        for obs in stream:
            if session and str(obs.get("session")) != str(session):
                continue
            out["observations_read"] += 1
            quotes = _quotes(con, str(obs.get("obs_id")))
            # Bars are fed from the underlying recorded on the book, or the
            # observation context, before any candidate is asked — and from
            # nothing later than this observation.
            price = _observed_price(obs, quotes)
            if price is not None:
                bars.observe(str(obs.get("instrument")), float(obs.get("ts")),
                             price)
            for cand in cands:
                row = evaluate(cand, obs, bars, quotes)
                _tally(out, row)
                if store.append_unique(SHADOW_FILE, row, field="shadow_id",
                                       seen=seen):
                    out["rows_written"] += 1
                else:
                    out["rows_already_present"] += 1
    finally:
        con.close()
    out["finished_at"] = dt.datetime.now(_IST).isoformat()
    return out


def _observed_price(obs: dict, quotes: dict[str, dict]) -> float | None:
    """The underlying price at this observation, or None.

    Taken from the futures quote's recorded underlying first and its traded
    price second. Never from a bid/ask midpoint: a midpoint is not a price
    anybody can trade at, and using one here would put a fictional level into
    the very window the decision is made on.
    """
    fut = quotes.get(FUTURES)
    if fut:
        for key in ("underlying", "traded"):
            val = fut.get(key)
            if val is not None and float(val) > 0:
                return float(val)
    ctx = obs.get("context_json")
    if isinstance(ctx, str) and ctx:
        try:
            parsed = json.loads(ctx)
        except ValueError:
            return None
        for key in ("underlying", "spot", "price", "ltp"):
            val = parsed.get(key) if isinstance(parsed, dict) else None
            if val is not None and float(val) > 0:
                return float(val)
    return None


def _tally(out: dict, row: dict) -> None:
    verdict = row.get("verdict")
    if verdict == SIGNAL:
        out["signals"] += 1
    elif verdict == UNPRICED:
        out["unpriced"] += 1
    elif verdict == NO_SIGNAL:
        out["no_signal"] += 1
    else:
        out["not_applicable"] += 1
    ev = row.get("evidence")
    if ev:
        out["evidence"][ev] = out["evidence"].get(ev, 0) + 1
    reason = row.get("reason") or row.get("evidence_reason")
    if reason:
        out["reasons"][str(reason)] = out["reasons"].get(str(reason), 0) + 1
    per = out["by_candidate"].setdefault(
        row["candidate_id"], {"signals": 0, "unpriced": 0})
    if verdict == SIGNAL:
        per["signals"] += 1
    elif verdict == UNPRICED:
        per["unpriced"] += 1


def observations() -> list[dict]:
    return store.read(SHADOW_FILE)
