"""Smoke checks for plan correctness and stale-plan suppression.

Each check corresponds to a defect measured in the 24-Aug session data:
  1. 155 of 206 shadow entries carried a first target at or below the entry
     premium, because the signal is held stable across ticks while the premium
     it was priced against keeps moving (ICICIBANK25AUG261420PE: called BUY 150
     times with target1 frozen at 10.3 while the premium walked 9.7 -> 13.7);
  2. those plans resolved as instant "T3" wins in a median of 0.1 minutes;
  3. one row (SBIN25AUG261030PE, stop above entry) recorded realized_r = 1e8
     because the risk denominator was floored at 1e-9 instead of refused.
"""
from __future__ import annotations

import json
import os
import tempfile
import time

_TMP = tempfile.mkdtemp()
os.environ["QT_DATA_DIR"] = _TMP

from app.analysis import plan_validity  # noqa: E402
from app.analysis import signal_journal as sj  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import (  # noqa: E402
    Candle,
    Decision,
    IndicatorSnapshot,
    MarketStatus,
    OptionQuote,
    OptionType,
    Signal,
)
from app.state import AppState  # noqa: E402

settings.data_dir = _TMP
settings.signal_journal_log = True
settings.exec_funnel_log = True

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not ok:
        _failures.append(label)


def _rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def _plan_blocks() -> list[dict]:
    return [r for r in _rows(os.path.join(_TMP, "exec_funnel.jsonl"))
            if str(r.get("primary_blocker", "")).startswith("PLAN_")]


# --- 1. one shared definition of an unenterable plan ------------------------
check(
    "target at the premium is no upside",
    plan_validity.unusable(13.7, 9.0, 13.7) == plan_validity.NO_UPSIDE,
)
check(
    "the ICICIBANK case is named, not traded",
    plan_validity.unusable(13.7, 9.0, 10.3) == plan_validity.NO_UPSIDE,
)
check(
    "the SBIN case (stop above entry) is named",
    plan_validity.unusable(3.0, 3.3, 6.0) == plan_validity.STOP_ABOVE_ENTRY,
)
check(
    "a missing level is reported as missing, not as contradiction",
    plan_validity.unusable(13.7, None, 20.0) == plan_validity.NO_LEVELS,
)
check(
    "a plan with room above and a stop below is usable",
    plan_validity.unusable(10.0, 8.0, 14.0) is None,
)
check(
    "every reason has plain-language text",
    all(plan_validity.describe(r) not in (None, r)
        for r in (plan_validity.NO_LEVELS, plan_validity.NO_UPSIDE,
                  plan_validity.STOP_ABOVE_ENTRY)),
)


def _buy(premium: float, stop: float | None, t1: float | None,
         symbol: str = "TESTOPT25AUG100CE") -> Decision:
    return Decision(
        signal=Signal.BUY, market_signal=Signal.BUY, confidence=80.0,
        signal_strength=70.0, trade_quality="GOOD",
        reasons=["engine reason"], recommended_option=symbol,
        strike=100.0, option_type=OptionType.CALL,
        current_premium=premium, spot_price=100.0,
        stop_loss=stop, target1=t1,
        target2=(t1 + 2 if t1 is not None else None),
        target3=(t1 + 4 if t1 is not None else None),
        entry_range=(premium - 0.2, premium + 0.2),
    )


# --- 2. a held plan whose premium has walked past it stops printing BUY -----
st = AppState(instrument="CRUDEOIL")
now = time.time()
st._committed = _buy(10.0, 8.0, 14.0)
st._committed_at = now
st._plan_at = now
st._plan_version = 1
fresh = st._decorate(now + 5)
check(
    "a usable held plan is FRESH and actionable and stays a BUY",
    fresh.plan_state == "FRESH" and fresh.plan_actionable
    and fresh.signal == Signal.BUY,
    f"{fresh.plan_state} / {fresh.signal.value}",
)
check("plan age is stamped", fresh.plan_age_seconds == 5)
check("plan version is stamped", fresh.plan_version == 1)
check("the premium the levels were priced from is stamped",
      fresh.plan_premium == 10.0)

