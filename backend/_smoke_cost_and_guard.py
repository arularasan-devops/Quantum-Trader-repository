"""Smoke: the four corrections made before the Pullback study.

Each assertion below is tied to a defect measured in the 4,807-row journal
export, not to a hypothetical:

1. brokerage was charged per LOT, so a 39-lot SENSEX leg carried Rs1,560 instead
   of Rs40 and 34 rows read as losses while the option price had risen;
2. not one fill recorded a bid or an ask, so every P&L in the book was
   mid-to-mid and a 5% spread turned a 62% win rate into 17%;
3. twelve rows were manual entries taken against an explicit WAIT or with the
   feed still warming, worth -Rs14,346 with 0 of 12 reaching T1;
4. 4,647 Flow rows sat in the same aggregates as the 156 tagged setup rows.
"""
from __future__ import annotations

from app.analysis import books, fill_book
from app.analysis import option_costs as oc
from app.config import settings
from app.execution import manual_guard
from app.models import Decision, OptionType, Signal

CHECKS = 0


def ok(cond: bool, msg: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(msg)
    print(f"OK  {msg}")


def plan(**over) -> Decision:
    base = dict(
        signal=Signal.BUY, confidence=71.0, signal_strength=64.0,
        trade_quality="A", market_signal=Signal.BUY,
        recommended_option="NIFTY24500CE", option_type=OptionType.CALL,
        current_premium=100.0, stop_loss=92.0, target1=112.0,
    )
    base.update(over)
    return Decision(**base)


def main() -> None:
    # --- 1. brokerage is per ORDER ---------------------------------------
    one = oc.charges(6.3, 6.5, 10)
    many = oc.charges(6.3, 6.5, 390)
    ok(one.brokerage == many.brokerage,
       "brokerage does not change with the number of lots on the order")
    ok(one.brokerage == 2.0 * settings.brokerage_per_lot,
       "a round trip is exactly two orders of brokerage")
    ok(many.statutory > one.statutory,
       "statutory charges scale with turnover even though brokerage does not")
    ok(abs(many.total - (many.brokerage + many.statutory)) < 0.01,
       "brokerage and statutory charges add up to the total")
    ok(many.entry_statutory > 0 and many.exit_statutory > many.entry_statutory,
       "entry and exit are charged separately, and STT falls on the sell side")
    ok(many.model == oc.COST_MODEL and oc.COST_MODEL_OLD != oc.COST_MODEL,
       "a new row names its cost model, distinct from the old one")
    # the SENSEX row that read -Rs1,404 while the option rose 6.3 -> 6.5
    gross = (6.5 - 6.3) * 390
    ok(gross - many.total > 0,
       "the 39-lot SENSEX leg is a win under per-order brokerage, as the price says")

    # --- 2. the book is recorded, never invented --------------------------
    entry = fill_book.book(6.2, 6.4, 100)
    exit_ = fill_book.book(6.45, 6.55, 402)
    rec = fill_book.fill_record(entry, exit_, 390, entry_ts=100, exit_ts=402)
    ok(rec["cost_status"] == fill_book.MEASURED, "both books present reads MEASURED")
    ok(rec["entry_bid"] == 6.2 and rec["exit_ask"] == 6.55,
       "both sides of both books are recorded")
    ok(rec["spread_cost_rupees"] == round(((0.2 + 0.1) / 2) * 390, 2),
       "spread cost is half of each spread, on the real quantity")
    blank = fill_book.fill_record(None, None, 390)
    ok(blank["cost_status"] == fill_book.UNKNOWN,
       "no book means COST_STATUS UNKNOWN")
    ok(blank["spread_cost_rupees"] is None and blank["entry_spread"] is None,
       "an unknown spread stays empty and is never modelled")
    half = fill_book.fill_record(entry, None, 390)
    ok(half["cost_status"] == fill_book.ENTRY_ONLY
       and half["spread_cost_rupees"] is None,
       "one book is not enough to state a round-trip spread cost")
    ok(fill_book.book(6.5, 6.2, 1) is None and fill_book.book(0, 0, 1) is None,
       "a crossed or empty book is rejected rather than recorded")
    ok(fill_book.quality(100, 400)[0] == fill_book.STALE,
       "a book minutes away from the fill is labelled stale, not used silently")

    # --- 3. manual entries the journal shows were mistakes ----------------
    ok(manual_guard.refusal(plan(), True) is None,
       "a real BUY with a valid plan on a live feed is allowed through")
    for label, decision, ready in (
        ("WAIT", plan(signal=Signal.WAIT, market_signal=Signal.WAIT), True),
        ("AVOID", plan(market_signal=Signal.AVOID), True),
        ("feed not ready", plan(), False),
        ("warming data", plan(confidence=0.0, signal_strength=0.0), True),
        ("no plan", plan(current_premium=None), True),
        ("no stop", plan(stop_loss=None), True),
        ("stop above entry", plan(stop_loss=140.0), True),
        ("target below entry", plan(target1=90.0), True),
    ):
        why = manual_guard.refusal(decision, ready)
        ok(bool(why), f"manual entry is refused on {label}")
        ok(len(str(why)) > 30, f"the {label} refusal states its reason in words")
    ok(manual_guard.refusal(None, True) is not None,
       "no decision at all is not an entry opportunity")

    # --- 4. Flow keeps its own book ---------------------------------------
    ok(books.classify({"book": books.FLOW}, None, None) == books.FLOW,
       "a row tagged at the source is Flow")
    ok(books.classify({}, None, {"engine": "flow"}) == books.FLOW,
       "a row whose context names the flow engine is Flow")
    ok(books.classify({}, "Candle-Flow PAPER - flow leg", None) == books.FLOW,
       "rows already on disk are recognised by their note")
    ok(books.classify({}, "Manual - signal-board buy", {"entry_trigger": "PULLBACK"})
       == books.SIGNAL,
       "a setup row is not Flow")

    # --- 5. the same four corrections on a real round trip ----------------
    # The unit checks above prove the parts; a row is only trustworthy if the
    # closed trade actually carries them, which is what the clean session
    # checklist is looking at.
    from app.state import AppState

    # The account trade cap is shared with whatever ran before this smoke; the
    # round trip below is not testing that cap.
    from app.engine import account_risk
    account_risk.account.reset()

    st = AppState("NIFTY")
    st.tick()
    dec = st._last_decision
    sym = dec.recommended_option if dec else None
    ok(bool(sym), "a session produces a recommended contract to test against")
    refused = st.buy(str(sym), lots=2)
    if dec and dec.signal == Signal.BUY and refused.ok:
        pass  # a genuine BUY is allowed through; covered by the guard checks
    else:
        ok(not refused.ok and len(refused.message) > 30,
           "a manual entry the engine is not calling states why it was refused")
    res = st.buy(str(sym), lots=2, manual=False)
    # The auto path skips the manual refusals entirely. It can still be stopped
    # by the risk limits that were always there (capital, daily loss), which is
    # not what is under test — so what is asserted is that no refusal it gets is
    # a manual-guard one.
    ok(res.ok or manual_guard.refusal(dec, True) is None
       or res.message != manual_guard.refusal(dec, st._feed_ready()),
       "the automatic path is not subject to the manual-entry refusals")
    if not res.ok:
        print(f"    (auto entry declined for another reason: {res.message})")
        res = st.buy(str(sym), lots=1, confirm=False, manual=False)
    # The simulated feed deliberately carries no book, so the quote used here is
    # the session's own quote with a book attached — the shape a real feed sends.
    live_q = next(x for x in st.provider.option_chain() if x.symbol == sym)
    quoted = live_q.model_copy(update={"bid": 99.5, "ask": 100.5})
    st._entry_book = fill_book.book(quoted.bid, quoted.ask, 1000)
    st._mark_position(101.0, quoted)
    ok(st._exit_book is not None and st._exit_book["bid"] == 99.5,
       "a quote carrying a book leaves the exit book recorded on the leg")
    st.sell(confirm=True)
    row = st.journal[-1] if st.journal else {}
    ok(row.get("cost_model") == oc.COST_MODEL,
       "the closed row names the per-order cost model")
    ok(row.get("brokerage") == 2.0 * settings.brokerage_per_lot,
       "the closed row carries two orders of brokerage, not two per lot")
    ok(float(row.get("statutory") or 0) > 0 and "total" in row,
       "the closed row itemises statutory charges beside the total")
    ok(row.get("cost_status") == fill_book.MEASURED
       and row.get("spread_cost_rupees") is not None,
       "a leg with both books recorded reports a measured spread cost")

    print(f"\nALL {CHECKS} CHECKS PASSED")


if __name__ == "__main__":
    main()
