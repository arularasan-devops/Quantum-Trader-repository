"""Futures contract rollover — RESEARCH ONLY (Phase 12 §11).

Why this exists
---------------
The futures research engine names the contract it would trade, and the only
contract it was ever handed was the near month, because that is the contract the
price feed quotes. So in expiry week it refused itself: on 25 Aug, 51 of its 76
refusals were ``EXPIRY_TOO_CLOSE`` — the last week of every month produced no
futures research at all, which is exactly the sample an option-vs-futures
comparison is missing.

The fix is to roll the research plan, not to loosen the expiry check. Inside
``settings.futures_roll_min_days`` days to expiry this module walks the forward
chain and selects the next contract that is far enough out, and records what it
chose and why.

Two limits stated rather than hidden:

* the **prices remain the quoted contract's**. Angel One's feed and WebSocket
  subscription are built on the near month, and this module does not touch
  either; a rolled plan's levels are therefore computed from near-month candles
  and carry the near-to-next basis as an unmeasured error. Every rolled
  selection says so in ``price_basis`` and ``price_basis_note``, and no rolled
  plan may be promoted on those levels without a next-month feed.
* it selects on **expiry only**. The futures book is not recorded here, so
  "liquid" cannot be measured per contract; the chain's own ordering is used and
  the liquidity of the selected contract is reported UNAVAILABLE rather than
  assumed.

This module places no order, sizes nothing and is read by the research card and
the Phase 12 report. There is no futures order path anywhere in the app.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading

from app.config import settings

# reason_for_roll values
NOT_ROLLED = "NOT_ROLLED"
ROLLED_NEAR_EXPIRY = "ROLLED_NEAR_EXPIRY"
ROLL_UNAVAILABLE = "ROLL_UNAVAILABLE"
CHAIN_UNAVAILABLE = "CHAIN_UNAVAILABLE"
EXPIRY_UNKNOWN = "EXPIRY_UNKNOWN"
DISABLED = "ROLLOVER_RESEARCH_DISABLED"

QUOTED = "QUOTED_CONTRACT"
ROLLED_BASIS_NOTE = (
    "levels are computed from the quoted (near) contract's candles; the "
    "near-to-next basis is an unmeasured error on a rolled plan"
)

REPORT_JSON = "futures_rollover.json"

_LOCK = threading.Lock()
# instrument -> last recorded selection, so the report states one row per
# instrument instead of one per tick. Raw ticks are not a rollover event.
_latest: dict[str, dict] = {}
_rolls: dict[str, int] = {}
_observations: dict[str, int] = {}


def _days_to_expiry(expiry: str | None, now: float) -> int | None:
    if not expiry:
        return None
    try:
        day = _dt.date.fromisoformat(str(expiry))
    except ValueError:
        return None
    today = _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc).date()
    return (day - today).days


def _norm(contract: dict | None, now: float) -> dict | None:
    if not contract or not contract.get("symbol"):
        return None
    expiry = contract.get("expiry")
    return {
        "symbol": str(contract.get("symbol")),
        "expiry": str(expiry) if expiry else None,
        "days_to_expiry": _days_to_expiry(expiry, now),
        "lot_size": contract.get("lot_size"),
        "exchange": contract.get("exchange"),
    }


def select(instrument: str, quoted: dict | None, chain: list[dict] | None,
           now: float, *, min_days: int | None = None) -> dict:
    """Pick the contract a research plan should be written on.

    Never raises into the caller's tick: an unusable chain returns the quoted
    contract with the reason it could not roll.
    """
    floor = int(settings.futures_roll_min_days if min_days is None else min_days)
    q = _norm(quoted, now)
    candidates = [c for c in (_norm(x, now) for x in (chain or [])) if c]
    # Nearest expiry first. Contracts whose expiry the feed did not state cannot
    # be ordered and cannot be shown to be far enough out, so they are not roll
    # targets — they stay visible in ``candidates`` for the report.
    dated = sorted((c for c in candidates if c["days_to_expiry"] is not None),
                   key=lambda c: c["days_to_expiry"])

    out: dict = {
        "instrument": instrument,
        "min_days_to_expiry": floor,
        "quoted_contract": q["symbol"] if q else None,
        "quoted_expiry": q["expiry"] if q else None,
        "quoted_days_to_expiry": q["days_to_expiry"] if q else None,
        "selected_contract": q["symbol"] if q else None,
        "expiry": q["expiry"] if q else None,
        "days_to_expiry": q["days_to_expiry"] if q else None,
        "lot_size": q["lot_size"] if q else None,
        "exchange": q["exchange"] if q else None,
        "rolled": False,
        "reason_for_roll": NOT_ROLLED,
        "price_basis": QUOTED,
        "price_basis_note": "",
        "liquidity": "UNAVAILABLE",
        "chain_size": len(candidates),
        "candidates": [{"symbol": c["symbol"], "expiry": c["expiry"],
                        "days_to_expiry": c["days_to_expiry"]}
                       for c in dated[:6]],
        "research_only": True,
        "executable": False,
        "ts": int(now),
    }

    if not settings.futures_rollover_research:
        out["reason_for_roll"] = DISABLED
        return out
    if q is None:
        out["reason_for_roll"] = CHAIN_UNAVAILABLE if not dated else NOT_ROLLED
    if out["quoted_days_to_expiry"] is None and q is not None:
        out["reason_for_roll"] = EXPIRY_UNKNOWN
        return out
    if q is not None and out["quoted_days_to_expiry"] >= floor:
        return out

    # The quoted contract is inside the roll window (or unknown): take the
    # nearest contract that is not.
    nxt = next((c for c in dated
                if c["days_to_expiry"] >= floor
                and (q is None or c["symbol"] != q["symbol"])), None)
    if nxt is None:
        out["reason_for_roll"] = ROLL_UNAVAILABLE if dated else CHAIN_UNAVAILABLE
        return out

    out.update({
        "selected_contract": nxt["symbol"],
        "expiry": nxt["expiry"],
        "days_to_expiry": nxt["days_to_expiry"],
        "lot_size": nxt["lot_size"],
        "exchange": nxt["exchange"],
        "rolled": True,
        "reason_for_roll": ROLLED_NEAR_EXPIRY,
        "price_basis": out["quoted_contract"] or QUOTED,
        "price_basis_note": ROLLED_BASIS_NOTE,
    })
    return out


def record(selection: dict) -> None:
    """Remember the current selection for the research report (in-memory)."""
    inst = str(selection.get("instrument") or "")
    if not inst:
        return
    with _LOCK:
        _observations[inst] = _observations.get(inst, 0) + 1
        prev = _latest.get(inst)
        if selection.get("rolled") and (
            prev is None
            or prev.get("selected_contract") != selection.get("selected_contract")
        ):
            _rolls[inst] = _rolls.get(inst, 0) + 1
        _latest[inst] = selection


def report() -> dict:
    """Per-instrument rollover state, for ``futures_rollover.json``."""
    with _LOCK:
        rows = sorted(_latest.values(), key=lambda r: str(r.get("instrument")))
        rolls = dict(_rolls)
        obs = dict(_observations)
    rolled = [r for r in rows if r.get("rolled")]
    blocked = [r for r in rows
               if r.get("reason_for_roll") in (ROLL_UNAVAILABLE, CHAIN_UNAVAILABLE)]
    return {
        "min_days_to_expiry": int(settings.futures_roll_min_days),
        "enabled": bool(settings.futures_rollover_research),
        "instruments": len(rows),
        "observations": sum(obs.values()),
        "rolled_instruments": len(rolled),
        "roll_events": sum(rolls.values()),
        "unrollable_instruments": len(blocked),
        "price_basis_caveat": ROLLED_BASIS_NOTE,
        "liquidity_note": (
            "the futures book is not recorded, so per-contract liquidity is "
            "UNAVAILABLE and the chain's expiry ordering is used instead"
        ),
        "research_only": True,
        "executable": False,
        "rows": rows,
    }


def write_report() -> str:
    """Write ``futures_rollover.json`` under ``data_dir``; return its path."""
    os.makedirs(settings.data_dir, exist_ok=True)
    path = os.path.join(settings.data_dir, REPORT_JSON)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report(), fh, indent=2, default=str)
    os.replace(tmp, path)
    return path


def reset_for_tests() -> None:
    with _LOCK:
        _latest.clear()
        _rolls.clear()
        _observations.clear()