# the premium walks above the frozen target: the ICICIBANK defect
st._committed.current_premium = 15.0
stale = st._decorate(now + 600)
check(
    "a BUY whose target is no longer above the premium is not printed as BUY",
    stale.signal == Signal.WAIT,
    stale.signal.value,
)
check(
    "the market bias is downgraded with it, so nothing downstream reads BUY",
    stale.market_signal == Signal.WAIT,
    stale.market_signal.value if stale.market_signal else "None",
)
check(
    "it is marked unactionable with the reason named",
    not stale.plan_actionable
    and stale.plan_invalid_reason == plan_validity.NO_UPSIDE,
    f"{stale.plan_state} / {stale.plan_invalid_reason}",
)
check(
    "the levels are still shown rather than deleted",
    stale.target1 == 14.0 and stale.stop_loss == 8.0,
)
check(
    "the suppression is stated as the leading reason to the user",
    bool(stale.reasons) and "WAIT" in stale.reasons[0]
    and "BUY" in stale.reasons[0],
    stale.reasons[0] if stale.reasons else "",
)

# --- 3. the funnel records the suppression once, not once a tick ------------
check(
    "the suppressed plan is written to the funnel so it cannot disappear",
    len(_plan_blocks()) == 1,
    f"{len(_plan_blocks())} row(s)",
)
st._decorate(now + 660)
st._decorate(now + 720)
check(
    "repeating the same suppressed plan does not repeat the funnel row",
    len(_plan_blocks()) == 1,
    f"{len(_plan_blocks())} row(s)",
)

# --- 4. a same-leg plan that becomes valid again is adopted, version bumped -
refreshed = st._refresh(_buy(15.0, 13.0, 19.0), now + 900)
check(
    "a newly valid plan for the same leg replaces the stale levels",
    refreshed.target1 == 19.0 and refreshed.stop_loss == 13.0,
    f"stop {refreshed.stop_loss} t1 {refreshed.target1}",
)
check(
    "the refresh is visible as a new plan version",
    refreshed.plan_version == 2 and refreshed.plan_state == "REFRESHED",
    f"v{refreshed.plan_version} / {refreshed.plan_state}",
)
check(
    "the refreshed plan is actionable again",
    refreshed.plan_actionable,
)
check("plan age restarts with the new levels", refreshed.plan_age_seconds == 0)

# --- 5. a plan for a DIFFERENT leg is never grafted onto the held signal ----
st2 = AppState(instrument="CRUDEOIL")
st2._committed = _buy(15.0, 8.0, 14.0)   # stale
st2._committed_at = st2._plan_at = now
kept = st2._refresh(_buy(40.0, 35.0, 55.0, symbol="OTHEROPT25AUG200CE"), now + 60)
check(
    "levels from another contract are not adopted",
    kept.target1 == 14.0 and kept.stop_loss == 8.0 and kept.plan_version == 1,
    f"stop {kept.stop_loss} t1 {kept.target1} v{kept.plan_version}",
)

# --- 6. a WAIT with no plan is not called invalid ---------------------------
st3 = AppState(instrument="CRUDEOIL")
st3._committed = Decision(signal=Signal.WAIT, market_signal=Signal.WAIT,
                          confidence=20.0, signal_strength=10.0,
                          trade_quality="POOR", reasons=["no setup"])
st3._committed_at = st3._plan_at = now
marked = st3._decorate(now + 10)
check(
    "a WAIT that offers no trade reports NO_PLAN, not INVALID",
    marked.plan_state == "NO_PLAN" and marked.plan_invalid_reason is None,
    marked.plan_state,
)

# --- 7. the journal refuses to follow an unenterable plan -------------------
_IND = IndicatorSnapshot(atr=1.0, vwap=100.0, rsi=60.0, adx=25.0, trend="UP")
_CANDLES = [Candle(time=1000 + 60 * i, open=99.0, high=101.0, low=98.0,
                   close=100.0, volume=1000) for i in range(30)]


