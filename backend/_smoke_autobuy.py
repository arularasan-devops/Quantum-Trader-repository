"""Smoke: the bot still takes paper entries once the opportunity gate is in.

A gate that never lets anything through is indistinguishable from a broken bot,
so this asserts BOTH directions: a lively contract is still bought, and a dead
one is refused WITH a reason the user can read on the board.

The BUY decision is built directly rather than waited for on the simulated
feed — the frozen engine legitimately produces no BUY for long stretches, so
waiting for one would make this test a coin toss rather than a check.
"""
import os
import tempfile

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())

from app.config import settings  # noqa: E402
from app.models import Decision, OptionType, Signal  # noqa: E402
from app.state import AppState  # noqa: E402

FAIL = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(name)


def fresh() -> tuple[AppState, str, float]:
    """An engine warmed on the simulated feed, plus a real ATM contract from its
    own chain (a made-up symbol would be rejected before the gate under test)."""
    eng = AppState(instrument="CRUDEOIL")
    eng.tick()
    # The CHEAPEST contract still above the premium floor. Both floors then bite
    # from opposite sides deterministically: it clears the ₹20 rule, and its
    # premium is small enough that a normal ATR leg is comfortably worth more
    # than the move floor whatever the simulated feed's volatility seed is.
    ok = [
        o
        for o in eng.provider.option_chain()
        if o.premium >= settings.auto_trade_min_premium * 1.5
    ]
    pick = min(ok, key=lambda o: o.premium)
    return eng, pick.symbol, pick.premium


def buy_decision(symbol: str, premium: float) -> Decision:
    """A clean, high-confidence BUY — every gate except opportunity satisfied."""
    return Decision(
        signal=Signal.BUY,
        confidence=90.0,
        signal_strength=90.0,
        trade_quality="A",
        recommended_option=symbol,
        option_type=OptionType.CALL,
        current_premium=premium,
        spot_price=7900.0,
        stop_loss=premium * 0.92,
        target1=premium * 1.11,
        entry_trigger="MOMENTUM",
        premium_momentum=1.0,
        buy_trap_prob=0.0,
        premium_health="HEALTHY",
        reasons=["smoke"],
    )


settings.auto_trade_enabled = True
settings.auto_trade_min_confidence = 0.0
settings.opportunity_gate_enabled = True

# --- 1. A tradeable contract is still bought --------------------------------
eng, sym, prem = fresh()
eng._auto_trade(buy_decision(sym, prem), False, 0)
check(
    "the bot takes a paper entry with the opportunity gate ON",
    bool(eng.position.option_symbol),
    eng._auto_last_action or "no action recorded",
)

# --- 2. An impossible floor blocks it, and says why -------------------------
# The failure mode being guarded against is the silent one: no trade and no
# reason, which on the dashboard looks exactly like a dead bot.
settings.opportunity_min_move_pct = 500.0  # nothing moves 500% of premium
eng2, sym2, prem2 = fresh()
eng2._auto_trade(buy_decision(sym2, prem2), False, 0)
check("an impossible move floor blocks the entry", not eng2.position.option_symbol)
action = (eng2._auto_last_action or "").lower()
check(
    "the block is explained in plain language, not silent",
    "quiet" in action or "floor" in action or "score" in action,
    eng2._auto_last_action or "no action recorded",
)
settings.opportunity_min_move_pct = 4.0

# --- 3. The gate can be switched off ---------------------------------------
settings.opportunity_gate_enabled = False
settings.opportunity_min_move_pct = 500.0
eng3, sym3, prem3 = fresh()
eng3._auto_trade(buy_decision(sym3, prem3), False, 0)
check(
    "switching the gate off restores the old behaviour",
    bool(eng3.position.option_symbol),
    eng3._auto_last_action or "",
)
settings.opportunity_gate_enabled = True
settings.opportunity_min_move_pct = 4.0

# --- 4. Nothing was ever sent to a broker -----------------------------------
check("mode stayed paper throughout", settings.trade_mode == "paper", settings.trade_mode)

print()
print("ALL AUTO-BUY CHECKS PASSED" if not FAIL else f"FAILURES: {FAIL}")
raise SystemExit(1 if FAIL else 0)