def _quote(symbol: str, prem: float) -> OptionQuote:
    return OptionQuote(symbol=symbol, strike=100.0, option_type=OptionType.CALL,
                       premium=prem, iv=0.2, delta=0.5, gamma=0.01, theta=-1.0,
                       vega=2.0, oi=10000, oi_change=100, volume=5000,
                       bid=prem - 0.1, ask=prem + 0.1)


def _journal(instrument: str, dec: Decision, ts: float) -> None:
    sj._maybe_journal(
        instrument, dec,
        [_quote(dec.recommended_option or "X", dec.current_premium or 1.0)],
        _IND, 100.0, MarketStatus.TRENDING, _CANDLES, ts,
        "2026-08-27", 900,
    )


sj.reset_for_tests()
T0 = 1_800_000_000.0
_journal("ICICIBANK", _buy(13.7, 9.0, 10.3), T0)   # the ICICIBANK defect
check(
    "an unenterable plan opens no tracked outcome (no instant fake T3)",
    not sj._open,
    f"{len(sj._open)} open",
)
rows = _rows(sj.journal_path())
check(
    "the call is still recorded, with the defect named",
    bool(rows) and rows[-1]["followed"] is False
    and rows[-1]["not_followed_reason"] == plan_validity.NO_UPSIDE
    and rows[-1]["outcome"] == sj.NO_ENTRY,
    str(rows[-1].get("not_followed_reason")) if rows else "no rows",
)

sj.reset_for_tests()
_journal("CRUDEOIL", _buy(10.0, 8.0, 14.0), T0)
check(
    "a usable plan is still followed",
    len(sj._open) == 1,
    f"{len(sj._open)} open",
)
state = next(iter(sj._open.values()))
check(
    "the tracked risk is the real distance to the stop, never an epsilon",
    abs(state["risk"] - 2.0) < 1e-9,
    str(state["risk"]),
)

# --- 8. no R is ever emitted from a non-positive risk -----------------------
sid, opened = next(iter(sj._open.items()))
opened["risk"] = 0.0                  # the SBIN row's condition
sj._resolve(sid, opened, 12.0, T0 + 600, sj.TIMEOUT)
resolved = [r for r in _rows(sj.outcomes_path()) if r.get("event") == "RESOLVED"]
last = resolved[-1] if resolved else {}
check(
    "zero risk yields no realized R at all",
    bool(last) and last.get("realized_r") is None,
    str(last.get("realized_r")),
)
check(
    "and says why R is unavailable",
    last.get("r_unavailable_reason") == "NON_POSITIVE_RISK",
    str(last.get("r_unavailable_reason")),
)
check(
    "the derived R figures are withheld too, not computed from nothing",
    last.get("mfe_r") is None and last.get("mae_r") is None
    and last.get("mfe_capture") is None and last.get("give_back_r") is None,
)

sj.reset_for_tests()
_journal("CRUDEOIL", _buy(10.0, 8.0, 14.0), T0)
sid, opened = next(iter(sj._open.items()))
sj._resolve(sid, opened, 12.0, T0 + 600, sj.TIMEOUT)
resolved = [r for r in _rows(sj.outcomes_path()) if r.get("event") == "RESOLVED"]
check(
    "a real risk still produces R, unchanged",
    resolved[-1]["realized_r"] == 1.0
    and resolved[-1]["r_unavailable_reason"] is None,
    str(resolved[-1]["realized_r"]),
)

# --- 9. an open position is never downgraded out from under the user --------
st4 = AppState(instrument="CRUDEOIL")
st4._committed = _buy(15.0, 8.0, 14.0)   # stale plan
st4._committed_at = st4._plan_at = now
st4.position.option_symbol = "TESTOPT25AUG100CE"
st4.position.quantity_lots = 1
held = st4._decorate(now + 30)
check(
    "a held position keeps its signal; suppression only affects fresh entries",
    held.signal == Signal.BUY and not held.plan_actionable,
    f"{held.signal.value} / actionable={held.plan_actionable}",
)

print()
if _failures:
    print(f"PLAN FRESHNESS SMOKE FAILED — {len(_failures)} check(s):")
    for f in _failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("PLAN FRESHNESS SMOKE PASSED")
